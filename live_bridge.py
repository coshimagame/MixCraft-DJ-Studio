"""Live-bridge logic for the Ableton companion (Phase: Layer 1, pure logic).

This module converts controller events into a *plan* of operations against
Ableton Live — a list of `LiveAction` — without importing or touching any
Live API (`_Framework`, `Live`, ...).  The future Remote Script that lives
inside Live is a thin adapter: it feeds MIDI through `midi_map.dispatch`,
hands the resulting command tuples to this module, and executes the
returned actions against the real Live objects.  Keeping the conversion
here means every rule below is unit-testable outside Live
(see tests/test_live_bridge.py).

Input contract — exactly what `midi_map.dispatch` returns:

- CC message    -> (action, deck, value / 127.0)   value is a float 0.0-1.0
- Note-on       -> (action, deck, True)
- Note-off      -> None (dispatch drops it)
- Unmapped      -> None
- deck is 0 (Deck A), 1 (Deck B) or None (global: crossfader / master)

Deck -> Live track: Deck A = Track 1, Deck B = Track 2 (the two deck
tracks of the companion template set).  Global actions have track=None.

Conversion rules (user decisions 2026-11-25, design doc section 5.4):

| dispatch action      | LiveAction                                            |
|----------------------|-------------------------------------------------------|
| volume (deck)        | set_parameter track.volume, value = v                 |
| gain   (deck, TRIM)  | set_parameter input.gain, value = v                   |
| eq_high/mid/low      | set_parameter eq_three.high/.mid/.low, value = v      |
| filter (deck)        | set_parameter filter.bipolar, value = (v - 0.5) * 2   |
| cross  (global)      | set_parameter mixer.crossfader, value = v (0=A, 1=B)  |
| master (global)      | set_parameter mixer.master_volume, value = v          |
| play   (deck)        | transport clip.toggle_play                            |
| cue_jump (deck)      | transport clip.cue_jump (back to clip start / cue)    |
| loop_in / loop_out   | transport clip.loop_in / clip.loop_out                |
| load   (deck)        | transport track.load (plan only; adapter may defer)   |
| hotcue_jump (deck)   | fire_clip for cues_to_clip_plan clip #cue_index       |
| jog    (deck)        | nudge clip.nudge, signed beats (see below)            |
| tempo  (deck)        | IGNORED — returns [] (set BPM is fixed)               |
| sync   (deck)        | IGNORED — returns [] (both decks warped to set BPM)   |
| anything else / None | []                                                    |

Value normalisation:

- Faders, TRIM, master, crossfader: identity — the dispatch float 0.0-1.0
  is already Live's normalised parameter range.  The adapter maps it onto
  the concrete device range of the template set.
- EQ bands: the normalised value is passed through, with **0.5 =
  neutral (0 dB)**.  The template set is expected to range-map EQ Three
  (or macro-mapped EQ Eight) so that the centre of the knob is flat;
  this module only guarantees the 0.5-is-neutral contract.
- FILTER is bipolar, matching the standalone MixCraft convention
  (left = lowpass, right = highpass): value = (v - 0.5) * 2, so
  -1.0 = fully lowpass, 0.0 = centre/open, +1.0 = fully highpass.
  The adapter drives an Auto Filter (frequency + LP/HP selection) or a
  two-filter template from this single signed value.

Jog -> nudge: DDJ-RB jog wheels send *relative* values, but dispatch
normalises every CC to raw / 127.0, so the raw byte is recovered as
round(v * 127).  The recovered tick count is raw if raw < 64 else
raw - 128 — and then NEGATED: the first hardware check (2026-11-26)
showed the nudge moved opposite to the wheel's rotation with the
original sign, so 0x01-0x3F now yields -1..-63 ticks and 0x7F-0x41
yields +1..+63.  One tick is NUDGE_BEATS_PER_TICK beats of temporary
position correction — a nudge, never a tempo change (the set BPM is
fixed).  delta == 0 yields [].

Hot Cue pads: `midi_map.dispatch` does not pass the pad index (a known
limitation shared by the APC40 and DDJ-RB profiles — every pad maps to
the same 'hotcue_jump' action), so the caller must supply `cue_index`
(the adapter knows which note arrived) plus the deck's clip plan built
by `library.cues_to_clip_plan`.  Without either, or with an out-of-range
index, the result is [] — firing the wrong clip is worse than none.
"""
from dataclasses import dataclass
from typing import Optional

# Deck index (midi_map convention) -> Live track number (template set).
DECK_TRACKS = {0: 1, 1: 2}

# One jog tick = this many beats of nudge.  Small on purpose: the jog is
# for nudging a warped clip back into phase, not for beat-jumping.
# 0.01 -> 0.005 on 2026-11-26: the user asked for a finer step.
NUDGE_BEATS_PER_TICK = 0.005

