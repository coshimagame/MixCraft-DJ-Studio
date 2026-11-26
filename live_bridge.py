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
| hp_cue (deck)        | transport track.cue_toggle (headphones cue on/off)    |
| loop_in / loop_out   | transport clip.loop_in / clip.loop_out                |
| load   (deck)        | transport track.load (plan_event only; LiveBridge     |
|                      |   overrides it — see the browse rows below)           |
| hotcue_jump (deck)   | fire_clip for cues_to_clip_plan clip #cue_index       |
| cue_set (deck, pad)  | clip_edit clip.start_marker (needs pad_context)       |
| quantize (pad)       | song_edit clip_trigger_quantization, bar <-> none     |
| pad_loop_in (deck)   | LiveBridge loop state machine -> clip_edit actions    |
| pad_loop_out (deck)  | LiveBridge loop state machine -> clip_edit actions    |
| browse_turn (global) | highlight view.highlighted_clip_slot (LiveBridge)     |
| browse_push (global) | fire_clip at the browse cursor (LiveBridge)           |
| load   (deck)        | LiveBridge: browse target override -> highlight       |
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

Performance pads (v1.5, user request 2026-11-26 — he wants to try the
assignment on the real DDJ-RB and avoids mouse operation): the RB's 8
pads per deck (notes 0-7 on the pad channels) are laid out as

    pads 1-4  Hot Cue A-D     fire the current song's hot-cue clips
    pad 5     CUE SET         playing clip's start_marker := position
    pad 6     QUANTIZE        song clip_trigger_quantization bar<->none
    pad 7     LOOP IN         loop_start := position, provisional end
    pad 8     LOOP OUT        loop_end := position; again = loop off

`plan_event` stays stateless: it plans `cue_set` from a caller-supplied
`pad_context` (the adapter's observation of the playing clip:
playing_position / looping / loop_start / loop_end / length, all in
beats) and `quantize` from a caller-supplied current state.  The
stateful parts — which song group the deck is on, whether quantize is
currently on, and the loop in/out state machine — live in `LiveBridge`,
which is what the adapter actually calls.  plan_event returns [] for
pad_loop_in / pad_loop_out.

