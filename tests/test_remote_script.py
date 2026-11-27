"""Remote Script adapter tests with a fake Live API (stdlib only).

No real Live exists in CI, so fake ``Live`` / ``_Framework`` modules
are injected into sys.modules and the MixCraft package is imported
from remote_script/.  The tests drive the real receive path —
MIDI bytes -> midi_map.dispatch -> live_bridge plan -> Live object
calls — and assert on the fake objects' recorded state.
"""
import sys
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# Fake Live API
# ---------------------------------------------------------------------------


class FakeParam:
    def __init__(self, name, minimum=0.0, maximum=1.0, value=0.0):
        self.name = name
        self.min = minimum
        self.max = maximum
        self.value = value


class FakeDevice:
    def __init__(self, class_name, name, params):
        self.class_name = class_name
        self.name = name
        self.parameters = params


class FakeClip:
    def __init__(self, name):
        self.name = name
        self.is_playing = False
        self.looping = False
        self.loop_start = 0.0
        self.loop_end = 0.0
        self.start_marker = 0.0
        self.length = 0.0
        self.playing_position = 0.0
        self.nudge_calls = []

    def move_playing_pos(self, beats):
        self.nudge_calls.append(beats)


class FakeClipSlot:
    def __init__(self, track, clip=None):
        self._track = track
        self.clip = clip
        self.fire_count = 0

    @property
    def has_clip(self):
        return self.clip is not None

    def fire(self):
        self.fire_count += 1
        if self.clip is not None:
            for slot in self._track.clip_slots:
                if slot.clip is not None:
                    slot.clip.is_playing = slot is self
            self.clip.is_playing = True


class FakeMixerDevice:
    def __init__(self, is_master=False):
        self.volume = FakeParam('Volume', 0.0, 1.0, 0.85)
        if is_master:
            self.crossfader = FakeParam('Crossfader', -1.0, 1.0, 0.0)


class FakeTrack:
    def __init__(self, name, clips=(), devices=()):
        self.name = name
        self.clip_slots = [FakeClipSlot(self, c) for c in clips]
        self.devices = list(devices)
        self.mixer_device = FakeMixerDevice()
        self.solo = False
        self.stop_all_calls = 0

    @property
    def playing_slot_index(self):
        for i, slot in enumerate(self.clip_slots):
            if slot.clip is not None and slot.clip.is_playing:
                return i
        return -1

    def stop_all_clips(self):
        self.stop_all_calls += 1
        for slot in self.clip_slots:
            if slot.clip is not None:
                slot.clip.is_playing = False


class FakeView:
    """Live's Song.View: the session highlight the browse knob moves."""

    def __init__(self):
        self.highlighted_clip_slot = None
        self.selected_track = None


class FakeSong:
    def __init__(self, tracks):
        self.tracks = tracks
        self.master_track = FakeTrack('Master')
        self.master_track.mixer_device = FakeMixerDevice(is_master=True)
        # Live.Song.clip_trigger_quantization, holding the fake enum
        # values from FakeQuantization below.
        self.clip_trigger_quantization = 'Q_1_BAR'
        self.view = FakeView()
        # Live.Song.tempo: the adapter captures it at load as the
        # TEMPO faders' centre reference (v1.7) and writes it on
        # fader moves.  Writes are recorded so tests can count them
        # (the bridge must write only on a 0.1 BPM change).
        self._tempo = 120.0
        self.tempo_writes = []

    @property
    def tempo(self):
        return self._tempo

    @tempo.setter
    def tempo(self, value):
        self._tempo = value
        self.tempo_writes.append(value)


class FakeCInstance:
    def __init__(self, song):
        self._song = song
        self.log_lines = []

    def log_message(self, *args):
        self.log_lines.append(args)

    def handle(self):
        return 'fake-script-handle'


