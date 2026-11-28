"""Pass 30 — the now-playing row is identifiable at a glance.

User 2026-11-28: 「リストの方は再生中の曲がわかるようにして
ほしい」. Pass 27/29 already drove the row state from the
engine and inverted the playing row, but the only TEXT signal
was the badge in the rightmost column — the column his 150%
scaling clips off the panel (his screenshot showed only
「待」 of the status column). Pass 30 adds a FILLED chip at
the row's LEFT edge carrying 再生中, so the marker survives
any right-side clipping; non-playing rows keep an empty slot
of the same width so the columns stay aligned.
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


def _left_chip(win, row):
    """The row's left-edge now-playing chip (fixed width 48 —
    the first label of that width in the row)."""
    container = win.setlist_rows.itemAt(row).widget()
    for widget in container.findChildren(QLabel):
        if widget.width() == 48:
            return widget
    return None


def test_playing_rows_wear_the_filled_left_chip(window, tmp_path):
    heya, signals = _setup(window, tmp_path)
    _load(window, 0, heya)
    _load(window, 1, signals)
    _play(window, 0)
    _play(window, 1)
    # Both actual rows are playing: each wears the filled chip
    # at its left edge, with the pale fill of the inverted row.
    for row in (0, 1):
        chip = _left_chip(window, row)
        assert chip is not None
        assert chip.text() == '再生中'
        assert appmod.COLOR_LCD_PALE in chip.styleSheet()
        assert 'background' in chip.styleSheet()
    # The planned row (waiting) keeps an EMPTY slot of the
    # same width — no chip text, no fill.
    chip = _left_chip(window, 2)
    assert chip is not None
    assert chip.text() == ''
    assert 'background' not in chip.styleSheet()
    # The inversion and the right-side badge are unchanged.
    container = window.setlist_rows.itemAt(0).widget()
    assert container.objectName() == 'setrowplaying'
    badges = [w.text() for w in container.findChildren(QLabel)
              if w.width() == 84]
    assert badges == ['再生中']


def test_chip_clears_when_the_deck_stops(window, tmp_path):
    heya, signals = _setup(window, tmp_path)
    _load(window, 0, heya)
    _play(window, 0)
    assert _left_chip(window, 0).text() == '再生中'
    _play(window, 0, on=False)
    # Played: the lamp goes out (the badge at the right now
    # reads 済, as before).
    assert _left_chip(window, 0).text() == ''
    container = window.setlist_rows.itemAt(0).widget()
    assert container.objectName() != 'setrowplaying'
