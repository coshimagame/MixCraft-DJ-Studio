# -*- coding: utf-8 -*-
"""MixCraft Remote Script v1 — the thin Live-side adapter.

Lives inside Ableton Live (User Library/Remote Scripts/MixCraft/) and
does only what the design memo (remote_script/README.md) assigns to it:

1. receive DDJ-RB MIDI, hand it to ``_midi_map.dispatch`` (bundled
   Python 2.7 copy of the repo-root ``midi_map.py``);
2. turn dispatch results into plans via ``_live_bridge.LiveBridge``
   (bundled copy of ``live_bridge.py``) — supplying the pad note number
   as ``cue_index``, because dispatch drops the pad index;
3. execute the returned LiveActions against real Live objects;
4. drive the DDJ-RB LEDs (manual mapping never lit them — verified on
   hardware 2026-11-25, so the script sends the Note On/Off itself).

Everything is defensive: a missing device/parameter/clip is skipped
silently (one log line at most), never an exception that would kill
the control surface.  Tempo/SYNC input is ignored by the bridge
(fixed set BPM, user decision 2026-11-25).

Template assumptions (see SETUP.md): Track 1 = Deck A, Track 2 =
Deck B; each deck track carries a Utility (TRIM), an EQ Three and an
Auto Filter; crossfader assignment A/B is pre-set in the template;
Master Out = 1/2, Cue Out = 3/4 on the DDJ-RB ASIO driver.

Written in Python 2.7 syntax for Live 10's bundled interpreter.
"""
import Live
import math

from _Framework.ControlSurface import ControlSurface

try:
    from . import _live_bridge as bridge_mod
    from . import _midi_map as midi_mod
except (ImportError, ValueError):  # fallback if loaded as loose modules
    import _live_bridge as bridge_mod
    import _midi_map as midi_mod

PROFILE_NAME = 'DDJ-RB'
PROFILE = midi_mod.DDJ_RB

DECKS = (0, 1)
PLAY_NOTE = 11          # per deck channel (0-based 0/1)
CUE_NOTE = 12           # per deck channel
HP_CUE_NOTE = 84        # HEADPHONES CUE 1/2, per deck channel
PAD_CHANNELS = (7, 8)   # 0-based; PDF channels 8/9
PAD_COUNT = 8

# 0-based channels the DDJ-RB profile uses: decks (0/1), FX (4/5),
# global/browser (6), performance pads (7/8).  build_midi_map
# forwards the full note/CC ranges on exactly these channels.
FORWARD_CHANNELS = (0, 1, 4, 5, 6, 7, 8)

# EQ Three's real LOM parameter names are GainLo/GainMid/GainHi —
# ground truth from the v1.3 device dump in the user's real
# Live 10.1.43 Log.txt (2026-11-26): class_name 'FilterEQ3',
# params ['Device On','GainLo','GainMid','GainHi','FreqLo',
# 'FreqHi','LowOn','MidOn','HighOn','Slope'].  Note the low band
# is 'GainLo', NOT 'GainLow' — that one wrong letter kept the LOW
# knob dead through v1.2.  Each entry is an ordered candidate
# tuple: the true LOM name first, older spellings kept as
# fallbacks for builds that might differ.
_EQ_PARAMS = {'eq_three.high': ('GainHi', 'High Gain'),
              'eq_three.mid': ('GainMid', 'Mid Gain'),
              'eq_three.low': ('GainLo', 'GainLow', 'Low Gain')}
_EQ_DEVICE_CLASSES = ('FilterEQ3', 'Eq3', 'Eq8')
_EQ_DEVICE_NAMES = ('EQ Three', 'EQ Eight')
_FILTER_DEVICE_CLASSES = ('AutoFilter',)
_FILTER_DEVICE_NAMES = ('Auto Filter',)
_UTILITY_CLASSES = ('Utility',)
_UTILITY_NAMES = ('Utility',)

# Auto Filter "Filter Type" enum indices (legacy Auto Filter order:
# lowpass, highpass, bandpass, notch, morph).  VERIFY on hardware /
# at template build time — see SETUP.md.
_FILTER_TYPE_LOWPASS = 0
_FILTER_TYPE_HIGHPASS = 1

_PLAN_REFRESH_TICKS = 50  # update_display calls between clip-plan rescans


