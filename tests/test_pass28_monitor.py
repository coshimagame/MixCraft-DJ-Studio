"""Pass 28 (user report 2026-11-28: monitor must work on both
decks at once): the per-deck monitor state's outward faces.

The toggle semantics themselves (both may be on, each deck
clears only itself, monitor_deck = most recently enabled) are
pinned in test_deck_state.py. What is new here: the SET LIST
marks every monitored deck's row at once (before, the single
monitor could only ever mark one), and the DDJ-RB headphone
CUE LEDs follow the per-deck set.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

try:
    from PySide6.QtWidgets import QApplication, QLabel
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False

qt_only = pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')


@pytest.fixture(scope='module')
def window():
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()
    qapp.processEvents()


def _row_markers(window):
    markers = []
    for i in range(window.setlist_rows.count()):
        container = window.setlist_rows.itemAt(i).widget()
        texts = [w.text() for w in container.findChildren(QLabel)]
        markers.append('モニター' in texts)
    return markers


@qt_only
def test_setlist_marks_both_monitored_decks_rows(window, tmp_path):
    import shutil
    from engine import load_track
    from make_demo import create
    create(tmp_path)
    src = sorted(tmp_path.glob('*.wav'))[0]
    path_a = str(src)
    path_b = str(tmp_path / 'second.wav')
    shutil.copy(path_a, path_b)
    window.loaded(0, load_track(path_a), '')
    window.loaded(1, load_track(path_b), '')
    original = window.setlist_data
    try:
        window.setlist_data = {
            'format': 'mixcraft-setlist', 'version': 1,
            'set_bpm': 122, 'started': False, 'current_index': -1,
            'tracks': [
                {'title': 'One', 'artist': 'X', 'bpm': 120,
                 'key': '8A', 'duration_seconds': 200,
                 'path': path_a},
                {'title': 'Two', 'artist': 'Y', 'bpm': 120,
                 'key': '8A', 'duration_seconds': 200,
                 'path': path_b}]}
        window.toggle_monitor(0)
        window.toggle_monitor(1)
        window.render_setlist()
        assert _row_markers(window) == [True, True]
        window.toggle_monitor(0)      # deck A off; B's row stays
        window.render_setlist()
        assert _row_markers(window) == [False, True]
    finally:
        window.monitor_decks = set()
        window._monitor_order = []
        window._refresh_monitor_buttons()
        window.setlist_data = original
        window.render_setlist()


def test_led_states_accepts_a_monitor_set():
    import midi_input
    # led_states emits (deck, note, on) triples; the headphone
    # CUE note is 84 on each deck's own channel.
    states = midi_input.led_states((True, True), {0, 1})
    cue = [s for s in states if s[1] == 84]
    assert len(cue) == 2 and all(s[2] for s in cue)
    # …and the legacy single-deck / None forms still work.
    states = midi_input.led_states((True, False), 0)
    cue = [s for s in states if s[1] == 84]
    assert [s[2] for s in cue] == [True, False]
    states = midi_input.led_states((False, False), None)
    cue = [s for s in states if s[1] == 84]
    assert [s[2] for s in cue] == [False, False]
