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
PAD_CHANNELS = (7, 8)   # 0-based; PDF channels 8/9
PAD_COUNT = 8

# 0-based channels the DDJ-RB profile uses: decks (0/1), FX (4/5),
# global/browser (6), performance pads (7/8).  build_midi_map
# forwards the full note/CC ranges on exactly these channels.
FORWARD_CHANNELS = (0, 1, 4, 5, 6, 7, 8)

# EQ Three's real LOM parameter names are GainLow/GainMid/GainHi
# (hardware-verified 2026-11-26: the '... Gain' spellings matched
# nothing, so the EQ knobs were silently dead).  Each entry is an
# ordered candidate tuple: the true LOM name first, the old spelling
# kept as a fallback for builds that might differ.
_EQ_PARAMS = {'eq_three.high': ('GainHi', 'High Gain'),
              'eq_three.mid': ('GainMid', 'Mid Gain'),
              'eq_three.low': ('GainLow', 'Low Gain')}
_EQ_DEVICE_CLASSES = ('Eq3', 'Eq8')
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
        # Diagnostics for the LOW-band investigation (2026-11-26):
        # MID/HI move on hardware but LOW alone does not, while every
        # layer tests green in mocks — so capture ground truth from
        # the real Live objects once, greppable as "MixCraft DIAG".
        self._diag_dumped = False
        self._diag_eq_failed = set()   # (track, target) already logged
        self._diag_eq_matched = set()  # (track, target) already logged
        self._diag_transport_logged = set()  # (track, target), ditto
        self._diag_jog_logged = set()  # tracks whose first jog logged
        self._diag_jog_raw = {}        # deck -> last raw normalised jog
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
        if command[0] == 'jog':
            # Remember the raw normalised value so the first-jog
            # diagnostic in _execute_nudge can show it next to the
            # delta the bridge computed from it.
            self._diag_jog_raw[command[1]] = command[2]
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
                self._diag_eq_result(action.track, target, param.name)
                self._set_normalised(param, action.value)
            else:
                self._diag_eq_result(action.track, target, None)
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
            playing_index = track.playing_slot_index
            outcome = self._toggle_play(track, deck)
            self._diag_transport(action.track, target, playing_index,
                                 outcome)
        elif target == 'clip.cue_jump':
            playing_index = track.playing_slot_index
            outcome = self._cue_jump(track, deck)
            self._diag_transport(action.track, target, playing_index,
                                 outcome)
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
        self._diag_jog(action)
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
        # Returns a short outcome token for the transport diagnostics.
        if track.playing_slot_index >= 0:
            track.stop_all_clips()
            return 'stopped playback'
        slot = self._start_slot(track, deck)
        if slot is not None:
            slot.fire()
            return 'fired start slot'
        return 'no slot found'

    def _cue_jump(self, track, deck):
        # v1 approximation of the DDJ-RB CUE button: while a clip is
        # playing, re-fire its slot so playback restarts from the
        # clip's start marker (the template sets the start marker to
        # the cue point).  When stopped, fire the loaded clip.
        # Returns a short outcome token for the transport diagnostics.
        index = track.playing_slot_index
        if index >= 0:
            track.clip_slots[index].fire()
            self._loaded_slot[deck] = index
            return 're-fired playing slot'
        slot = self._start_slot(track, deck)
        if slot is not None:
            slot.fire()
            return 'fired start slot'
        return 'no slot found'

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
    # Diagnostics (LOW-band + CUE investigation, 2026-11-26)
    # ------------------------------------------------------------------
    # On the user's Live 10.1.43 the EQ LOW knob alone does nothing
    # while TRIM/FILTER/MID/HI all move, and every software layer
    # (dispatch -> bridge -> candidate-tuple lookup) tests green in
    # mocks.  The asymmetry must live in the real Live objects, so
    # dump them once and log the EQ lookup outcome once per target.
    # The CUE button was reported dead the same morning, so PLAY/CUE
    # transport arrivals and their outcomes are logged once each too.
    # Every line carries the "MixCraft DIAG" prefix for Log.txt
    # grepping, and nothing here may ever raise.
    def _diag_log(self, message):
        try:
            self.log_message(message)
        except Exception:
            pass

    def _diag_dump_devices(self):
        """Log every deck track's devices + parameter names, once."""
        try:
            for deck in DECKS:
                track_number = deck + 1
                track = self._deck_track(track_number)
                if track is None:
                    self._diag_log('MixCraft DIAG track %d: missing'
                                   % track_number)
                    continue
                try:
                    devices = list(track.devices)
                except Exception:
                    devices = []
                self._diag_log('MixCraft DIAG track %d (%s): '
                               '%d device(s)'
                               % (track_number,
                                  getattr(track, 'name', '?'),
                                  len(devices)))
                for index, device in enumerate(devices):
                    try:
                        class_name = getattr(device, 'class_name',
                                             None)
                        name = getattr(device, 'name', '?')
                        names = [param.name
                                 for param in device.parameters]
                        self._diag_log(
                            'MixCraft DIAG track %d device %d '
                            'class=%r name=%r params=%s'
                            % (track_number, index, class_name, name,
                               names))
                    except Exception:
                        # One unreadable device must not stop the
                        # rest of the dump.
                        pass
        except Exception:
            pass

    def _diag_eq_result(self, track_number, target, matched_name):
        """Log an EQ lookup once per (track, target): first failure
        and first success (with the parameter name that matched)."""
        try:
            key = (track_number, target)
            if matched_name is None:
                if key in self._diag_eq_failed:
                    return
                self._diag_eq_failed.add(key)
                self._diag_log('MixCraft DIAG no param for %s '
                               'on track %s' % (target, track_number))
            else:
                if key in self._diag_eq_matched:
                    return
                self._diag_eq_matched.add(key)
                self._diag_log('MixCraft DIAG matched %s on track %s '
                               '-> param %r'
                               % (target, track_number, matched_name))
        except Exception:
            pass

    def _diag_transport(self, track_number, target, playing_index,
                        outcome):
        """Log a PLAY/CUE transport action once per (track, target):
        that it reached the adapter, the track's playing_slot_index
        at arrival, and what the handler ultimately did.  Added when
        the CUE button (Note 12) was also reported dead on hardware
        (2026-11-26) — this separates "the action never arrived"
        from "it arrived but found nothing to fire"."""
        try:
            key = (track_number, target)
            if key in self._diag_transport_logged:
                return
            self._diag_transport_logged.add(key)
            self._diag_log('MixCraft DIAG transport %s on track %s: '
                           'playing_slot_index=%s -> %s'
                           % (target, track_number, playing_index,
                              outcome))
        except Exception:
            pass

    def _diag_jog(self, action):
        """Log the first jog per deck: the raw normalised value that
        arrived from dispatch and the delta the bridge computed from
        it (ticks and beats).  Direction confirmation for the sign
        inversion of 2026-11-26 — if the wheel still feels reversed,
        this line shows exactly which way the numbers went."""
        try:
            if action.track in self._diag_jog_logged:
                return
            self._diag_jog_logged.add(action.track)
            normalised = self._diag_jog_raw.get(action.track - 1)
            ticks = None
            try:
                ticks = int(round(
                    action.value / bridge_mod.NUDGE_BEATS_PER_TICK))
            except Exception:
                pass
            self._diag_log('MixCraft DIAG jog on track %s: '
                           'raw normalised=%s -> ticks=%s, '
                           'delta=%s beats'
                           % (action.track, normalised, ticks,
                              action.value))
        except Exception:
            pass

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
        # One-time device dump (LOW-band diagnostics): exactly once
        # per surface lifetime, on the first plan refresh.
        if not self._diag_dumped:
            self._diag_dumped = True
            self._diag_dump_devices()

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