class FakeControlSurface:
    """Minimal stand-in for _Framework.ControlSurface."""

    def __init__(self, c_instance):
        self._c_instance = c_instance
        self.sent_midi = []
        self.midi_map_builds = []

    def build_midi_map(self, midi_map_handle):
        self.midi_map_builds.append(midi_map_handle)

    def song(self):
        return self._c_instance._song

    def _send_midi(self, midi_bytes):
        self.sent_midi.append(tuple(midi_bytes))
        return True

    def log_message(self, *args):
        self._c_instance.log_message(*args)

    def show_message(self, *args):
        pass

    def update_display(self):
        pass

    def disconnect(self):
        pass


class FakeMidiMap:
    """Stand-in for Live.MidiMap: records every forward_* call."""

    forwarded_notes = []  # (script_handle, map_handle, channel, note)
    forwarded_ccs = []    # (script_handle, map_handle, channel, cc)

    @staticmethod
    def forward_midi_note(script_handle, midi_map_handle, channel, note):
        FakeMidiMap.forwarded_notes.append(
            (script_handle, midi_map_handle, channel, note))

    @staticmethod
    def forward_midi_cc(script_handle, midi_map_handle, channel, cc):
        FakeMidiMap.forwarded_ccs.append(
            (script_handle, midi_map_handle, channel, cc))


class FakeQuantization:
    """Stand-in for Live.Song.Quantization (enum members as sentinels)."""
    q_1_bar = 'Q_1_BAR'
    q_no_q = 'Q_NO_Q'


class FakeSongType:
    Quantization = FakeQuantization


def _install_fake_live():
    framework = types.ModuleType('_Framework')
    cs_module = types.ModuleType('_Framework.ControlSurface')
    cs_module.ControlSurface = FakeControlSurface
    framework.ControlSurface = cs_module
    live_module = types.ModuleType('Live')
    live_module.MidiMap = FakeMidiMap
    live_module.Song = FakeSongType
    sys.modules.setdefault('_Framework', framework)
    sys.modules.setdefault('_Framework.ControlSurface', cs_module)
    sys.modules.setdefault('Live', live_module)


_install_fake_live()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / 'remote_script'))

import MixCraft as mixcraft_package  # noqa: E402
from MixCraft.MixCraft import MixCraft  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _eq_three():
    return FakeDevice('Eq3', 'EQ Three', [
        FakeParam('Low Gain', -15.0, 15.0, 0.0),
        FakeParam('Mid Gain', -15.0, 15.0, 0.0),
        FakeParam('High Gain', -15.0, 15.0, 0.0)])


def _eq_three_lom():
    # Device class + parameter names exactly as the real EQ Three
    # exposes them via the LOM — ground truth from the v1.3 device
    # dump in the user's Live 10.1.43 Log.txt (2026-11-26).  Note
    # the low band is 'GainLo', NOT 'GainLow'.
    return FakeDevice('FilterEQ3', 'EQ Three', [
        FakeParam('GainLo', -15.0, 15.0, 0.0),
        FakeParam('GainMid', -15.0, 15.0, 0.0),
        FakeParam('GainHi', -15.0, 15.0, 0.0),
        FakeParam('FreqLo', 30.0, 500.0, 250.0),
        FakeParam('FreqHi', 500.0, 8000.0, 2500.0)])


def _auto_filter():
    return FakeDevice('AutoFilter', 'Auto Filter', [
        FakeParam('Filter Type', 0, 4, 0),
        FakeParam('Frequency', 20.0, 19999.0, 19999.0)])


def _utility():
    return FakeDevice('Utility', 'Utility', [
        FakeParam('Gain', -70.0, 35.0, 0.0)])


