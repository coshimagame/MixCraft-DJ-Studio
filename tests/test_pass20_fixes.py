"""Pass 20 (the user's review fix list, 2026-11-27):
- the MASTER knob and each deck's GAIN / FILTER knobs wear the
  muted-amber accent (indicator + arc colour); HI / MID / LOW
  keep the neutral grey — exactly five accented knobs;
- the transport buttons read in the SAME top-to-bottom order on
  both decks （読込 / 再生・一時停止 / 先頭） — Deck B is no
  longer mirrored;
- the deck info block has no BPM readout any more;
- the audition seconds combo carries its caption （試聴の長さ /
  AUDITION LENGTH), switching with the language toggle.

Offscreen Qt; skipped where PySide6 cannot be imported.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

try:
    from PySide6.QtGui import QColor
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


def test_accent_on_exactly_master_gain_filter(window):
    accent = QColor(appmod.COLOR_KNOB_ACCENT)
    neutral = QColor(appmod.COLOR_TEXT_SUB)
    assert window.master.color == accent
    accented = [window.master]
    for deck in window.decks:
        for action in ('gain', 'filter'):
            assert deck.controls[action].color == accent, action
            accented.append(deck.controls[action])
        for action in ('eq_high', 'eq_mid', 'eq_low'):
            assert deck.controls[action].color == neutral, action
    assert len(accented) == 5


def test_transport_order_identical_on_both_decks(window):
    for deck in window.decks:
        grid = deck.layout()
        transport_box = grid.itemAtPosition(0, 0).widget()
        order = [transport_box.layout().itemAt(i).widget()
                 for i in range(3)]
        assert order == [deck.load, deck.play, deck.cue]


def test_no_deck_bpm_readout(window):
    for deck in window.decks:
        assert not hasattr(deck, 'bpm')


def test_audition_length_caption_switches_language(window):
    assert window.audition_label.text() == '試聴の長さ'
    window.set_language('en')
    try:
        assert window.audition_label.text() == 'AUDITION LENGTH'
    finally:
        window.set_language('jp')
    assert window.audition_label.text() == '試聴の長さ'
