"""Pass 32 — rekordbox-style list/deck loading (user dictation
2026-11-28: 「だめだ」 on the pass-29 behaviour).

Rule A: songs dropped onto the set list fill the empty decks
in order — first added song to deck A if A is empty, second
to deck B if B is empty; with both decks occupied the drop
only adds rows, as before.

Rule B: a file dropped onto a deck loads there, and the
loaded song's row becomes the TOPMOST row of the list —
actual rows are ordered most-recent deck load first.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

pytest.importorskip('PySide6')
import soundfile as sf

from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QLabel

import app as appmod
from engine import RATE, load_track


@pytest.fixture()
def window(qapp=None):
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:  # no audio stack / display in this env
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()
    qapp.processEvents()


def _wav(tmp_path, name, freq=440.0):
    t = np.arange(int(6 * RATE)) / RATE
    audio = np.column_stack([np.sin(2 * np.pi * freq * t)] * 2)
    path = tmp_path / name
    sf.write(path, (audio * 0.2).astype('float32'), RATE)
    return path


def _record_loads(win):
    """Replace Window.load with a recorder: the deck
    assignments are the observable under test (the real load
    path itself is covered by the pass-29 tests)."""
    calls = []
    win.load = lambda deck, path: calls.append((deck, str(path)))
    return calls


def _occupy(win, deck, path):
    """Put a real track on a deck through the normal arrival
    door (Window.loaded), as a finished load would."""
    win.loaded(deck, load_track(str(path)), '')
    win.tick()


def _planned_proud(win):
    win.setlist_data = {
        'format': 'mixcraft-setlist', 'version': 1, 'set_bpm': 125,
        'started': False, 'current_index': -1, 'target_minutes': 60,
        'tracks': [{'title': 'Proud (Extended Mix)',
                    'artist': 'Qrion', 'bpm': None, 'key': None,
                    'duration_seconds': 323, 'preview_url': None,
                    'preview_source': None, 'source_url': None,
                    'local_preview': None, 'path': None}]}
    win._reset_setlist_live()
    win.render_setlist()


def _row_texts(win, row):
    container = win.setlist_rows.itemAt(row).widget()
    return [w.text() for w in container.findChildren(QLabel)]


# ------------------------------------------------------------------
# Rule A — drops onto the list fill the empty decks
# ------------------------------------------------------------------

def test_drop_to_list_fills_empty_decks_a_then_b(window, tmp_path):
    a = _wav(tmp_path, 'A - One.wav', 440.0)
    b = _wav(tmp_path, 'B - Two.wav', 550.0)
    c = _wav(tmp_path, 'C - Three.wav', 660.0)
    calls = _record_loads(window)
    window.add_dropped_to_setlist([str(a), str(b), str(c)])
    # First added -> deck A, second -> deck B, third only adds.
    assert calls == [(0, str(a)), (1, str(b))]
    assert len(window.setlist_data['tracks']) == 3
    # A list born from a drop starts with the cursor on row 1.
    assert window._setlist_cursor == ('planned', 0)


def test_drop_to_list_skips_the_occupied_deck(window, tmp_path):
    held = _wav(tmp_path, 'Held.wav', 330.0)
    b = _wav(tmp_path, 'B - Two.wav', 550.0)
    c = _wav(tmp_path, 'C - Three.wav', 660.0)
    _occupy(window, 0, held)            # deck A already holds a song
    calls = _record_loads(window)
    window.add_dropped_to_setlist([str(b), str(c)])
    # Only deck B was empty: the first added song goes there,
    # the second only adds a row.
    assert calls == [(1, str(b))]
    assert len(window.setlist_data['tracks']) == 2


def test_drop_to_list_with_both_decks_occupied_only_adds(
        window, tmp_path):
    x = _wav(tmp_path, 'X.wav', 330.0)
    y = _wav(tmp_path, 'Y.wav', 370.0)
    b = _wav(tmp_path, 'B - Two.wav', 550.0)
    _occupy(window, 0, x)
    _occupy(window, 1, y)
    calls = _record_loads(window)
    window.add_dropped_to_setlist([str(b)])
    assert calls == []
    assert len(window.setlist_data['tracks']) == 1


# ------------------------------------------------------------------
# Rule B — a deck load puts the song's row on top
# ------------------------------------------------------------------

def test_deck_loads_stack_actual_rows_most_recent_first(
        window, tmp_path):
    heya = _wav(tmp_path,
                'Romain Garcia - Heya (Extended Mix).wav', 440.0)
    signals = _wav(tmp_path, 'Hidden Empire - Signals.wav', 550.0)
    _planned_proud(window)
    _occupy(window, 1, signals)         # deck B takes Signals
    assert [r['title'] for r in window._actual_rows] == ['Signals']
    _occupy(window, 0, heya)            # deck A takes Heya later
    # Most recent load first: Heya's row is the topmost row of
    # the whole list, Signals follows, the planned row is last.
    assert [r['title'] for r in window._actual_rows] == \
        ['Heya (Extended Mix)', 'Signals']
    assert window.setlist_rows.count() == 3
    assert any('Heya' in t for t in _row_texts(window, 0))
    assert any('Signals' in t for t in _row_texts(window, 1))
    assert any('Proud' in t for t in _row_texts(window, 2))
    # Reloading Signals onto its deck brings its row back up.
    _occupy(window, 1, signals)
    assert [r['title'] for r in window._actual_rows] == \
        ['Signals', 'Heya (Extended Mix)']


def test_deck_drop_event_loads_and_tops_the_row(window, tmp_path):
    signals = _wav(tmp_path, 'Hidden Empire - Signals.wav', 550.0)
    _planned_proud(window)
    calls = []

    def forward(deck, path):
        calls.append((deck, str(path)))
        window.loaded(deck, load_track(str(path)), '')

    window.load = forward
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(signals))])
    event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction,
                       mime, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    window.decks[1].dropEvent(event)    # dropped onto deck B's strip
    window.tick()
    assert calls == [(1, str(signals))]
    assert [r['title'] for r in window._actual_rows] == ['Signals']
    assert window._actual_rows[0]['deck'] == 1
    assert any('Signals' in t for t in _row_texts(window, 0))