def _make_surface(deck_a_clips=('Song A',), deck_b_clips=('Song B',),
                  eq_factory=None):
    if eq_factory is None:
        eq_factory = _eq_three
    devices = [_utility(), eq_factory(), _auto_filter()]
    track_a = FakeTrack('Deck A',
                        clips=[FakeClip(n) if n else None
                               for n in deck_a_clips],
                        devices=list(devices))
    track_b = FakeTrack('Deck B',
                        clips=[FakeClip(n) if n else None
                               for n in deck_b_clips],
                        devices=[_utility(), eq_factory(), _auto_filter()])
    song = FakeSong([track_a, track_b])
    surface = MixCraft(FakeCInstance(song))
    return surface, song


def _cc(channel, number, raw):
    return (0xB0 | channel, number, raw)


def _note(channel, number, velocity=127):
    return (0x90 | channel, number, velocity)


# ---------------------------------------------------------------------------
# Required paths
# ---------------------------------------------------------------------------


def test_create_instance_returns_surface():
    song = FakeSong([FakeTrack('A'), FakeTrack('B')])
    instance = mixcraft_package.create_instance(FakeCInstance(song))
    assert isinstance(instance, MixCraft)


def test_build_midi_map_forwards_all_ddj_rb_channels():
    # Regression test for the v1 real-hardware failure: Live only
    # delivers MIDI to receive_midi() for messages forwarded in
    # build_midi_map(); v1 forwarded nothing and heard nothing.
    surface, song = _make_surface()
    FakeMidiMap.forwarded_notes.clear()
    FakeMidiMap.forwarded_ccs.clear()
    surface.build_midi_map('fake-map-handle')
    # The parent's component map build ran, with the handle passed on.
    assert surface.midi_map_builds == ['fake-map-handle']
    notes = {(ch, n) for (_s, _m, ch, n) in FakeMidiMap.forwarded_notes}
    ccs = {(ch, c) for (_s, _m, ch, c) in FakeMidiMap.forwarded_ccs}
    # Spot-check the profile channels at both range boundaries.
    for channel in (0, 1, 6, 7, 8):
        assert (channel, 0) in notes and (channel, 127) in notes
        assert (channel, 0) in ccs and (channel, 127) in ccs
    # Full ranges on exactly the seven DDJ-RB channels, nothing else.
    assert {ch for (ch, _n) in notes} == {0, 1, 4, 5, 6, 7, 8}
    assert len(FakeMidiMap.forwarded_notes) == 7 * 128
    assert len(FakeMidiMap.forwarded_ccs) == 7 * 128
    # Every forward used this surface's own script handle + map handle.
    assert {(s, m) for (s, m, _c, _n)
            in FakeMidiMap.forwarded_notes} == {('fake-script-handle',
                                                 'fake-map-handle')}
    assert {(s, m) for (s, m, _c, _c2)
            in FakeMidiMap.forwarded_ccs} == {('fake-script-handle',
                                               'fake-map-handle')}


def test_crossfader_cc_drives_master_crossfader():
    surface, song = _make_surface()
    param = song.master_track.mixer_device.crossfader
    surface.receive_midi(_cc(6, 31, 0))     # global ch, CC 31, full A
    assert param.value == -1.0
    surface.receive_midi(_cc(6, 31, 127))   # full B
    assert param.value == 1.0
    surface.receive_midi(_cc(6, 31, 64))    # centre
    assert abs(param.value) < 0.02


def test_play_note_fires_then_stops_the_deck_clip():
    surface, song = _make_surface()
    track = song.tracks[0]
    surface.receive_midi(_note(0, 11))      # PLAY deck 1
    assert track.clip_slots[0].fire_count == 1
    assert track.playing_slot_index == 0
    surface.receive_midi(_note(0, 11))      # PLAY again -> stop
    assert track.stop_all_calls == 1
    assert track.playing_slot_index == -1
    surface.receive_midi(_note(0, 11, 0))   # note-off: no-op
    assert track.clip_slots[0].fire_count == 1


