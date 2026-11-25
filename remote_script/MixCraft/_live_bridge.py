# -*- coding: utf-8 -*-
"""Bundled Python 2.7 compatible copy of the repo-root ``live_bridge.py``.

Live 10 ships Python 2.7: no ``dataclasses``, no ``typing``-based class
syntax.  This copy keeps ``plan_event`` / ``plan_dispatch`` /
``LiveBridge`` with *identical conversion logic*; the only changes are:

- ``LiveAction`` is a plain class with ``__eq__``/``__repr__`` instead
  of a ``@dataclass``;
- type annotations and the ``typing`` import are dropped;
- Python 2 ``round()`` returns a float, so ``_jog_delta`` casts to
  ``int`` to keep the tick delta an integer as in the original.

The full design rationale (conversion table, fixed-BPM rule, jog
encoding assumption) lives in the root module's docstring — read it
there.  When the root module's rules change, mirror them here.
"""

# Deck index (midi_map convention) -> Live track number (template set).
DECK_TRACKS = {0: 1, 1: 2}

# One jog tick = this many beats of nudge.
# 0.01 -> 0.005 on 2026-11-26 (finer step, user request; mirror of root).
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


class LiveAction(object):
    """One planned operation against Live.  Pure data, no Live objects.

    kind:   'set_parameter' | 'transport' | 'fire_clip' | 'nudge'
    track:  1-based Live track number, or None for global (mixer) targets
    target: dotted name of what to touch, e.g. 'eq_three.high'
    value:  normalised float for set_parameter, signed beats for nudge,
            True for transport / fire_clip
    clip_index / clip_name: set for fire_clip (index into the deck's
            clip plan clips list)
    """

    def __init__(self, kind, track, target, value=None,
                 clip_index=None, clip_name=None):
        self.kind = kind
        self.track = track
        self.target = target
        self.value = value
        self.clip_index = clip_index
        self.clip_name = clip_name

    def __eq__(self, other):
        if not isinstance(other, LiveAction):
            return NotImplemented
        return (self.kind == other.kind and self.track == other.track
                and self.target == other.target
                and self.value == other.value
                and self.clip_index == other.clip_index
                and self.clip_name == other.clip_name)

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __repr__(self):
        return ('LiveAction(%r, %r, %r, %r, clip_index=%r, clip_name=%r)'
                % (self.kind, self.track, self.target, self.value,
                   self.clip_index, self.clip_name))


def _clamp01(value):
    return min(1.0, max(0.0, float(value)))


def _jog_delta(normalised):
    """Recover the signed tick delta from dispatch's raw/127 float.

    The recovered count is negated — hardware check 2026-11-26: with
    the original sign the nudge ran opposite to the wheel (mirror of
    the root module's fix).
    """
    raw = int(round(_clamp01(normalised) * 127))
    ticks = raw if raw < 64 else raw - 128
    return -ticks


def plan_event(action, deck, value, clip_plan=None, cue_index=None):
    """Convert one semantic controller event into LiveActions.

    `action`/`deck`/`value` follow the midi_map.dispatch contract.
    `clip_plan` is a library.cues_to_clip_plan result for the deck's
    track; `cue_index` selects which hot-cue clip to fire.
    Returns a list (possibly empty) of LiveAction.
    """
    if action is None or action in _IGNORED_ACTIONS:
        return []
    track = DECK_TRACKS.get(deck)  # None for global actions / unknown deck

    if action in ('cross', 'master'):
        target = ('mixer.crossfader' if action == 'cross'
                  else 'mixer.master_volume')
        return [LiveAction('set_parameter', None, target, _clamp01(value))]
    if track is None:
        return []  # deck action without a known deck: nowhere to send it

    if action == 'volume':
        return [LiveAction('set_parameter', track, 'track.volume',
                           _clamp01(value))]
    if action == 'gain':
        return [LiveAction('set_parameter', track, 'input.gain',
                           _clamp01(value))]
    if action in _EQ_TARGETS:
        return [LiveAction('set_parameter', track, _EQ_TARGETS[action],
                           _clamp01(value))]
    if action == 'filter':
        bipolar = (_clamp01(value) - 0.5) * 2.0
        return [LiveAction('set_parameter', track, 'filter.bipolar', bipolar)]
    if action in _TRANSPORT_TARGETS:
        return [LiveAction('transport', track, _TRANSPORT_TARGETS[action],
                           True)]
    if action == 'jog':
        delta = _jog_delta(value)
        if delta == 0:
            return []
        return [LiveAction('nudge', track, 'clip.nudge',
                           delta * NUDGE_BEATS_PER_TICK)]
    if action == 'hotcue_jump':
        if clip_plan is None or cue_index is None:
            return []  # dispatch drops the pad index; see root docstring
        clips = clip_plan.get('clips', [])
        if not 0 <= cue_index < len(clips):
            return []
        clip = clips[cue_index]
        return [LiveAction('fire_clip', track, 'clip.fire', True,
                           clip_index=cue_index,
                           clip_name=clip.get('name'))]
    return []


def plan_dispatch(command, clip_plan=None, cue_index=None):
    """Convert a midi_map.dispatch result (or None) into LiveActions."""
    if command is None:
        return []
    action, deck, value = command
    return plan_event(action, deck, value,
                      clip_plan=clip_plan, cue_index=cue_index)


class LiveBridge(object):
    """Stateful convenience wrapper the Remote Script adapter holds.

    Holds each deck's clip plan (built from library.cues_to_clip_plan,
    or by the adapter scanning the deck track's Session slots) and the
    fixed set BPM.  The BPM is recorded for display/logging only — by
    design no event ever turns into a tempo action.
    """

    def __init__(self, clip_plans=None, set_bpm=None):
        self.clip_plans = clip_plans or {}  # deck index -> clip plan
        self.set_bpm = set_bpm

    def handle(self, action, deck, value, cue_index=None):
        return plan_event(action, deck, value,
                          clip_plan=self.clip_plans.get(deck),
                          cue_index=cue_index)

    def handle_dispatch(self, command, cue_index=None):
        if command is None:
            return []
        return self.handle(command[0], command[1], command[2],
                           cue_index=cue_index)
