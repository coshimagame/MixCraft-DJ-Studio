"""Pass 16 (remaining-time displays removed): the deck clocks
show ELAPSED only, and the MIX TIMER is the elapsed readout plus
START/STOP + RESET — no 'remaining' readout, no over-target state.
(The engine's format_mix_remaining math stays in engine.py; the
app simply no longer displays it.)

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


def test_deck_clock_shows_elapsed_only(window):
    from engine import RATE
    window.engine.decks[0]['position'] = int(13.0 * RATE)
    window.engine.decks[1]['position'] = int(11.0 * RATE)
    window.tick()
    assert window.decks[0].clock.text() == '00:13.0'
    assert window.decks[1].clock.text() == '00:11.0'
    for deck in window.decks:
        assert '-' not in deck.clock.text()
    window.engine.decks[0]['position'] = 0
    window.engine.decks[1]['position'] = 0
    window.tick()