def test_filter_cc_selects_type_and_sweeps_frequency():
    surface, song = _make_surface()
    device = song.tracks[0].devices[2]
    type_param, freq_param = device.parameters
    surface.receive_midi(_cc(6, 23, 0))     # FILTER deck A, full left
    assert type_param.value == 0            # lowpass
    assert abs(freq_param.value - freq_param.min) < 0.01
    surface.receive_midi(_cc(6, 23, 127))   # full right
    assert type_param.value == 1            # highpass
    assert abs(freq_param.value - freq_param.max) < 0.01
    surface.receive_midi(_cc(6, 23, 63))    # just left of centre: LP, open
    assert type_param.value == 0
    assert freq_param.value > 0.9 * freq_param.max


def test_hotcue_pad_fires_the_planned_clip():
    surface, song = _make_surface(deck_a_clips=('Intro', 'Drop', 'Break'))
    track = song.tracks[0]
    surface.receive_midi(_note(7, 2))       # pad ch (0-based 7), pad 3
    assert track.clip_slots[2].fire_count == 1
    assert track.clip_slots[2].clip.name == 'Break'
    surface.receive_midi(_note(8, 0))       # deck B pad 1
    assert song.tracks[1].clip_slots[0].fire_count == 1
    surface.receive_midi(_note(7, 7))       # pad 8: no clip -> nothing
    assert sum(s.fire_count for s in track.clip_slots) == 1


# ---------------------------------------------------------------------------
# Supporting behaviour
# ---------------------------------------------------------------------------


def test_channel_fader_trim_and_eq_reach_device_params():
    surface, song = _make_surface()
    track = song.tracks[0]
    surface.receive_midi(_cc(0, 19, 127))   # channel fader deck A
    assert track.mixer_device.volume.value == 1.0
    surface.receive_midi(_cc(0, 4, 127))    # TRIM -> Utility Gain max
    assert track.devices[0].parameters[0].value == 35.0
    surface.receive_midi(_cc(0, 7, 127))    # EQ HI -> +15 dB
    hi = track.devices[1].parameters[2]
    assert hi.name == 'High Gain' and hi.value == 15.0
    surface.receive_midi(_cc(0, 7, 0))
    assert hi.value == -15.0


def test_eq_knobs_drive_lom_named_eq_three_params():
    # Regression (hardware 2026-11-26): a real EQ Three (LOM class
    # 'FilterEQ3') names its parameters GainLo/GainMid/GainHi.  The
    # script's original 'Low Gain'/'Mid Gain'/'High Gain' spellings
    # matched nothing, and v1.2's 'GainLow' still missed the low
    # band by one letter — the LOW knob stayed dead until the real
    # names were dumped from Live's own Log.txt.
    surface, song = _make_surface(eq_factory=_eq_three_lom)
    track = song.tracks[0]
    low, mid, hi = track.devices[1].parameters[:3]
    assert (low.name, mid.name, hi.name) == ('GainLo', 'GainMid',
                                            'GainHi')
    surface.receive_midi(_cc(0, 15, 127))   # EQ LOW deck A -> max
    surface.receive_midi(_cc(0, 11, 127))   # EQ MID deck A -> max
    surface.receive_midi(_cc(0, 7, 127))    # EQ HI  deck A -> max
    assert low.value == 15.0 and mid.value == 15.0 and hi.value == 15.0
    surface.receive_midi(_cc(0, 15, 0))
    surface.receive_midi(_cc(0, 11, 0))
    surface.receive_midi(_cc(0, 7, 0))
    assert low.value == -15.0 and mid.value == -15.0
    assert hi.value == -15.0
    surface.receive_midi(_cc(0, 11, 64))    # centre detent ~ flat
    assert abs(mid.value) < 0.25
    # Deck B speaks the same names.
    track_b = song.tracks[1]
    surface.receive_midi(_cc(1, 7, 127))    # EQ HI deck B -> max
    assert track_b.devices[1].parameters[2].value == 15.0


def test_sync_is_ignored():
    surface, song = _make_surface()
    surface.receive_midi(_note(0, 88))      # SYNC deck A
    assert song.tracks[0].clip_slots[0].fire_count == 0
    assert song.tempo_writes == []


