"""Remaining-time displays. Pass 16 removed them all; pass 20
(user review fix, 2026-11-27: 「残り時間はほしいね」) restored
the remaining figure on the DECK clocks only — the MIX TIMER is
still the elapsed readout plus START/STOP + RESET, with no
'remaining' readout and no over-target state. (The engine's
format_mix_remaining math stays in engine.py regardless.)

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


def test_no_remaining_readout(window):
    assert not hasattr(window, 'mix_remaining')
    assert not hasattr(window, 'mix_target_seconds')


def test_mix_timer_shows_elapsed_only(window):
    window.mix_timer['elapsed'] = 23 * 60 + 47
    window.refresh_mix_timer()
    assert window.mix_elapsed.text() == 'MIX 23:47'
    window.mix_timer['elapsed'] = 0.0
    window.refresh_mix_timer()
    assert window.mix_elapsed.text() == 'MIX 00:00'


def test_deck_clock_shows_remaining_and_elapsed(window):
    # Pass 20: with a track loaded, the deck clock is the pair
    # '-remaining  elapsed'; with no track it falls back to
    # elapsed alone (there is no remaining to show).
    import numpy as np
    from engine import RATE

    class StubTrack:
        def __init__(self, seconds):
            self.audio = np.zeros((int(seconds * RATE), 2),
                                  dtype=np.float32)

    window.engine.decks[0]['track'] = StubTrack(24.0)
    window.engine.decks[0]['position'] = int(13.0 * RATE)
    window.engine.decks[1]['position'] = int(11.0 * RATE)
    window.tick()
    assert window.decks[0].clock.text() == '-00:11.0  00:13.0'
    assert window.decks[1].clock.text() == '00:11.0'
    window.engine.decks[0]['track'] = None
    window.engine.decks[0]['position'] = 0
    window.engine.decks[1]['position'] = 0
    window.tick()
