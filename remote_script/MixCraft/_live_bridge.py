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

The v1.5 performance-pad rules (song groups, quantize toggle, loop
state machine, pad LEDs) are mirrored identically; the name
separators are written as unicode escapes so Python 2 compares them
as unicode, never as bytes, against clip names coming from Live.

The full design rationale (conversion table, fixed-BPM rule, jog
encoding assumption) lives in the root module's docstring — read it
there.  When the root module's rules change, mirror them here.
"""

# Deck index (midi_map convention) -> Live track number (template set).
DECK_TRACKS = {0: 1, 1: 2}

# One jog tick = this many beats of nudge.
# 0.01 -> 0.005 on 2026-11-26 (finer step, user request; mirror of root).
NUDGE_BEATS_PER_TICK = 0.005

# Performance pads (v1.5, mirror of root): pads 1-4 Hot Cue A-D,
# pad 5 CUE SET, pad 6 QUANTIZE, pad 7 LOOP IN, pad 8 LOOP OUT.
PAD_HOTCUE_COUNT = 4

# LOOP IN arms a provisional loop of this many beats.
LOOP_PROVISIONAL_BEATS = 4.0

# The two clip_trigger_quantization states pad 6 toggles between.
QUANTIZATION_BAR = 'bar'
QUANTIZATION_NONE = 'none'

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


class LiveAction(object):
    """One planned operation against Live.  Pure data, no Live objects.

    kind:   'set_parameter' | 'transport' | 'fire_clip' | 'nudge' |
            'clip_edit' | 'song_edit'
    track:  1-based Live track number, or None for global (mixer) targets
    target: dotted name of what to touch, e.g. 'eq_three.high';
            'clip.start_marker' / 'clip.loop_start' / 'clip.loop_end' /
            'clip.looping' for clip_edit, 'clip_trigger_quantization'
            for song_edit
    value:  normalised float for set_parameter, signed beats for nudge,
            True for transport / fire_clip, beats or bool for
            clip_edit, 'bar' / 'none' for song_edit
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


# Clip-name separators (mirror of root): main clips are
# '<title> - <artist>' (hyphen), hot-cue clips '<title> – <label>'
# (en dash U+2013).  Escapes, so Python 2 treats them as unicode.
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
    """Partition an ordered clip plan into song groups (mirror of root).

    A clip joins the previous group when its name is a hot-cue
    variant of that group's main clip (same base title, different
    remainder).  No detectable grouping -> one group per clip.
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
                    and _name_remainder(name)
                    != _name_remainder(main_name)):
                groups[-1].append(ordinal)
                joined = True
        if not joined:
            groups.append([ordinal])
    return groups


def _group_has_main(clips, group):
    """True when the group's first clip is a song's main clip.

    Main = '<title> - <artist>' (hyphen) with en-dash hot-cue clips
    after it; a group starting with an en-dash name has no separate
    main, so pads address it from position 0 (mirror of root).
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

    `action`/`deck`/`value` follow the midi_map.dispatch contract.
    `clip_plan` is a library.cues_to_clip_plan result for the deck's
    track; `cue_index` selects which hot-cue clip to fire.
    `pad_context` is the adapter's observation of the deck's playing
    clip (beats); `quantize_on` is the caller's quantize state, owned
    by LiveBridge (mirror of root).
    Returns a list (possibly empty) of LiveAction.
    """
    if action is None or action in _IGNORED_ACTIONS:
        return []
    track = DECK_TRACKS.get(deck)  # None for global actions / unknown deck

    if action in ('cross', 'master'):
        target = ('mixer.crossfader' if action == 'cross'
                  else 'mixer.master_volume')
        return [LiveAction('set_parameter', None, target, _clamp01(value))]
    if action == 'quantize':
        # Song-level: toggles the song's launch quantization.
        new_value = QUANTIZATION_NONE if quantize_on else QUANTIZATION_BAR
        return [LiveAction('song_edit', None,
                           'clip_trigger_quantization', new_value)]
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
    if action == 'cue_set':
        # Pad 5: start marker := playing position, snapped to the
        # nearest whole beat while quantize is on (mirror of root).
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
    return []


def plan_dispatch(command, clip_plan=None, cue_index=None,
                  pad_context=None, quantize_on=True):
    """Convert a midi_map.dispatch result (or None) into LiveActions."""
    if command is None:
        return []
    action, deck, value = command
    return plan_event(action, deck, value,
                      clip_plan=clip_plan, cue_index=cue_index,
                      pad_context=pad_context, quantize_on=quantize_on)


class LiveBridge(object):
    """Stateful convenience wrapper the Remote Script adapter holds.

    Holds each deck's clip plan (built from library.cues_to_clip_plan,
    or by the adapter scanning the deck track's Session slots) and the
    fixed set BPM.  The BPM is recorded for display/logging only — by
    design no event ever turns into a tempo action.

    v1.5 pad state (mirror of the root module — see its docstring):
    `_group_main[deck]` is the current song's main clip ordinal,
    learned from `note_clip_fired`; `_quantize_on` is pad 6's toggle
    (starts True, Live's 1-bar factory default); `_loop_state[deck]`
    is the LOOP IN/OUT state machine ('idle' -> 'open' -> 'set' ->
    'idle').
    """

    def __init__(self, clip_plans=None, set_bpm=None):
        self.clip_plans = clip_plans or {}  # deck index -> clip plan
        self.set_bpm = set_bpm
        self._quantize_on = True
        self._loop_state = {}   # deck -> {'phase': str, 'start': float}
        self._group_main = {}   # deck -> main clip ordinal of the song
        self._groups_cache = {}  # deck -> (names tuple, groups)

    @property
    def quantize_on(self):
        """Pad 6's current state (True = launch quantization is 1 bar)."""
        return self._quantize_on

    # -- clip plans / song groups --------------------------------------

    def set_clip_plan(self, deck, plan):
        """Replace a deck's clip plan; keeps the tracked song if it
        still exists in the new plan (mirror of root)."""
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
        """Learn the current song from a clip the adapter fired."""
        if ordinal is None or ordinal < 0:
            return
        for group in self._groups_for(deck):
            if ordinal in group:
                self._group_main[deck] = group[0]
                return
        self._group_main[deck] = ordinal

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
                # Grouping is detectable and this song has no such
                # hot cue: firing another song's clip is worse.
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
        # Position not past the start: the provisional end stands.
        actions.append(LiveAction('clip_edit', track, 'clip.looping', True))
        state['phase'] = 'set'
        return actions

    # -- pad LEDs ---------------------------------------------------------

    def pad_leds(self, deck, playing_ordinal=None):
        """The 8 pad LED states for a deck, as booleans (mirror of root).

        Pads 1-4 lit while their target clip plays; pad 5 while any
        clip plays; pad 6 = quantize on; pad 7 = loop open; pad 8 =
        loop set and active.
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

    # -- entry points -----------------------------------------------------

    def handle(self, action, deck, value, cue_index=None, pad_context=None):
        if action == 'hotcue_jump':
            return self._plan_hotcue(deck, cue_index)
        if action == 'pad_loop_in':
            return self._plan_pad_loop_in(deck, pad_context)
        if action == 'pad_loop_out':
            return self._plan_pad_loop_out(deck, pad_context)
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
