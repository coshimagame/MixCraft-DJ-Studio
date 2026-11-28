"""Pass 29 — set list vs decks: off-list tracks get ACTUAL rows.

The user's verdict on pass 27 (2026-11-28): 「トラックリストと
再生中のものの矛盾も解決してない」. His screenshot: the list
held ONE planned row (Qrion – Proud, deck A, waiting) while
deck A played Heya and deck B played Signals, both loaded
directly with the decks' 読込 buttons. Pass 27's rule — an
off-list play never touches the rows — kept the planned rows
honest but left the panel claiming a plan reality had left
behind: the performance itself was nowhere in the list.

Pass 29: when a deck loads a track that matches no planned
row, the track appears as an ACTUAL row — in load order, with
its real deck chip (not the plan's A/B alternation) and a live
status driven by the same claims machinery — rendered ABOVE
the planned rows. Planned rows keep their pass-27 behaviour
exactly (statuses, claims, next-marker).
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

pytest.importorskip('PySide6')
import soundfile as sf

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


def _setup(window, tmp_path):
    """His shape: ONE planned row with no local file (the phone
    list's Proud), plus two off-list files on disk."""
    heya = _wav(tmp_path,
                'Romain Garcia - Heya (Extended Mix).wav', 440.0)
    signals = _wav(tmp_path, 'Hidden Empire - Signals.wav', 550.0)
    window.setlist_data = {
        'format': 'mixcraft-setlist', 'version': 1, 'set_bpm': 125,
        'started': False, 'current_index': -1, 'target_minutes': 60,
        'tracks': [{'title': 'Proud (Extended Mix)',
                    'artist': 'Qrion', 'bpm': None, 'key': None,
                    'duration_seconds': 323, 'preview_url': None,
                    'preview_source': None, 'source_url': None,
                    'local_preview': None, 'path': None}]}
    window._reset_setlist_live()
    window.render_setlist()
    return heya, signals


def _load(win, deck, path):
    win.loaded(deck, load_track(str(path)), '')
    win.tick()


def _play(win, deck, on=True):
    win.engine.command('play', deck, on)
    win.engine.apply_pending()
    win.tick()


def _row_texts(win, row):
    container = win.setlist_rows.itemAt(row).widget()
    return [w.text() for w in container.findChildren(QLabel)]


def _row_badges(win):
    badges = []
    for i in range(win.setlist_rows.count()):
        container = win.setlist_rows.itemAt(i).widget()
        for widget in container.findChildren(QLabel):
            if widget.width() == 84:
                badges.append(widget.text())
    return badges


def _row_chips(win):
    chips = []
    for i in range(win.setlist_rows.count()):
        container = win.setlist_rows.itemAt(i).widget()
        for widget in container.findChildren(QLabel):
            if widget.width() == 22:
                chips.append(widget.text())
    return chips


def test_off_list_load_creates_actual_row_on_its_real_deck(
        window, tmp_path):
    heya, signals = _setup(window, tmp_path)
    _load(window, 1, signals)          # deck B, via 読込 route
    # The row exists from the LOAD, before any play: the list
    # can no longer claim the decks are empty while deck B
    # holds Signals.
    assert len(window._actual_rows) == 1
    assert window._actual_rows[0]['deck'] == 1
    assert window._actual_live_statuses() == ['queued']
    # The planned row is untouched (pass 27 semantics).
    assert window._setlist_live_statuses() == ['queued']
    assert window._deck_claims == [None, None]
    # Rendered: actual row ABOVE the planned row, wearing its
    # real deck letter B (the plan's alternation would say A
    # for row 1); the planned row follows.
    assert window.setlist_rows.count() == 2
    assert any('Signals' in text for text in _row_texts(window, 0))
    assert any('Proud' in text for text in _row_texts(window, 1))
    assert _row_chips(window) == ['B', 'A']


def test_off_list_play_then_stop_moves_actual_row_only(
        window, tmp_path):
    heya, signals = _setup(window, tmp_path)
    _load(window, 1, signals)
    _play(window, 1)
    assert window._actual_live_statuses() == ['playing']
    assert window._setlist_live_statuses() == ['queued']
    assert _row_badges(window) == ['再生中', '待機']
    _play(window, 1, on=False)
    assert window._actual_live_statuses() == ['played']
    assert window._setlist_live_statuses() == ['queued']
    assert _row_badges(window) == ['済', '待機']


def test_two_off_list_tracks_keep_load_order_and_decks(
        window, tmp_path):
    heya, signals = _setup(window, tmp_path)
    _load(window, 0, heya)
    _load(window, 1, signals)
    assert [r['deck'] for r in window._actual_rows] == [0, 1]
    _play(window, 0)
    _play(window, 1)
    # His screenshot's state, finally representable: both decks
    # on air with songs the plan never listed, both rows live,
    # the planned row still simply waiting below.
    assert window._actual_live_statuses() == ['playing', 'playing']
    assert window._setlist_live_statuses() == ['queued']
    assert window.setlist_rows.count() == 3
    assert _row_chips(window) == ['A', 'B', 'A']


def test_listed_track_never_gets_an_actual_row(window, tmp_path):
    listed = _wav(tmp_path, 'listed.wav', 660.0)
    window.setlist_data = {
        'format': 'mixcraft-setlist', 'version': 1, 'set_bpm': 120,
        'started': False, 'current_index': -1, 'target_minutes': 60,
        'tracks': [{'title': 'Listed', 'artist': 'T', 'bpm': 120,
                    'key': '8A', 'duration_seconds': 6,
                    'preview_url': None, 'preview_source': None,
                    'source_url': None, 'local_preview': None,
                    'path': str(listed)}]}
    window._reset_setlist_live()
    window.render_setlist()
    _load(window, 0, listed)
    _play(window, 0)
    assert window._actual_rows == []
    assert window._deck_claims[0] == 0
    assert window._setlist_live_statuses() == ['playing']
