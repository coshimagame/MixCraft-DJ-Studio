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


class FakeSong:
    def __init__(self, tracks):
        self.tracks = tracks
        self.master_track = FakeTrack('Master')
        self.master_track.mixer_device = FakeMixerDevice(is_master=True)


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


def _install_fake_live():
    framework = types.ModuleType('_Framework')
    cs_module = types.ModuleType('_Framework.ControlSurface')
    cs_module.ControlSurface = FakeControlSurface
    framework.ControlSurface = cs_module
    live_module = types.ModuleType('Live')
    live_module.MidiMap = FakeMidiMap
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
    # Parameter names as the real EQ Three exposes them via the LOM
    # (hardware-verified 2026-11-26).
    return FakeDevice('Eq3', 'EQ Three', [
        FakeParam('GainLow', -15.0, 15.0, 0.0),
        FakeParam('GainMid', -15.0, 15.0, 0.0),
        FakeParam('GainHi', -15.0, 15.0, 0.0)])


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
    # Regression (hardware 2026-11-26): a real EQ Three names its
    # parameters GainLow/GainMid/GainHi.  The script's original
    # 'Low Gain'/'Mid Gain'/'High Gain' spellings matched nothing,
    # so the DDJ-RB EQ knobs silently did nothing on hardware.
    surface, song = _make_surface(eq_factory=_eq_three_lom)
    track = song.tracks[0]
    low, mid, hi = track.devices[1].parameters
    assert (low.name, mid.name, hi.name) == ('GainLow', 'GainMid',
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


def test_tempo_and_sync_are_ignored():
    surface, song = _make_surface()
    before = song.master_track.mixer_device.crossfader.value
    surface.receive_midi(_cc(0, 0, 127))    # TEMPO deck A
    surface.receive_midi(_note(0, 88))      # SYNC deck A
    assert song.master_track.mixer_device.crossfader.value == before
    assert song.tracks[0].clip_slots[0].fire_count == 0


def test_jog_nudges_the_playing_clip():
    surface, song = _make_surface()
    track = song.tracks[0]
    surface.receive_midi(_note(0, 11))      # start playback
    surface.receive_midi(_cc(0, 34, 3))     # jog +3 ticks
    clip = track.clip_slots[0].clip
    assert len(clip.nudge_calls) == 1
    assert abs(clip.nudge_calls[0] - 0.03) < 1e-9
    surface.receive_midi(_cc(0, 34, 127))   # jog -1 tick
    assert abs(clip.nudge_calls[1] + 0.01) < 1e-9


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
