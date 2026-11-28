"""Pass 26 (SET BPM follow, user 2026-11-28: 「setbpmはスライダーで
変えたら追随してほしい」): the DDJ-RB TEMPO faders and the SET BPM
dial move the master SET BPM — ±6 BPM around the base, 0.1 steps,
last-moved wins, detent restores the base exactly — and every deck
rate follows proportionally, through the journal, into the .als
replay's Master Tempo envelope.

Engine/replay tests are Qt-free; the controller and dial tests
build the real window offscreen (skipped without PySide6), with
the same fake-backend pattern as test_midi_input.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

from engine import Engine, RATE, Track
import performance


def make_track(bpm, offset=0, conf=0.9, seconds=30, name='t.wav'):
    frames = int(seconds * RATE)
    return Track(name, np.zeros((frames, 2), dtype='float32'),
                 np.ones(16, dtype='float32'), bpm, 1.0,
                 beat_offset=offset, beat_confidence=conf)


def drain(e):
    out = []
    while not e.events.empty():
        out.append(e.events.get())
    return out


# ------------------------------------------------------------------
# Engine: anchor and master move
# ------------------------------------------------------------------

def test_anchor_adopts_without_scaling():
    e = Engine()
    e.command('load', 0, make_track(120.0, name='a.wav'))
    e.command('tempo', 0, 0.9)
    e.command('set_bpm_anchor', None, 120.0)
    e.apply_pending()
    assert e.set_bpm == 120.0
    assert e.decks[0]['rate'] == pytest.approx(0.9)
    events = drain(e)
    assert [ev['action'] for ev in events].count('tempo') == 1


def test_master_move_scales_every_deck_and_journals_tempo():
    e = Engine()
    e.command('load', 0, make_track(120.0, name='a.wav'))
    e.command('load', 1, make_track(124.0, name='b.wav'))
    e.command('tempo', 1, 0.9)
    e.command('set_bpm_anchor', None, 120.0)
    e.apply_pending()
    drain(e)
    e.command('set_bpm', None, 126.0)
    e.apply_pending()
    assert e.set_bpm == 126.0
    assert e.decks[0]['rate'] == pytest.approx(1.05)
    assert e.decks[1]['rate'] == pytest.approx(0.9 * 1.05)
    events = drain(e)
    assert events[0]['action'] == 'set_bpm'
    assert events[0]['deck'] is None
    assert events[0]['value'] == 126.0
    tempos = {ev['deck']: ev['value'] for ev in events[1:]
              if ev['action'] == 'tempo'}
    assert tempos[0] == pytest.approx(1.05)
    assert tempos[1] == pytest.approx(0.9 * 1.05)


def test_master_move_without_anchor_just_adopts():
    e = Engine()
    e.command('load', 0, make_track(120.0, name='a.wav'))
    e.command('set_bpm', None, 120.0)
    e.apply_pending()
    assert e.set_bpm == 120.0
    assert e.decks[0]['rate'] == 1.0


def test_synced_decks_follow_a_master_move_in_phase():
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, name='a.wav'))
    e.command('load', 1, make_track(124.0, 9000, name='b.wav'))
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.command('sync', 1, True)
    e.command('set_bpm_anchor', None, 120.0)
    e.apply_pending()
    ratio = e.decks[1]['rate'] / e.decks[0]['rate']
    e.command('set_bpm', None, 123.0)
    e.apply_pending()
    for _ in range(4):
        e.render(512)
    assert e.decks[1]['rate'] / e.decks[0]['rate'] == \
        pytest.approx(ratio)
    ds, dm = e.decks[1], e.decks[0]
    err = (((dm['position'] - 0) / (RATE * 60 / 120.0))
           - ((ds['position'] - 9000) / (RATE * 60 / 124.0))) % 1.0
    assert min(err, 1.0 - err) * 60.0 / 124.0 < 0.030


# ------------------------------------------------------------------
# Replay: the Master Tempo envelope follows
# ------------------------------------------------------------------

def test_replay_set_bpm_series_and_tempo_envelope():
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, name='a.wav'))
    e.command('load', 1, make_track(124.0, 9000, name='b.wav'))
    e.command('set_bpm_anchor', None, 120.0)
    e.command('play', 0, True); e.command('play', 1, True)
    e.render(48000)                       # 1 s in
    e.command('set_bpm', None, 126.0)
    e.apply_pending()
    e.render(48000)
    e.command('cross', value=0.5)         # pin the journal's end
    e.apply_pending()
    events = drain(e)
    recon = performance.reconstruct(
        events,
        durations={'a.wav': 30.0, 'b.wav': 30.0},
        bpms={'a.wav': 120.0, 'b.wav': 124.0},
        set_bpm=120.0)
    assert recon['set_bpm_series'] == [(0.0, 120.0), (48000.0, 126.0)]
    rates = [seg['rate'] for seg in recon['segments'][0]]
    assert any(r == pytest.approx(1.0) for r in rates)
    assert any(r == pytest.approx(1.05) for r in rates)
    meta = {p: {'bpm': b, 'name': p, 'duration_seconds': 30.0}
            for p, b in (('a.wav', 120.0), ('b.wav', 124.0))}
    plan = performance.build_perf_plan(recon, 120.0, meta)
    tempo = plan['envelopes']['tempo']
    assert tempo[0] == (0.0, 120.0)
    assert (2.0, 126.0) in tempo          # frame 48000 = beat 2.0


def test_replay_without_moves_keeps_constant_tempo():
    events = [
        dict(frame=0, action='load', deck=0, value='a.wav'),
        dict(frame=0, action='play', deck=0, value=True),
    ]
    recon = performance.reconstruct(
        events, durations={'a.wav': 30.0}, bpms={'a.wav': 120.0},
        set_bpm=120.0)
    meta = {'a.wav': {'bpm': 120.0, 'name': 'a',
                      'duration_seconds': 30.0}}
    plan = performance.build_perf_plan(recon, 120.0, meta)
    assert {v for _, v in plan['envelopes']['tempo']} == {120.0}


# ------------------------------------------------------------------
# Window / MIDI controller / dial (offscreen, fake backend)
# ------------------------------------------------------------------

try:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    import app as appmod
    from midi_input import MidiController
    HAVE_QT = True
except Exception:
    HAVE_QT = False

pytestmark = pytest.mark.skipif(not HAVE_QT,
                                reason='PySide6 unavailable')


class FakePort:
    def close(self):
        pass


class FakeBackend:
    available = True

    def __init__(self):
        self.callback = None

    def input_names(self):
        return ['DDJ-RB']

    def output_names(self):
        return ['DDJ-RB']

    def open_input(self, name, callback):
        self.callback = callback
        return FakePort()

    def open_output(self, name):
        return FakePort()

    def send_note(self, port, channel, note, velocity):
        pass


@pytest.fixture(scope='module')
def window():
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()


@pytest.fixture()
def rig(window):
    backend = FakeBackend()
    ctl = MidiController(window, backend=backend)
    window.midi = ctl
    window.deck_a_bpm = 120.0
    window.refresh_set_bpm_dial()
    window.engine.apply_pending()
    yield window, ctl
    ctl.close()
    # Restore the neutral base for the next test.
    window.deck_a_bpm = None
    window.setlist_data = None
    window.refresh_set_bpm_dial()


def test_window_anchor_and_dial_follow(rig):
    window, ctl = rig
    assert window.set_bpm_now == 120.0
    assert window.engine.set_bpm == 120.0
    assert window.set_dial.bpm == 120.0
    window.set_set_bpm(122.4)
    assert window.set_bpm_now == 122.4
    window.engine.apply_pending()
    assert window.engine.set_bpm == 122.4
    # Clamped to the base ±6, quantized to 0.1.
    window.set_set_bpm(140.0)
    assert window.set_bpm_now == 126.0
    window.set_set_bpm(100.03)
    assert window.set_bpm_now == 114.0


def test_tempo_fader_moves_set_bpm_and_detent_restores(rig):
    window, ctl = rig
    ctl.dispatch(('cc', 0, 0, 127))          # deck A fader full up
    assert window.set_bpm_now == 126.0
    ctl.dispatch(('cc', 0, 0, 96))           # part-way down
    assert window.set_bpm_now == 123.0
    ctl.dispatch(('cc', 0, 0, 64))           # detent: exact base
    assert window.set_bpm_now == 120.0
    window.engine.apply_pending()
    assert window.engine.set_bpm == 120.0
    actions = [(e['action'], e['value'])
               for e in _drain(window.engine)]
    assert ('set_bpm', 126.0) in actions
    assert ('set_bpm', 120.0) in actions


def test_tempo_fader_last_moved_wins_and_stale_base_rearms(rig):
    window, ctl = rig
    ctl.dispatch(('cc', 0, 0, 127))          # A takes it to 126
    assert window.set_bpm_now == 126.0
    ctl.dispatch(('cc', 1, 5, 0))            # B captures 126, full down
    assert window.set_bpm_now == 120.0
    ctl.dispatch(('cc', 1, 5, 64))           # B detent: its base, 126
    assert window.set_bpm_now == 126.0
    # The dial moves it behind fader A's back; A's detent must
    # NOT stomp the newer value with its stale baseline.
    window.set_set_bpm(121.0)
    ctl.dispatch(('cc', 0, 0, 64))
    assert window.set_bpm_now == 121.0
    # And A's next real move re-captures around the current value.
    ctl.dispatch(('cc', 0, 0, 127))
    assert window.set_bpm_now == 126.0       # 121 + 6, clamped


def test_tempo_fader_inert_without_a_set_bpm_source(window):
    window.deck_a_bpm = None
    window.setlist_data = None
    window.refresh_set_bpm_dial()
    assert window.set_bpm_now is None
    ctl = MidiController(window, backend=FakeBackend())
    ctl.dispatch(('cc', 0, 0, 127))
    assert window.set_bpm_now is None
    ctl.close()


def _drain(engine):
    out = []
    while not engine.events.empty():
        out.append(engine.events.get())
    return out


def test_dial_drag_moves_set_bpm(rig):
    window, ctl = rig
    dial = window.set_dial
    assert dial.bpm == 120.0
    press = QMouseEvent(QEvent.Type.MouseButtonPress,
                        QPointF(74, 100), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    dial.mousePressEvent(press)
    move = QMouseEvent(QEvent.Type.MouseMove,
                       QPointF(74, 60), Qt.MouseButton.NoButton,
                       Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    dial.mouseMoveEvent(move)                # 40 px up = +2.0 BPM
    assert window.set_bpm_now == 122.0
    dial.mouseReleaseEvent(press)
