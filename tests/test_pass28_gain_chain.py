"""Pass 28 (user report 2026-11-28: 「音が小さい」 — confirmed
to be the APP's playback): the in-app gain chain, measured.

Stage gains at the OLD defaults (single deck playing):
  deck volume 0.8 (-1.94 dB) x crossfader centre cos(pi/4)
  0.7071 (-3.01 dB) x master 0.8 (-1.94 dB) = 0.4525
  ≈ -6.89 dB. Two stacked 0.8 defaults were the found cause;
  the soft limiter (threshold 0.90) never engaged at that
  level, and no other stage attenuates.

The fix: the volume and master defaults are unity (1.0) in
the engine, in the export's replay seeds, and on the widgets.
What remains at centre is the equal-power crossfader's -3.01
dB — the correct blend law, the same as hardware mixers:
push the crossfader to the playing deck's side and a single
deck passes at exactly unity.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

import performance
from engine import RATE, Engine, Track


def _constant_track(level=0.5, seconds=30):
    audio = np.full((RATE * seconds, 2), level, dtype='float32')
    return Track('flat.wav', audio, np.ones(64, dtype='float32'),
                 120.0, 0.0)


def _render_peak(engine, blocks=120):
    peak = 0.0
    for _ in range(blocks):
        out = engine.render(512)
        peak = max(peak, float(np.max(np.abs(out))))
    return peak


def _playing_engine():
    e = Engine()
    e.command('load', 0, _constant_track())
    e.command('play', 0, True)
    e.render(512)
    return e


def test_defaults_are_unity_and_agree_everywhere():
    e = Engine()
    assert e.master == 1.0
    assert e.decks[0]['volume'] == 1.0
    # The export's replay seeds must match the engine defaults,
    # or the written volume envelope would bake a level the
    # performance never had.
    assert performance.GLOBAL_DEFAULTS['master'] == e.master
    assert performance.PARAM_DEFAULTS['volume'] == \
        e.decks[0]['volume']


def test_centre_crossfader_passes_at_equal_power_gain():
    e = _playing_engine()
    peak = _render_peak(e)
    # 0.5 source x volume 1.0 x cos(pi/4) x master 1.0.
    assert peak == pytest.approx(0.5 * 0.70711, rel=0.03)


def test_crossfader_at_the_decks_side_passes_at_unity():
    e = _playing_engine()
    e.command('cross', None, 0.0)      # fully to deck A
    peak = _render_peak(e)
    assert peak == pytest.approx(0.5, rel=0.03)


try:
    from PySide6.QtWidgets import QApplication
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


@qt_only
def test_widget_defaults_match_the_engine(window):
    # Channel faders and the MASTER knob open at 100 (= 1.0),
    # the same unity the engine starts from.
    for deck in window.decks:
        assert deck.volume.value() == 100
    assert window.master.value() == 100
