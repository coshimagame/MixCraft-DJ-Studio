"""Knob captions: all five knobs per deck stay, fully wired —
only the caption TEXT changed over the passes. Pass 17 set GAIN /
(none) / (none) / LOW / FILTER (the 'EQ' prefix gone, HIGH / MID
uncaptioned); pass 20 (user review fix, 2026-11-27: 「hi mid の
文字がない」) restored the bare band names — the final row is
GAIN / HI / MID / LOW / FILTER, and the letters 'EQ' still appear
nowhere.

Offscreen Qt; skipped where PySide6 cannot be imported.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False

pytestmark = pytest.mark.skipif(not HAVE_QT,
                                reason='PySide6 unavailable')


@pytest.fixture(scope='module')
def window():
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:  # no audio stack / display in this env
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()
    qapp.processEvents()


def test_all_five_knobs_present_with_final_captions(window):
    for deck in window.decks:
        assert list(deck.controls) == ['gain', 'eq_high', 'eq_mid',
                                       'eq_low', 'filter']
        labels = [deck.controls[k].label for k in deck.controls]
        assert labels == ['GAIN', 'HI', 'MID', 'LOW', 'FILTER']
        assert all('EQ' not in label for label in labels)


def test_knobs_still_drive_the_engine(window):
    # Turning every knob lands in the engine state (gain: v/50;
    # EQ bands: v/100 mapped to 0..2 by the engine; filter: v/100).
    cases = [('gain', 40, 'gain', 0.8),
             ('eq_high', 40, 'eq_high', 0.8),
             ('eq_mid', 60, 'eq_mid', 1.2),
             ('eq_low', 30, 'eq_low', 0.6),
             ('filter', 35, 'filter', 0.35)]
    for deck_index, deck in enumerate(window.decks):
        for knob_key, value, state_key, expected in cases:
            deck.controls[knob_key].setValue(value)
            window.engine.render(8)
            actual = window.engine.decks[deck_index][state_key]
            assert actual == pytest.approx(expected), \
                (deck_index, knob_key, actual)
        for knob_key, value, state_key, expected in cases:
            deck.controls[knob_key].setValue(50)
        window.engine.render(8)
