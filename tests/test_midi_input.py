"""Pass 23 (DDJ-RB MIDI input v1): the mapping is the official
DDJ-RB numbers as encoded in midi_map.DDJ_RB; every hardware
gesture is performed on the SAME widgets the mouse drives, so it
takes the identical command path into the engine and lands in
the performance journal. Tests drive a fake backend — no
hardware, no rtmidi needed.

The pure translate() table is tested without Qt; the controller
tests build the real window offscreen (skipped without PySide6).
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

from midi_input import MidiController, led_states, translate


# ------------------------------------------------------------------
# Pure mapping table
# ------------------------------------------------------------------

def test_translate_transport_and_monitor():
    assert translate('note', 0, 11, 127) == ('play', 0)
    assert translate('note', 1, 11, 127) == ('play', 1)
    assert translate('note', 0, 12, 127) == ('cue', 0)
    assert translate('note', 1, 84, 127) == ('monitor', 1)
    # Note-off (velocity 0) never fires.
    assert translate('note', 0, 11, 0) is None


def test_translate_pads_mirror_the_six_app_pads():
    for n in range(6):
        assert translate('note', 7, n, 127) == ('pad', 0, n + 1)
        assert translate('note', 8, n, 127) == ('pad', 1, n + 1)
    # Pads 7-8 are unassigned in v1.
    assert translate('note', 7, 6, 127) is None
    assert translate('note', 8, 7, 127) is None


def test_translate_continuous_controls():
    assert translate('cc', 0, 19, 90) == ('volume', 0, 90)
    assert translate('cc', 1, 4, 64) == ('knob', 1, 'gain', 64)
    assert translate('cc', 0, 7, 1) == ('knob', 0, 'eq_high', 1)
    assert translate('cc', 1, 11, 1) == ('knob', 1, 'eq_mid', 1)
    assert translate('cc', 0, 15, 1) == ('knob', 0, 'eq_low', 1)
    assert translate('cc', 6, 23, 1) == ('knob', 0, 'filter', 1)
    assert translate('cc', 6, 24, 1) == ('knob', 1, 'filter', 1)
    assert translate('cc', 6, 31, 1) == ('cross', 1)
    assert translate('cc', 6, 8, 1) == ('master', 1)
    assert translate('cc', 0, 0, 127) == ('tempo', 0, 127)
    assert translate('cc', 1, 5, 0) == ('tempo', 1, 0)


def test_translate_jog_is_relative():
    assert translate('cc', 0, 34, 3) == ('jog', 0, 3)
    assert translate('cc', 1, 33, 125) == ('jog', 1, -3)
    assert translate('cc', 0, 34, 0) is None


def test_translate_unassigned_in_v1():
    # Still unassigned: BROWSE PUSH (no honest use yet), the
    # RB's SYNC and loop buttons. BROWSE turn and LOAD left
    # this list in pass 32 — see test_pass32_browse_load.py.
    assert translate('note', 6, 65, 127) is None
    assert translate('note', 0, 88, 127) is None
    assert translate('note', 0, 16, 127) is None
    assert translate('note', 3, 11, 127) is None
    assert translate('aftertouch', 0, 1, 1) is None


def test_led_states():
    states = led_states([True, False], 1)
    assert (0, 11, True) in states
    assert (1, 11, False) in states
    assert (0, 84, False) in states
    assert (1, 84, True) in states
    assert led_states([False, False], None) == [
        (0, 11, False), (0, 84, False), (1, 11, False), (1, 84, False)]


# ------------------------------------------------------------------
# Controller against the real window, with a fake backend
# ------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False

pytestmark = pytest.mark.skipif(not HAVE_QT,
                                reason='PySide6 unavailable')


class FakePort:
    def __init__(self):
        self.sent = []
        self.closed = False

    def close(self):
        self.closed = True


class FakeBackend:
    available = True

    def __init__(self, inputs=('DDJ-RB',), outputs=('DDJ-RB',)):
        self._inputs = list(inputs)
        self._outputs = list(outputs)
        self.callback = None
        self.out_port = None
        self.scans = 0

    def input_names(self):
        self.scans += 1
        return list(self._inputs)

    def output_names(self):
        return list(self._outputs)

    def open_input(self, name, callback):
        self.callback = callback
        return FakePort()

    def open_output(self, name):
        self.out_port = FakePort()
        return self.out_port

    def send_note(self, port, channel, note, velocity):
        port.sent.append((channel, note, velocity))

    def inject(self, message):
        self.callback(message)


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
    yield window, backend, ctl
    ctl.close()
    window.monitor_deck = None


def _journal_actions(window):
    window.engine.apply_pending()
    window.journal.drain(window.engine)
    return [(e['action'], e['deck']) for e in window.journal.history]


def test_connect_announces_and_retries(rig):
    window, backend, ctl = rig
    ctl.poll(now=100.0)
    assert ctl.connected
    assert window._status_fn(window.lang) == 'DDJ-RB 接続済み'
    # A missing device stays disconnected and the first scan says so.
    backend2 = FakeBackend(inputs=('Some Other Device',))
    ctl2 = MidiController(window, backend=backend2)
    ctl2.poll(now=100.0)
    assert not ctl2.connected
    assert window._status_fn(window.lang) == 'DDJ-RB 未接続'
    scans = backend2.scans
    ctl2.poll(now=101.0)          # inside the retry interval: no rescan
    assert backend2.scans == scans
    ctl2.poll(now=104.0)          # past it: rescans
    assert backend2.scans > scans
    ctl2.close()


def test_hardware_play_lands_in_the_journal(rig):
    window, backend, ctl = rig
    ctl.poll(now=100.0)
    window.journal.history.clear()
    backend.inject(('note', 0, 11, 127))
    ctl.poll(now=100.1)
    assert ('play', 0) in _journal_actions(window)


def test_hardware_knob_moves_the_widget_and_journals(rig):
    window, backend, ctl = rig
    ctl.poll(now=100.0)
    window.journal.history.clear()
    backend.inject(('cc', 0, 4, 127))     # TRIM -> GAIN full
    ctl.poll(now=100.1)
    assert window.decks[0].controls['gain'].value() == 100
    actions = _journal_actions(window)
    assert ('gain', 0) in actions
    gain_events = [e for e in window.journal.history
                   if e['action'] == 'gain' and e['deck'] == 0]
    assert gain_events[-1]['value'] == pytest.approx(2.0)


def test_hardware_pads_and_monitor(rig):
    window, backend, ctl = rig
    ctl.poll(now=100.0)
    window.journal.history.clear()
    backend.inject(('note', 7, 0, 127))   # pad 1 = cue set
    backend.inject(('note', 8, 6, 127))   # pad 7 = unassigned
    ctl.poll(now=100.1)
    actions = _journal_actions(window)
    assert ('cue_set', 0) in actions
    assert len(actions) == 1
    backend.inject(('note', 1, 84, 127))  # headphone CUE 2
    ctl.poll(now=100.2)
    assert window.monitor_deck == 1
    # Pass 28: each CUE button toggles its OWN deck only — deck 1
    # joins while deck 2 stays monitored (both on at once).
    backend.inject(('note', 0, 84, 127))  # headphone CUE 1
    ctl.poll(now=100.25)
    assert window.monitor_decks == {0, 1}
    backend.inject(('note', 0, 84, 127))  # re-press clears deck 1 only
    ctl.poll(now=100.28)
    assert window.monitor_decks == {1}
    backend.inject(('note', 1, 84, 127))  # re-press clears deck 2
    ctl.poll(now=100.3)
    assert window.monitor_deck is None


def test_leds_follow_state(rig):
    window, backend, ctl = rig
    ctl.poll(now=100.0)
    window.engine.decks[0]['playing'] = True
    window.monitor_deck = 1
    ctl.poll(now=100.1)
    sent = backend.out_port.sent
    assert (0, 11, 127) in sent       # PLAY A lit
    assert (1, 11, 0) in sent         # PLAY B dark
    assert (1, 84, 127) in sent       # monitor B lit
    window.engine.decks[0]['playing'] = False
    window.monitor_deck = None


def test_tempo_fader_moves_the_master_set_bpm(rig):
    # Pass 26 replaced the pass-23 per-deck rate offset (the old
    # test's ±8% semantics): the TEMPO faders now move the master
    # SET BPM — ±6 BPM around the captured base, detent restores
    # it exactly. Full matrix in test_set_bpm_follow.py.
    window, backend, ctl = rig
    ctl.poll(now=100.0)
    previous = window.deck_a_bpm
    window.deck_a_bpm = 120.0
    window.refresh_set_bpm_dial()
    window.engine.apply_pending()
    window.journal.history.clear()
    backend.inject(('cc', 0, 0, 127))     # full up: SET BPM +6
    ctl.poll(now=100.1)
    _journal_actions(window)
    moves = [e['value'] for e in window.journal.history
             if e['action'] == 'set_bpm']
    assert moves[-1] == 126.0
    backend.inject(('cc', 0, 0, 64))      # back to the detent
    ctl.poll(now=100.2)
    _journal_actions(window)
    moves = [e['value'] for e in window.journal.history
             if e['action'] == 'set_bpm']
    assert moves[-1] == 120.0
    window.deck_a_bpm = previous
    window.refresh_set_bpm_dial()


def test_jog_seeks_the_loaded_deck(rig):
    window, backend, ctl = rig
    ctl.poll(now=100.0)
    stub = type('Stub', (), {})()
    stub.path = 'stub.wav'
    stub.audio = np.zeros((48000 * 10, 2), dtype=np.float32)
    window.engine.command('load', 0, stub)
    window.engine.command('seek', 0, 48000 * 5)   # park mid-track
    window.engine.apply_pending()
    window.journal.history.clear()
    # Pass 24: the jog direction is INVERTED vs pass 23 (the v1
    # sign felt backwards at the platter — the user's hardware
    # report), so a positive jog delta moves BACKWARD from here.
    # Pass 28: the gesture lands as an engine 'jog' DELTA of
    # 1 ms per tick (was an absolute 5 ms seek), and the engine
    # applies it to the live position.
    backend.inject(('cc', 0, 34, 10))     # +10 ticks
    ctl.poll(now=100.1)
    _journal_actions(window)
    jogs = [e for e in window.journal.history
            if e['action'] == 'jog' and e['deck'] == 0]
    assert jogs and jogs[-1]['value'] == -10 * (48000 // 1000)
    assert window.engine.decks[0]['position'] == \
        48000 * 5 - 10 * (48000 // 1000)
    backend.inject(('cc', 0, 33, 118))    # -10 ticks (raw 118 = -10)
    ctl.poll(now=100.2)
    _journal_actions(window)
    jogs = [e for e in window.journal.history
            if e['action'] == 'jog' and e['deck'] == 0]
    assert jogs[-1]['value'] == 10 * (48000 // 1000)
    assert window.engine.decks[0]['position'] == 48000 * 5
    window.engine.command('load', 0, stub)  # leave deck as found-ish