_EQ_TARGETS = {'eq_high': 'eq_three.high',
               'eq_mid': 'eq_three.mid',
               'eq_low': 'eq_three.low'}

_TRANSPORT_TARGETS = {'play': 'clip.toggle_play',
                      'cue_jump': 'clip.cue_jump',
                      'loop_in': 'clip.loop_in',
                      'loop_out': 'clip.loop_out',
                      'load': 'track.load'}

# Actions that intentionally produce no LiveAction (design doc 5.4).
_IGNORED_ACTIONS = {'tempo', 'sync'}


@dataclass(frozen=True)
class LiveAction:
    """One planned operation against Live.  Pure data, no Live objects.

    kind:   'set_parameter' | 'transport' | 'fire_clip' | 'nudge'
    track:  1-based Live track number, or None for global (mixer) targets
    target: dotted name of what to touch, e.g. 'eq_three.high'
    value:  normalised float for set_parameter, signed beats for nudge,
            True for transport / fire_clip
    clip_index / clip_name: set for fire_clip (index into the deck's
            cues_to_clip_plan clips list)
    """
    kind: str
    track: Optional[int]
    target: str
    value: object = None
    clip_index: Optional[int] = None
    clip_name: Optional[str] = None


def _clamp01(value):
    return min(1.0, max(0.0, float(value)))


def _jog_delta(normalised):
    """Recover the signed tick delta from dispatch's raw/127 float.

    The recovered count is negated — hardware check 2026-11-26: with
    the original sign the nudge ran opposite to the wheel.
    """
    raw = round(_clamp01(normalised) * 127)
    ticks = raw if raw < 64 else raw - 128
    return -ticks


def plan_event(action, deck, value, clip_plan=None, cue_index=None):
    """Convert one semantic controller event into LiveActions.

    `action`/`deck`/`value` follow the midi_map.dispatch contract above.
    `clip_plan` is a library.cues_to_clip_plan result for the deck's
    track; `cue_index` selects which hot-cue clip to fire.
    Returns a list (possibly empty) of LiveAction.
    """
    if action is None or action in _IGNORED_ACTIONS:
        return []
    track = DECK_TRACKS.get(deck)  # None for global actions / unknown deck

    if action in ('cross', 'master'):
        target = 'mixer.crossfader' if action == 'cross' else 'mixer.master_volume'
        return [LiveAction('set_parameter', None, target, _clamp01(value))]
    if track is None:
        return []  # deck action without a known deck: nowhere to send it

    if action == 'volume':
        return [LiveAction('set_parameter', track, 'track.volume', _clamp01(value))]
    if action == 'gain':
        return [LiveAction('set_parameter', track, 'input.gain', _clamp01(value))]
    if action in _EQ_TARGETS:
        return [LiveAction('set_parameter', track, _EQ_TARGETS[action], _clamp01(value))]
    if action == 'filter':
        bipolar = (_clamp01(value) - 0.5) * 2.0
        return [LiveAction('set_parameter', track, 'filter.bipolar', bipolar)]
    if action in _TRANSPORT_TARGETS:
        return [LiveAction('transport', track, _TRANSPORT_TARGETS[action], True)]
    if action == 'jog':
        delta = _jog_delta(value)
        if delta == 0:
            return []
        return [LiveAction('nudge', track, 'clip.nudge', delta * NUDGE_BEATS_PER_TICK)]
    if action == 'hotcue_jump':
        if clip_plan is None or cue_index is None:
            return []  # dispatch drops the pad index; see module docstring
        clips = clip_plan.get('clips', [])
        if not 0 <= cue_index < len(clips):
            return []
        clip = clips[cue_index]
        return [LiveAction('fire_clip', track, 'clip.fire', True,
                           clip_index=cue_index, clip_name=clip.get('name'))]
    return []


def plan_dispatch(command, clip_plan=None, cue_index=None):
    """Convert a midi_map.dispatch result (or None) into LiveActions."""
    if command is None:
        return []
    action, deck, value = command
    return plan_event(action, deck, value, clip_plan=clip_plan, cue_index=cue_index)


class LiveBridge:
    """Stateful convenience wrapper the Remote Script adapter can hold.

    Holds each deck's clip plan (built from library.cues_to_clip_plan)
    and the fixed set BPM.  The BPM is recorded for display/logging
    only — by design no event ever turns into a tempo action.
    """

    def __init__(self, clip_plans=None, set_bpm=None):
        self.clip_plans = clip_plans or {}  # deck index -> cues_to_clip_plan result
        self.set_bpm = set_bpm

    def handle(self, action, deck, value, cue_index=None):
        return plan_event(action, deck, value,
                          clip_plan=self.clip_plans.get(deck),
                          cue_index=cue_index)

    def handle_dispatch(self, command, cue_index=None):
        if command is None:
            return []
        return self.handle(command[0], command[1], command[2], cue_index=cue_index)