Song groups: in a set built for this workflow a song's clips sit in
consecutive slots, main clip first, then its hot-cue clips.  The
bridge recovers the groups from the clip plan: an explicit 'groups'
list of clip ordinals wins; otherwise a name heuristic groups a clip
with the previous song when its name is a hot-cue variant of that
song's main clip (see `compute_clip_groups`).  When no grouping is
detectable at all (e.g. the user's hand-built template set), pads 1-4
fall back to the v1.4 behaviour — pad n fires clip #n of the deck.

Browse knob (v1.6, user request 2026-11-26 — track selection by knob,
no mouse): the DDJ-RB's central BROWSE encoder turns a per-deck
*selection cursor* over the deck track's clips and its push fires
the selected clip.  The state lives in `LiveBridge` (plan_event
stays stateless and returns [] for browse actions):

- Target deck: the STANDBY deck — the one not currently playing
  (playing state is reported by the adapter via `note_deck_playing`).
  When neither or both decks are playing, the last target stands.
  The initial target is the deck with clips that is not playing,
  preferring Deck A (sets start there) and falling back to Deck B
  when only B has clips.
- LOAD 1 / LOAD 2 (documented notes 70/71 on the browser channel)
  override the target explicitly — selection only, LOAD never
  fires.  The override clears as soon as a clip fires on that deck
  (the natural flow: browse the standby deck, push to fire it, and
  browsing moves on to the new standby deck).
- Turn: the cursor moves one slot per encoder detent, clamped at
  the first/last clip (no wrap).  The plan is a `highlight` action;
  the adapter points Live's session highlight at the cursor's slot,
  which is the only feedback (the knob has no LED).
- Push: a plain `fire_clip` for the cursor's clip — the exact
  firing path the pads use.

MIDI caveat: the repo's documented DDJ-RB map contains NO browse
encoder message (the official-list extract in midi_map.py covers
the browser channel's crossfader / master / filter / LOAD only).
The turn/push bindings in midi_map.py are therefore placeholders on
the documented Browser/Global channel, pending a hardware probe —
see the comment there and TRY_PADS.md.  The relative-value decoding
follows the convention this codebase documents for the jog wheels
(0x01-0x3F = +, 0x7F-0x41 = -) but WITHOUT the jog's sign flip,
which was a jog-specific hardware finding; if the knob turns out
reversed, flip BROWSE_TURN_SIGN.
"""
from dataclasses import dataclass
from typing import Optional

# Deck index (midi_map convention) -> Live track number (template set).
DECK_TRACKS = {0: 1, 1: 2}

# One jog tick = this many beats of nudge.  Small on purpose: the jog is
# for nudging a warped clip back into phase, not for beat-jumping.
# 0.01 -> 0.005 on 2026-11-26: the user asked for a finer step.
NUDGE_BEATS_PER_TICK = 0.005

# Performance pads (v1.5): pads 1-4 are Hot Cue A-D, pad 5 CUE SET,
# pad 6 QUANTIZE, pad 7 LOOP IN, pad 8 LOOP OUT (see module docstring).
PAD_HOTCUE_COUNT = 4

# LOOP IN arms a provisional loop of this many beats; LOOP OUT then
# moves the end to the real position (or the clip end, whichever first).
LOOP_PROVISIONAL_BEATS = 4.0

# The two clip_trigger_quantization states pad 6 toggles between,
# as plain strings; the adapter maps them onto Live's enum
# (q_1_bar / q_no_q).  1 bar matches Live's own launch-quantize default.
QUANTIZATION_BAR = 'bar'
QUANTIZATION_NONE = 'none'

# Browse knob (v1.6): sign applied to the decoded encoder delta.
# +1 assumes clockwise = next clip, following the relative-value
# convention documented for the jog wheels but without the jog's
# hardware-observed sign flip.  Flip to -1 if the real knob runs
# reversed (see the module docstring's MIDI caveat).
BROWSE_TURN_SIGN = 1

_EQ_TARGETS = {'eq_high': 'eq_three.high',
               'eq_mid': 'eq_three.mid',
               'eq_low': 'eq_three.low'}

_TRANSPORT_TARGETS = {'play': 'clip.toggle_play',
                      'cue_jump': 'clip.cue_jump',
                      'hp_cue': 'track.cue_toggle',
                      'loop_in': 'clip.loop_in',
                      'loop_out': 'clip.loop_out',
                      'load': 'track.load'}

# Actions that intentionally produce no LiveAction (design doc 5.4).
_IGNORED_ACTIONS = {'tempo', 'sync'}


@dataclass(frozen=True)
class LiveAction:
    """One planned operation against Live.  Pure data, no Live objects.

    kind:   'set_parameter' | 'transport' | 'fire_clip' | 'nudge' |
            'clip_edit' | 'song_edit' | 'highlight'
    track:  1-based Live track number, or None for global (mixer) targets
    target: dotted name of what to touch, e.g. 'eq_three.high';
            'clip.start_marker' / 'clip.loop_start' / 'clip.loop_end' /
            'clip.looping' for clip_edit (the deck's playing clip),
            'clip_trigger_quantization' for song_edit,
            'view.highlighted_clip_slot' for highlight
    value:  normalised float for set_parameter, signed beats for nudge,
            True for transport / fire_clip / highlight, beats (float)
            or bool for clip_edit, 'bar' / 'none' for song_edit
    clip_index / clip_name: set for fire_clip and highlight (index
            into the deck's cues_to_clip_plan clips list)
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


def _browse_delta(normalised):
    """Signed detents from a browse-encoder CC (dispatch float).

    Same relative encoding the codebase documents for the jog
    wheels (raw 0x01-0x3F = +, 0x7F-0x41 = -), but NOT negated:
    the jog's flip was specific to the jog hardware path.  One
    message per detent on a typical encoder, so the tick count is
    the number of slots to move.
    """
    raw = round(_clamp01(normalised) * 127)
    ticks = raw if raw < 64 else raw - 128
    return BROWSE_TURN_SIGN * ticks


# Clip names follow two conventions in this codebase: a song's main
# clip is '<title> - <artist>' (live_plan; hyphen) and its hot-cue
# clips are '<title> – <cue label>' (library.cues_to_clip_plan;
# en dash U+2013).  The separators below are written as escapes so the
# bundled Python 2.7 copy compares them as unicode, never as bytes.
_NAME_SEPARATORS = (' - ', ' \u2013 ')


def clip_base_name(name):
    """The song-title part of a clip name (before the first separator)."""
    if not name:
        return ''
    cut = len(name)
    for sep in _NAME_SEPARATORS:
        index = name.find(sep)
        if 0 <= index < cut:
            cut = index
    return name[:cut]


def _name_remainder(name):
    """The part of a clip name after the first separator ('' if none)."""
    if not name:
        return ''
    best = None
    for sep in _NAME_SEPARATORS:
        index = name.find(sep)
        if index >= 0 and (best is None or index < best[0]):
            best = (index, len(sep))
    if best is None:
        return ''
    return name[best[0] + best[1]:]


def compute_clip_groups(clips):
    """Partition an ordered clip plan into song groups.

    Returns a list of groups; each group is a list of clip ordinals,
    main clip first, then that song's hot-cue clips.  A clip joins the
    previous group when its name is a hot-cue variant of that group's
    main clip: same base title, different remainder (e.g. main
    'Song 1 - Artist', variant 'Song 1 – Hot Cue A').  Identical names
    never group (a repeated song stays two groups), and clips without
    a separator in their name always start a new group — so a set with
    no detectable grouping yields one group per clip, which is what
    makes the pad fallback (fire clip #pad) well-defined.
    """
    groups = []
    for ordinal, clip in enumerate(clips):
        name = clip.get('name') or ''
        base = clip_base_name(name)
        joined = False
        if groups and base:
            main_name = clips[groups[-1][0]].get('name') or ''
            if (base == clip_base_name(main_name)
                    and name != main_name
                    and _name_remainder(name) != _name_remainder(main_name)):
                groups[-1].append(ordinal)
                joined = True
        if not joined:
            groups.append([ordinal])
    return groups


def _group_has_main(clips, group):
    """True when the group's first clip is a song's main clip.

    Convention check: a main clip is '<title> - <artist>' (hyphen)
    while its hot-cue clips are '<title> – <label>' (en dash).  A
    group whose first clip already uses the en-dash style (a bare
    cues_to_clip_plan list) has no separate main clip — every clip in
    it is a hot-cue clip, so pads address it from position 0.
    """
    if len(group) < 2:
        return False
    first = clips[group[0]].get('name') or ''
    if ' - ' not in first:
        return False
    base = clip_base_name(first)
    for ordinal in group[1:]:
        other = clips[ordinal].get('name') or ''
        if '\u2013' in other and clip_base_name(other) == base:
            return True
    return False


def plan_event(action, deck, value, clip_plan=None, cue_index=None,
               pad_context=None, quantize_on=True):
    """Convert one semantic controller event into LiveActions.

    `action`/`deck`/`value` follow the midi_map.dispatch contract above.
    `clip_plan` is a library.cues_to_clip_plan result for the deck's
    track; `cue_index` selects which hot-cue clip to fire.
    `pad_context` is the adapter's observation of the deck's playing
    clip (playing_position / looping / loop_start / loop_end / length,
    in beats) and is required by the pad actions that edit the clip;
    `quantize_on` is the caller's current quantize state (pad 6's
    toggle and CUE SET's beat snapping both read it — the state itself
    is owned by LiveBridge).
    Returns a list (possibly empty) of LiveAction.
    """
    if action is None or action in _IGNORED_ACTIONS:
        return []
    track = DECK_TRACKS.get(deck)  # None for global actions / unknown deck

    if action in ('cross', 'master'):
        target = 'mixer.crossfader' if action == 'cross' else 'mixer.master_volume'
        return [LiveAction('set_parameter', None, target, _clamp01(value))]
    if action == 'quantize':
        # Song-level, not deck-level: the action carries the deck the
        # pad sits on, but the target is the song's launch quantization.
        new_value = QUANTIZATION_NONE if quantize_on else QUANTIZATION_BAR
        return [LiveAction('song_edit', None,
                           'clip_trigger_quantization', new_value)]
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
    if action == 'cue_set':
        # Pad 5: the playing clip's start marker becomes the cue
        # point.  LOM playing positions are in beats for warped
        # clips, so with quantize on the snap is to the nearest
        # whole beat — the beat grid the clip itself plays on.
        if pad_context is None:
            return []
        position = pad_context.get('playing_position')
        if position is None:
            return []
        position = float(position)
        if quantize_on:
            position = float(int(round(position)))
        return [LiveAction('clip_edit', track, 'clip.start_marker',
                           position)]
    if action in ('pad_loop_in', 'pad_loop_out'):
        return []  # state machine: LiveBridge.handle owns these
    if action in ('browse_turn', 'browse_push'):
        return []  # cursor state: LiveBridge.handle owns these
    return []


def plan_dispatch(command, clip_plan=None, cue_index=None,
                  pad_context=None, quantize_on=True):
    """Convert a midi_map.dispatch result (or None) into LiveActions."""
    if command is None:
        return []
    action, deck, value = command
    return plan_event(action, deck, value, clip_plan=clip_plan,
                      cue_index=cue_index, pad_context=pad_context,
                      quantize_on=quantize_on)


class LiveBridge:
    """Stateful convenience wrapper the Remote Script adapter can hold.

    Holds each deck's clip plan (built from library.cues_to_clip_plan)
    and the fixed set BPM.  The BPM is recorded for display/logging
    only — by design no event ever turns into a tempo action.

    v1.5 pad state (see the module docstring's pad section):

    - `_group_main[deck]`: clip ordinal of the current song's main
      clip, learned from `note_clip_fired` (the adapter reports every
      clip it fires).  Pads 1-4 address the current group; when the
      set shows no grouping at all they fall back to firing clip
      #pad, the v1.4 behaviour.
    - `_quantize_on`: pad 6's toggle state.  Starts True — Live's
      factory default for clip launch quantization is 1 bar — so the
      first press turns quantization off.  If a set was saved with a
      different value the first press may appear to do nothing; the
      state is consistent from then on.
    - `_loop_state[deck]`: the LOOP IN/OUT state machine —
      'idle' -> (LOOP IN) -> 'open' (start set, provisional end,
      looping) -> (LOOP OUT) -> 'set' (real end, looping) ->
      (LOOP OUT again) -> 'idle' (looping off).  LOOP OUT from
      'idle' sets the end directly (the clip's existing loop_start
      stands) and lands in 'set'.

    v1.6 browse state (see the module docstring's browse section):

    - `_playing[deck]`: clip ordinal currently playing on the deck,
      or None — reported by the adapter via `note_deck_playing`;
      drives the standby-deck targeting rule.
    - `_browse_cursor[deck]`: the selection cursor (clip ordinal),
      one per deck so switching decks remembers each position.
      Defaults to the last clip fired on that deck, else clip 0.
    - `_browse_explicit`: deck pinned by LOAD 1/2, or None.  Clears
      when a clip fires on the pinned deck.
    - `_browse_last`: the last resolved target deck, used when the
      standby rule cannot decide (neither or both decks playing).
    """

    def __init__(self, clip_plans=None, set_bpm=None):
        self.clip_plans = clip_plans or {}  # deck index -> cues_to_clip_plan result
        self.set_bpm = set_bpm
        self._quantize_on = True
        self._loop_state = {}   # deck -> {'phase': str, 'start': float|None}
        self._group_main = {}   # deck -> clip ordinal of the song's main clip
        self._groups_cache = {}  # deck -> (names tuple, groups)
        self._playing = {}      # deck -> playing clip ordinal or None
        self._last_fired = {}   # deck -> last clip ordinal the script fired
        self._browse_cursor = {}  # deck -> selection cursor (clip ordinal)
        self._browse_explicit = None  # deck pinned by LOAD 1/2, or None
        self._browse_last = None      # last resolved browse target deck

    @property
    def quantize_on(self):
        """Pad 6's current state (True = launch quantization is 1 bar)."""
        return self._quantize_on

    # -- clip plans / song groups --------------------------------------

    def set_clip_plan(self, deck, plan):
        """Replace a deck's clip plan (adapter rescans call this).

        Keeps the tracked song when it still exists in the new plan;
        loop and quantize state are performance state and survive
        rescans untouched.
        """
        if plan is None:
            self.clip_plans.pop(deck, None)
        else:
            self.clip_plans[deck] = plan
        self._groups_cache.pop(deck, None)
        main = self._group_main.get(deck)
        if main is not None and not any(
                main in group for group in self._groups_for(deck)):
            self._group_main[deck] = None

    def _groups_for(self, deck):
        plan = self.clip_plans.get(deck) or {}
        explicit = plan.get('groups')
        if explicit:
            return [list(group) for group in explicit]
        clips = plan.get('clips', [])
        key = tuple(clip.get('name') for clip in clips)
        cached = self._groups_cache.get(deck)
        if cached is not None and cached[0] == key:
            return cached[1]
        groups = compute_clip_groups(clips)
        self._groups_cache[deck] = (key, groups)
        return groups

    def note_clip_fired(self, deck, ordinal):
        """Learn the current song from a clip the adapter fired.

        PLAY / CUE / pad firing all report here; the current group
        becomes the group containing the fired clip.
        """
        if ordinal is None or ordinal < 0:
            return
        self._last_fired[deck] = ordinal
        if self._browse_explicit == deck:
            # The LOAD-pinned deck just got its clip: the override
            # has done its job, standby targeting resumes.
            self._browse_explicit = None
        for group in self._groups_for(deck):
            if ordinal in group:
                self._group_main[deck] = group[0]
                return
        self._group_main[deck] = ordinal

    def note_deck_playing(self, deck, ordinal):
        """Record which clip is playing on a deck (None = stopped).

        The adapter reports this from its display tick; the browse
        target rule reads it to find the standby deck.
        """
        if deck in DECK_TRACKS:
            self._playing[deck] = ordinal

    def _hotcue_target(self, deck, cue_index):
        """Clip ordinal pad (cue_index + 1) would fire, or None."""
        plan = self.clip_plans.get(deck)
        if (not plan or cue_index is None
                or not 0 <= cue_index < PAD_HOTCUE_COUNT):
            return None
        clips = plan.get('clips', [])
        if not clips:
            return None
        groups = self._groups_for(deck)
        main = self._group_main.get(deck)
        if main is not None:
            for group in groups:
                if group and group[0] == main:
                    offset = 1 if _group_has_main(clips, group) else 0
                    position = cue_index + offset
                    if position < len(group):
                        return group[position]
                    break
            if any(len(group) > 1 for group in groups):
                # Grouping is detectable in this set and the current
                # song has no such hot cue: firing another song's
                # clip would be worse than firing none.
                return None
        if cue_index < len(clips):
            return cue_index  # fallback: pad n fires clip #n (v1.4)
        return None

    def _plan_hotcue(self, deck, cue_index):
        track = DECK_TRACKS.get(deck)
        if track is None:
            return []
        target = self._hotcue_target(deck, cue_index)
        if target is None:
            return []
        clips = self.clip_plans[deck].get('clips', [])
        return [LiveAction('fire_clip', track, 'clip.fire', True,
                           clip_index=target,
                           clip_name=clips[target].get('name'))]

    # -- loop state machine ---------------------------------------------

    def loop_phase(self, deck):
        """'idle' | 'open' | 'set' — the deck's pad-loop state."""
        return self._loop_state.get(deck, {}).get('phase', 'idle')

    def _plan_pad_loop_in(self, deck, pad_context):
        track = DECK_TRACKS.get(deck)
        if track is None or pad_context is None:
            return []
        position = pad_context.get('playing_position')
        if position is None:
            return []
        position = float(position)
        end = position + LOOP_PROVISIONAL_BEATS
        length = pad_context.get('length')
        if length:
            end = min(end, float(length))
        if end <= position:
            return []  # at/past the clip end: no sane loop to arm
        state = self._loop_state.setdefault(
            deck, {'phase': 'idle', 'start': None})
        state['phase'] = 'open'
        state['start'] = position
        return [LiveAction('clip_edit', track, 'clip.loop_start', position),
                LiveAction('clip_edit', track, 'clip.loop_end', end),
                LiveAction('clip_edit', track, 'clip.looping', True)]

    def _plan_pad_loop_out(self, deck, pad_context):
        track = DECK_TRACKS.get(deck)
        if track is None or pad_context is None:
            return []
        position = pad_context.get('playing_position')
        if position is None:
            return []
        position = float(position)
        state = self._loop_state.setdefault(
            deck, {'phase': 'idle', 'start': None})
        if state['phase'] == 'set':
            # A loop is active and set: this press turns it off.
            state['phase'] = 'idle'
            state['start'] = None
            return [LiveAction('clip_edit', track, 'clip.looping', False)]
        start = (state['start'] if state['phase'] == 'open'
                 else pad_context.get('loop_start', 0.0))
        actions = []
        if position > float(start):
            actions.append(
                LiveAction('clip_edit', track, 'clip.loop_end', position))
        # If the position is not past the start (e.g. OUT hit in the
        # same beat as IN), the provisional end from LOOP IN stands.
        actions.append(LiveAction('clip_edit', track, 'clip.looping', True))
        state['phase'] = 'set'
        return actions

    # -- pad LEDs ---------------------------------------------------------

    def pad_leds(self, deck, playing_ordinal=None):
        """The 8 pad LED states for a deck, as booleans.

        Pads 1-4: lit while the clip that pad would fire is playing.
        Pad 5 (CUE SET): lit while any clip is playing (a cue point
        can be set).  Pad 6: quantize on.  Pad 7: loop open (start
        set, waiting for OUT).  Pad 8: loop set and active.
        """
        leds = [False] * 8
        for n in range(PAD_HOTCUE_COUNT):
            target = self._hotcue_target(deck, n)
            leds[n] = (target is not None
                       and playing_ordinal is not None
                       and target == playing_ordinal)
        leds[4] = playing_ordinal is not None
        leds[5] = self._quantize_on
        phase = self.loop_phase(deck)
        leds[6] = phase == 'open'
        leds[7] = phase == 'set'
        return leds

    # -- browse knob (v1.6) ----------------------------------------------

    def _browse_clip_count(self, deck):
        plan = self.clip_plans.get(deck) or {}
        return len(plan.get('clips', []))

    def browse_target_deck(self):
        """The deck the browse knob currently addresses.

        LOAD-pinned deck first; else the standby deck (the one not
        playing); else the last target; initially the deck with
        clips that is not playing, Deck A preferred (see the module
        docstring).  The resolution is remembered as the last target.
        """
        playing = [deck for deck in (0, 1)
                   if self._playing.get(deck) is not None]
        if self._browse_explicit is not None:
            target = self._browse_explicit
        elif len(playing) == 1:
            target = 1 - playing[0]
        elif self._browse_last is not None:
            target = self._browse_last
        elif self._browse_clip_count(0):
            target = 0
        elif self._browse_clip_count(1):
            target = 1
        else:
            target = 0
        self._browse_last = target
        return target

    def browse_cursor(self, deck):
        """The deck's selection cursor (clip ordinal), or None.

        None when the deck has no clips.  Defaults to the last clip
        fired on the deck, else the first clip; clamped into the
        current plan on every read, so plan rescans cannot leave
        the cursor pointing past the end.
        """
        count = self._browse_clip_count(deck)
        if count == 0:
            return None
        cursor = self._browse_cursor.get(deck)
        if cursor is None:
            cursor = self._last_fired.get(deck, 0)
        cursor = min(max(int(cursor), 0), count - 1)
        self._browse_cursor[deck] = cursor
        return cursor

    def _highlight_action(self, deck):
        track = DECK_TRACKS.get(deck)
        cursor = self.browse_cursor(deck)
        if track is None or cursor is None:
            return []
        clips = self.clip_plans[deck].get('clips', [])
        return [LiveAction('highlight', track,
                           'view.highlighted_clip_slot', True,
                           clip_index=cursor,
                           clip_name=clips[cursor].get('name'))]

    def _plan_browse_turn(self, value):
        delta = _browse_delta(value)
        if delta == 0:
            return []
        deck = self.browse_target_deck()
        count = self._browse_clip_count(deck)
        if count == 0:
            return []
        cursor = self.browse_cursor(deck)
        moved = min(max(cursor + delta, 0), count - 1)
        if moved == cursor:
            return []  # at the first/last clip: no wrap, no plan
        self._browse_cursor[deck] = moved
        return self._highlight_action(deck)

    def _plan_browse_push(self):
        deck = self.browse_target_deck()
        track = DECK_TRACKS.get(deck)
        cursor = self.browse_cursor(deck)
        if track is None or cursor is None:
            return []
        clips = self.clip_plans[deck].get('clips', [])
        return [LiveAction('fire_clip', track, 'clip.fire', True,
                           clip_index=cursor,
                           clip_name=clips[cursor].get('name'))]

    def _plan_load(self, deck):
        """LOAD 1/2: pin the browse target to this deck (no firing).

        plan_event's plain 'load' -> transport track.load conversion
        is superseded here for the script path: the adapter's
        track.load was a deliberate no-op (set list fixed in
        advance), and the user now wants track selection on the
        hardware, so LOAD becomes the target override.  The plan is
        the highlight for the newly targeted deck, so he sees the
        selection jump on screen.
        """
        if deck not in DECK_TRACKS:
            return []
        self._browse_explicit = deck
        self._browse_last = deck
        return self._highlight_action(deck)

    # -- entry points -----------------------------------------------------

    def handle(self, action, deck, value, cue_index=None, pad_context=None):
        if action == 'hotcue_jump':
            return self._plan_hotcue(deck, cue_index)
        if action == 'pad_loop_in':
            return self._plan_pad_loop_in(deck, pad_context)
        if action == 'pad_loop_out':
            return self._plan_pad_loop_out(deck, pad_context)
        if action == 'browse_turn':
            return self._plan_browse_turn(value)
        if action == 'browse_push':
            return self._plan_browse_push()
        if action == 'load':
            return self._plan_load(deck)
        actions = plan_event(action, deck, value,
                             clip_plan=self.clip_plans.get(deck),
                             cue_index=cue_index, pad_context=pad_context,
                             quantize_on=self._quantize_on)
        if action == 'quantize' and actions:
            self._quantize_on = not self._quantize_on
        return actions

    def handle_dispatch(self, command, cue_index=None, pad_context=None):
        if command is None:
            return []
        return self.handle(command[0], command[1], command[2],
                           cue_index=cue_index, pad_context=pad_context)