class MixCraft(ControlSurface):
    """DDJ-RB -> Live adapter.  All intelligence stays in the bridge."""

    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._bridge = bridge_mod.LiveBridge()
        # deck -> actual Session slot index last fired by this script
        self._loaded_slot = dict((deck, None) for deck in DECKS)
        # (channel, note) -> last velocity sent, to avoid MIDI spam
        self._led_cache = {}
        # deck -> last filter side (-1 LP, +1 HP) so the type parameter
        # is only rewritten when the knob crosses the centre
        self._filter_side = dict((deck, 0) for deck in DECKS)
        self._tick = 0
        self._refresh_clip_plans()
        try:
            self.log_message('MixCraft Remote Script v1 loaded '
                             '(DDJ-RB companion)')
        except Exception:
            pass

    def disconnect(self):
        # Turn every LED we may have lit off again.
        for (channel, note) in list(self._led_cache.keys()):
            self._send_led(channel, note, 0)
        try:
            ControlSurface.disconnect(self)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # MIDI input
    # ------------------------------------------------------------------
    def build_midi_map(self, midi_map_handle):
        # The parent builds the usual component/parameter map first.
        ControlSurface.build_midi_map(self, midi_map_handle)
        # Live delivers MIDI to receive_midi() ONLY for messages that
        # are explicitly forwarded here — Ableton's own scripts put it
        # plainly: "when you are not forwarding MIDI, nor mapping
        # parameters, you will never get any MIDI messages at all."
        # v1 forwarded nothing, so on real hardware the surface loaded
        # (and logged that it had) yet never received a single message
        # (confirmed on Live 10.1.43, 2026-11-25).  The input port is
        # dedicated to the DDJ-RB, so forward the full note/CC ranges
        # on every channel the profile uses; future profile additions
        # on those channels then work without another map change.
        try:
            script_handle = self._c_instance.handle()
            for channel in FORWARD_CHANNELS:
                for note in range(128):
                    Live.MidiMap.forward_midi_note(
                        script_handle, midi_map_handle, channel, note)
                for cc_number in range(128):
                    Live.MidiMap.forward_midi_cc(
                        script_handle, midi_map_handle, channel, cc_number)
        except Exception as exc:
            # Forwarding must never take the surface down with it.
            try:
                self.log_message('MixCraft: MIDI forwarding failed: %s'
                                 % (exc,))
            except Exception:
                pass

    def receive_midi(self, midi_bytes):
        if not midi_bytes or len(midi_bytes) < 3:
            return
        status = midi_bytes[0]
        number = midi_bytes[1]
        value = midi_bytes[2]
        channel = status & 0x0F
        if 0xB0 <= status <= 0xBF:
            kind = 'cc'
        elif 0x90 <= status <= 0x9F:
            kind = 'note'
        elif 0x80 <= status <= 0x8F:
            kind = 'note'
            value = 0
        else:
            return  # pitch bend / aftertouch / system: not ours
        # dispatch drops the pad index (known constraint): remember the
        # note number of pad-channel notes so the bridge can select the
        # hot-cue clip.  Pad notes are 0-7 by construction of the profile.
        cue_index = None
        if kind == 'note' and channel in PAD_CHANNELS:
            cue_index = number
        command = midi_mod.dispatch(PROFILE, kind, channel, number, value)
        if command is None:
            return
        try:
            actions = self._bridge.handle_dispatch(command,
                                                   cue_index=cue_index)
        except Exception:
            return
        for action in actions:
            self._execute(action)

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------
    def _execute(self, action):
        try:
            if action.kind == 'set_parameter':
                self._execute_set_parameter(action)
            elif action.kind == 'transport':
                self._execute_transport(action)
            elif action.kind == 'fire_clip':
                self._execute_fire_clip(action)
            elif action.kind == 'nudge':
                self._execute_nudge(action)
        except Exception:
            # A single bad target must never take the surface down.
            pass

    def _execute_set_parameter(self, action):
        if action.track is None:
            master = self.song().master_track
            if action.target == 'mixer.crossfader':
                self._set_normalised(master.mixer_device.crossfader,
                                     action.value)
            elif action.target == 'mixer.master_volume':
                self._set_normalised(master.mixer_device.volume,
                                     action.value)
            return
        track = self._deck_track(action.track)
        if track is None:
            return
        target = action.target
        if target == 'track.volume':
            self._set_normalised(track.mixer_device.volume, action.value)
        elif target in _EQ_PARAMS:
            param = self._find_param(track, _EQ_DEVICE_CLASSES,
                                     _EQ_DEVICE_NAMES,
                                     _EQ_PARAMS[target])
            if param is not None:
                self._set_normalised(param, action.value)
        elif target == 'input.gain':
            param = self._find_param(track, _UTILITY_CLASSES,
                                     _UTILITY_NAMES, 'Gain')
            if param is not None:
                self._set_normalised(param, action.value)
        elif target == 'filter.bipolar':
            self._apply_filter(track, action.track - 1, action.value)

    def _execute_transport(self, action):
        track = self._deck_track(action.track)
        if track is None:
            return
        deck = action.track - 1
        target = action.target
        if target == 'clip.toggle_play':
            self._toggle_play(track, deck)
        elif target == 'clip.cue_jump':
            self._cue_jump(track, deck)
        elif target == 'track.cue_toggle':
            # HEADPHONES CUE button: toggle the track's Solo switch.
            # In a set whose Cue Out is a separate output (the
            # template pins 3/4), Live treats Solo as Cue, so this
            # routes the deck to the headphones only.
            track.solo = not track.solo
        elif target == 'clip.loop_in':
            clip = self._playing_clip(track)
            if clip is not None:
                clip.loop_start = clip.playing_position
        elif target == 'clip.loop_out':
            clip = self._playing_clip(track)
            if clip is not None:
                clip.loop_end = clip.playing_position
                clip.looping = True
        elif target == 'track.load':
            pass  # no-op by design: the set list is fixed in advance

    def _execute_fire_clip(self, action):
        track = self._deck_track(action.track)
        if track is None or action.clip_index is None:
            return
        slot = self._clip_bearing_slot(track, action.clip_index)
        if slot is None:
            return
        slot.fire()
        for i, candidate in enumerate(track.clip_slots):
            if candidate is slot:
                self._loaded_slot[action.track - 1] = i
                break

    def _execute_nudge(self, action):
        track = self._deck_track(action.track)
        if track is None:
            return
        clip = self._playing_clip(track)
        if clip is None:
            return
        # move_playing_pos exists in the Live Object Model; guard for
        # builds where it is not exposed to the Python API.  If it is
        # missing the jog simply does nothing in v1 (SETUP.md notes
        # the beat-jump fallback as future work).
        move = getattr(clip, 'move_playing_pos', None)
        if move is not None:
            move(action.value)

    # ------------------------------------------------------------------
    # Transport helpers
    # ------------------------------------------------------------------
    def _toggle_play(self, track, deck):
        if track.playing_slot_index >= 0:
            track.stop_all_clips()
            return
        slot = self._start_slot(track, deck)
        if slot is not None:
            slot.fire()

    def _cue_jump(self, track, deck):
        # v1 approximation of the DDJ-RB CUE button: while a clip is
        # playing, re-fire its slot so playback restarts from the
        # clip's start marker (the template sets the start marker to
        # the cue point).  When stopped, fire the loaded clip.
        index = track.playing_slot_index
        if index >= 0:
            track.clip_slots[index].fire()
            self._loaded_slot[deck] = index
            return
        slot = self._start_slot(track, deck)
        if slot is not None:
            slot.fire()

    def _start_slot(self, track, deck):
        """The slot PLAY should fire: last fired, else first with a clip."""
        index = self._loaded_slot.get(deck)
        if index is not None and 0 <= index < len(track.clip_slots):
            slot = track.clip_slots[index]
            if slot.has_clip:
                return slot
        for i, slot in enumerate(track.clip_slots):
            if slot.has_clip:
                self._loaded_slot[deck] = i
                return slot
        return None

    def _clip_bearing_slot(self, track, ordinal):
        """The ordinal-th slot that actually holds a clip (0-based)."""
        seen = -1
        for slot in track.clip_slots:
            if slot.has_clip:
                seen += 1
                if seen == ordinal:
                    return slot
        return None

    def _playing_clip(self, track):
        index = track.playing_slot_index
        if index < 0 or index >= len(track.clip_slots):
            return None
        return track.clip_slots[index].clip

    # ------------------------------------------------------------------
    # Device / parameter plumbing
    # ------------------------------------------------------------------
    def _deck_track(self, track_number):
        """1-based template track number -> Live track object or None."""
        tracks = self.song().tracks
        if 1 <= track_number <= len(tracks):
            return tracks[track_number - 1]
        return None

    def _set_normalised(self, param, value):
        """Map a 0.0-1.0 bridge value onto the parameter's own range."""
        low = param.min
        high = param.max
        param.value = low + float(value) * (high - low)

    def _find_param(self, track, class_names, device_names, param_name):
        """Find a device parameter by device class/name + parameter name.

        ``param_name`` may be a single name or an ordered tuple of
        candidate names (EQ Three: the real LOM name first, legacy
        spellings after); candidates are tried in order within the
        matched device.  Primary device match is the LOM
        ``class_name``; the display-name match is a fallback for
        builds whose class strings differ.  Returns None (caller
        skips) when nothing matches.
        """
        if isinstance(param_name, (tuple, list)):
            candidates = tuple(param_name)
        else:
            candidates = (param_name,)
        fallback = None
        for device in track.devices:
            params = None
            if getattr(device, 'class_name', None) in class_names:
                params = device.parameters
            elif device.name in device_names:
                params = device.parameters
            elif fallback is None:
                for param in device.parameters:
                    if param.name in candidates:
                        fallback = param
                        break
            if params is not None:
                for candidate in candidates:
                    for param in params:
                        if param.name == candidate:
                            return param
        return fallback

    def _apply_filter(self, track, deck, bipolar):
        device = None
        for candidate in track.devices:
            if (getattr(candidate, 'class_name', None)
                    in _FILTER_DEVICE_CLASSES
                    or candidate.name in _FILTER_DEVICE_NAMES):
                device = candidate
                break
        if device is None:
            return
        freq_param = None
        type_param = None
        for param in device.parameters:
            if param.name == 'Frequency':
                freq_param = param
            elif param.name == 'Filter Type':
                type_param = param
        if freq_param is None:
            return
        if bipolar <= 0.0:
            side = -1
            sweep = 1.0 + bipolar      # centre 1.0 -> full LP 0.0
            type_value = _FILTER_TYPE_LOWPASS
        else:
            side = 1
            sweep = bipolar            # centre 0.0 -> full HP 1.0
            type_value = _FILTER_TYPE_HIGHPASS
        if type_param is not None and self._filter_side.get(deck) != side:
            try:
                type_param.value = type_value
            except Exception:
                pass
            self._filter_side[deck] = side
        low = freq_param.min
        high = freq_param.max
        if low > 0 and high > low:
            # Frequency is perceived logarithmically; map in log space.
            freq_param.value = math.exp(
                math.log(low) + sweep * (math.log(high) - math.log(low)))
        else:
            freq_param.value = low + sweep * (high - low)

    # ------------------------------------------------------------------
    # Clip plans (hot-cue pads)
    # ------------------------------------------------------------------
    def _refresh_clip_plans(self):
        """Build each deck's clip plan from its Session slots.

        The bridge only fires a hot cue when a plan exists.  Until a
        rekordbox-derived plan is wired in (future stage), the plan is
        simply "the clips sitting in the deck track's slots, in slot
        order" — which is exactly how the template asks the user to
        lay hot-cue clips out (see SETUP.md).  Empty slots are skipped
        here and again when firing, so gaps never misalign the pads.
        """
        plans = {}
        for deck in DECKS:
            track = self._deck_track(deck + 1)
            if track is None:
                continue
            clips = []
            for slot in track.clip_slots:
                if slot.has_clip:
                    clips.append({'name': slot.clip.name})
            if clips:
                plans[deck] = {'clips': clips, 'locators': []}
        self._bridge.clip_plans = plans

    # ------------------------------------------------------------------
    # LED feedback
    # ------------------------------------------------------------------
    def update_display(self):
        self._tick += 1
        if self._tick % _PLAN_REFRESH_TICKS == 0:
            try:
                self._refresh_clip_plans()
            except Exception:
                pass
        try:
            self._refresh_leds()
        except Exception:
            pass

    def _refresh_leds(self):
        playing = []
        for deck in DECKS:
            track = self._deck_track(deck + 1)
            playing.append(bool(track is not None
                                and track.playing_slot_index >= 0))
        # PLAY LEDs: reuse midi_map.led_feedback's message construction;
        # the DDJ-RB expects velocity 127 = lit (manual mapping never
        # lit them at all, so this is script-only territory — the 127
        # assumption is a hardware-check item in SETUP.md).
        for (status, note, velocity) in midi_mod.led_feedback(
                PROFILE_NAME, playing):
            self._send_led(status & 0x0F, note, 127 if velocity else 0)
        # CUE LEDs: lit while a clip is loaded but not playing
        # (CDJ-style), dark while playing or when nothing is loaded.
        for deck in DECKS:
            track = self._deck_track(deck + 1)
            loaded = False
            if track is not None:
                if self._loaded_slot.get(deck) is not None:
                    loaded = True
                else:
                    loaded = any(slot.has_clip
                                 for slot in track.clip_slots)
            self._send_led(deck, CUE_NOTE,
                           127 if (loaded and not playing[deck]) else 0)
        # Headphone-cue LEDs: lit while the deck track's cue (Solo,
        # acting as Cue when Cue Out is a separate output) is on.
        for deck in DECKS:
            track = self._deck_track(deck + 1)
            cued = bool(track is not None
                        and getattr(track, 'solo', False))
            self._send_led(deck, HP_CUE_NOTE, 127 if cued else 0)
        # Pad LEDs: lit for pads whose slot holds a clip.
        for deck in DECKS:
            plan = self._bridge.clip_plans.get(deck) or {}
            count = len(plan.get('clips', []))
            for n in range(PAD_COUNT):
                self._send_led(PAD_CHANNELS[deck], n,
                               127 if n < count else 0)

    def _send_led(self, channel, note, velocity):
        key = (channel, note)
        if self._led_cache.get(key) == velocity:
            return
        self._led_cache[key] = velocity
        try:
            self._send_midi((0x90 | channel, note, velocity))
        except Exception:
            pass