# ---------------------------------------------------------------------------
# Tempo faders (v1.7): both decks drive the song tempo
# ---------------------------------------------------------------------------


def test_tempo_faders_drive_the_song_tempo():
    surface, song = _make_surface()
    # The reference is the song tempo at load (120.0 in the fake);
    # a fader parked at its centre detent (raw 63/64) writes nothing.
    surface.receive_midi(_cc(0, 0, 64))     # TEMPO deck A, detent
    surface.receive_midi(_cc(0, 0, 63))
    assert song.tempo == 120.0 and song.tempo_writes == []
    # Deck A to the top of the ±6 BPM range, then back to centre.
    surface.receive_midi(_cc(0, 0, 127))
    assert song.tempo == 126.0
    surface.receive_midi(_cc(0, 0, 64))
    assert song.tempo == 120.0
    # Deck B's slider (CC 5 on ch 1) drives the same tempo:
    # last-moved wins, here to the bottom of the range.
    surface.receive_midi(_cc(1, 5, 0))
    assert song.tempo == 114.0
    assert song.tempo_writes == [126.0, 120.0, 114.0]


def test_tempo_fader_writes_only_on_a_tenth_change():
    surface, song = _make_surface()
    surface.receive_midi(_cc(0, 0, 65))     # one count up: 120.1
    assert song.tempo_writes == [120.1]
    # The same position again quantises to the same value, so the
    # adapter writes nothing (within-step positions are covered at
    # bridge level in test_live_bridge.py).
    surface.receive_midi(_cc(0, 0, 65))
    assert song.tempo_writes == [120.1]


def test_jog_nudges_the_playing_clip():
    surface, song = _make_surface()
    track = song.tracks[0]
    surface.receive_midi(_note(0, 11))      # start playback
    # Sign inverted (hardware 2026-11-26) and step is 0.005 beats:
    # raw 3 -> -3 ticks -> -0.015 beats.
    surface.receive_midi(_cc(0, 34, 3))
    clip = track.clip_slots[0].clip
    assert len(clip.nudge_calls) == 1
    assert abs(clip.nudge_calls[0] - -0.015) < 1e-9
    surface.receive_midi(_cc(0, 34, 127))   # raw 127 -> +1 tick
    assert abs(clip.nudge_calls[1] - 0.005) < 1e-9


def test_led_feedback_follows_play_state():
    surface, song = _make_surface()
    surface.receive_midi(_note(0, 11))      # deck A plays
    surface.update_display()
    sent = surface.sent_midi
    assert (0x90, 11, 127) in sent          # PLAY A lit
    assert (0x91, 11, 0) in sent            # PLAY B dark
    assert (0x97, 0, 127) in sent           # pad A1 lit (clip present)
    surface.receive_midi(_note(0, 11))      # stop deck A
    surface.update_display()
    assert (0x90, 11, 0) in surface.sent_midi


def test_missing_devices_are_skipped_silently():
    song = FakeSong([FakeTrack('Bare A', clips=[FakeClip('X')]),
                     FakeTrack('Bare B')])
    surface = MixCraft(FakeCInstance(song))
    # No Utility / EQ / Filter devices: nothing may raise.
    surface.receive_midi(_cc(0, 7, 100))
    surface.receive_midi(_cc(0, 4, 100))
    surface.receive_midi(_cc(6, 23, 100))
    surface.receive_midi(_cc(0, 34, 5))     # jog with nothing playing



# ---------------------------------------------------------------------------
# Headphone cue buttons (v1.4, hardware probe 2026-11-26)
# ---------------------------------------------------------------------------


def test_hp_cue_note_toggles_track_solo():
    # The RB's HEADPHONES CUE 1/2 buttons send Note 84 on the deck
    # channels (probed via Live's map mode).  Each press toggles the
    # deck track's solo — which Live treats as Cue when the set's
    # Cue Out is a separate output.
    surface, song = _make_surface()
    track_a, track_b = song.tracks
    assert track_a.solo is False and track_b.solo is False
    surface.receive_midi(_note(0, 84))      # HP CUE 1 -> on
    assert track_a.solo is True
    assert track_b.solo is False
    surface.receive_midi(_note(0, 84))      # again -> off
    assert track_a.solo is False
    surface.receive_midi(_note(1, 84))      # HP CUE 2 -> on
    assert track_b.solo is True
    surface.receive_midi(_note(1, 84, 0))   # note-off: no-op
    assert track_b.solo is True


def test_hp_cue_led_follows_solo_state():
    surface, song = _make_surface()
    surface.receive_midi(_note(0, 84))      # cue deck A on
    surface.update_display()
    assert (0x90, 84, 127) in surface.sent_midi   # CUE 1 LED lit
    assert (0x91, 84, 0) in surface.sent_midi     # CUE 2 LED dark
    surface.receive_midi(_note(0, 84))      # cue deck A off again
    surface.update_display()
    assert (0x90, 84, 0) in surface.sent_midi


# ---------------------------------------------------------------------------
# Performance pads (v1.5): CUE SET / QUANTIZE / LOOP IN / LOOP OUT
# ---------------------------------------------------------------------------


def test_pad_cue_set_writes_the_playing_clips_start_marker():
    surface, song = _make_surface()
    track = song.tracks[0]
    surface.receive_midi(_note(0, 11))      # PLAY deck A
    clip = track.clip_slots[0].clip
    clip.playing_position = 12.3
    surface.receive_midi(_note(7, 4))       # pad 5 = CUE SET
    # Quantize starts on (Live's 1-bar default): snapped to the beat.
    assert clip.start_marker == 12.0
    surface.receive_midi(_note(7, 5))       # pad 6 = QUANTIZE off
    clip.playing_position = 20.3
    surface.receive_midi(_note(7, 4))
    assert clip.start_marker == 20.3


def test_pad_quantize_toggles_the_song_launch_quantization():
    surface, song = _make_surface()
    assert song.clip_trigger_quantization == 'Q_1_BAR'
    surface.receive_midi(_note(7, 5))       # pad 6 on deck A
    assert song.clip_trigger_quantization == 'Q_NO_Q'
    surface.receive_midi(_note(8, 5))       # pad 6 on deck B: same switch
    assert song.clip_trigger_quantization == 'Q_1_BAR'


def test_pad_loop_in_out_cycle_on_the_playing_clip():
    surface, song = _make_surface()
    track = song.tracks[0]
    surface.receive_midi(_note(0, 11))      # PLAY deck A
    clip = track.clip_slots[0].clip
    clip.length = 64.0
    clip.playing_position = 8.0
    surface.receive_midi(_note(7, 6))       # pad 7 = LOOP IN
    assert clip.loop_start == 8.0
    assert clip.loop_end == 12.0            # provisional +4 beats
    assert clip.looping is True
    clip.playing_position = 16.0
    surface.receive_midi(_note(7, 7))       # pad 8 = LOOP OUT
    assert clip.loop_end == 16.0
    assert clip.looping is True
    surface.receive_midi(_note(7, 7))       # pad 8 again = loop off
    assert clip.looping is False


def test_pad_hotcue_fires_the_current_songs_hot_cue_clip():
    surface, song = _make_surface(
        deck_a_clips=('Song 1 - Artist', 'Song 1 – Hot Cue A',
                      'Song 2 - Other'))
    track = song.tracks[0]
    surface.receive_midi(_note(0, 11))      # PLAY: Song 1's main clip
    assert track.clip_slots[0].fire_count == 1
    surface.receive_midi(_note(7, 0))       # pad 1 = Hot Cue A
    assert track.clip_slots[1].fire_count == 1
    surface.update_display()
    assert (0x97, 0, 127) in surface.sent_midi  # pad 1 lit while it plays
    # Song 1 has no Hot Cue B clip: pad 2 must not fire Song 2's clip.
    surface.receive_midi(_note(7, 1))
    assert track.clip_slots[2].fire_count == 0


def test_pad_edits_without_a_playing_clip_are_noops():
    surface, song = _make_surface()
    clip = song.tracks[0].clip_slots[0].clip
    surface.receive_midi(_note(7, 4))       # CUE SET, nothing playing
    surface.receive_midi(_note(7, 6))       # LOOP IN
    surface.receive_midi(_note(7, 7))       # LOOP OUT
    assert clip.start_marker == 0.0
    assert clip.looping is False


# ---------------------------------------------------------------------------
# Browse knob (v1.6): turn = session highlight, push = fire
# ---------------------------------------------------------------------------


def test_browse_turn_highlights_and_push_fires():
    surface, song = _make_surface(deck_a_clips=('A1', 'A2', 'A3'),
                                  deck_b_clips=('B1', 'B2'))
    track_a = song.tracks[0]
    # Nothing playing: the initial target is Deck A (sets start
    # there).  One detent clockwise moves the highlight to A2.
    surface.receive_midi(_cc(6, 64, 1))
    assert song.view.highlighted_clip_slot is track_a.clip_slots[1]
    assert song.view.selected_track is track_a
    # Push fires the highlighted clip through the fire_clip path.
    surface.receive_midi(_note(6, 65))
    assert track_a.clip_slots[1].fire_count == 1


def test_browse_targets_the_standby_deck_once_a_plays():
    surface, song = _make_surface(deck_a_clips=('A1', 'A2'),
                                  deck_b_clips=('B1', 'B2', 'B3'))
    track_b = song.tracks[1]
    surface.receive_midi(_note(0, 11))      # PLAY on Deck A
    surface.update_display()                # playing state reported
    # The knob now browses Deck B (the standby deck).
    surface.receive_midi(_cc(6, 64, 1))
    assert song.view.highlighted_clip_slot is track_b.clip_slots[1]
    assert song.view.selected_track is track_b
    surface.receive_midi(_note(6, 65))      # push fires B2
    assert track_b.clip_slots[1].fire_count == 1


def test_browse_load_button_pins_the_target_deck():
    surface, song = _make_surface(deck_a_clips=('A1', 'A2'),
                                  deck_b_clips=('B1', 'B2'))
    track_a, track_b = song.tracks
    surface.receive_midi(_note(0, 11))      # PLAY on Deck A
    surface.update_display()
    # LOAD 1 (documented note 70) pins Deck A despite A playing:
    # the highlight jumps to A's cursor and LOAD itself fires
    # nothing.
    surface.receive_midi(_note(6, 70))
    assert song.view.highlighted_clip_slot is track_a.clip_slots[0]
    assert track_a.clip_slots[0].fire_count == 1  # from PLAY only
    assert track_b.clip_slots[0].fire_count == 0
    surface.receive_midi(_cc(6, 64, 1))
    assert song.view.highlighted_clip_slot is track_a.clip_slots[1]


def test_unmapped_browser_channel_message_logs_once():
    # The safety net: an unknown control on the browser channel
    # identifies itself in the log — once, not per message.
    surface, song = _make_surface()
    surface.receive_midi(_cc(6, 99, 5))
    surface.receive_midi(_cc(6, 99, 6))
    lines = [' '.join(str(a) for a in entry)
             for entry in surface._c_instance.log_lines]
    hits = [line for line in lines if 'unmapped' in line and '99' in line]
    assert len(hits) == 1
    # Other channels stay silent (the FX channels would flood).
    surface.receive_midi(_cc(4, 99, 5))
    lines = [' '.join(str(a) for a in entry)
             for entry in surface._c_instance.log_lines]
    assert len([line for line in lines if 'unmapped' in line]) == 1
