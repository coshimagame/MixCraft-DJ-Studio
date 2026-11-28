"""Pass 14 (old-LCD windows): the display surfaces are old-LCD
glass — pale grey-green, dark green-charcoal ink, ghost dot grid.

These tests pin the palette's readability contract (ink-on-glass
contrast ratios, computed from the actual constants), the segment
meter's fill math, and that every LCD surface paints offscreen
without raising."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication

import app as appmod


def _luminance(hex_color):
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_lcd_ink_on_glass_contrast():
    # Primary ink must read comfortably even against the lightest
    # glass stop; secondary ink must clear normal-text contrast;
    # the inverted (now-playing) row's pale text is the same ink /
    # glass pair reversed, so it inherits the primary ratio.
    assert _contrast(appmod.COLOR_LCD_INK,
                     appmod.COLOR_LCD_GLASS_HI) >= 7.0
    assert _contrast(appmod.COLOR_LCD_INK_SOFT,
                     appmod.COLOR_LCD_GLASS_HI) >= 4.5
    assert _contrast(appmod.COLOR_LCD_PALE,
                     appmod.COLOR_LCD_INK) >= 7.0
    # De-emphasis ink (played rows) is a density step below the
    # secondary ink, not a second text colour.
    assert _contrast(appmod.COLOR_LCD_INK_DIM,
                     appmod.COLOR_LCD_GLASS_HI) < \
        _contrast(appmod.COLOR_LCD_INK_SOFT, appmod.COLOR_LCD_GLASS_HI)


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_meter_segment_fill(qapp):
    meter = appmod.MeterLamp()
    assert meter.lit_segments() == 0
    meter.setValue(3)     # below the signal threshold: still dark
    assert meter.lit_segments() == 0
    meter.setValue(10)    # any real signal lights the first block
    assert meter.lit_segments() == 1
    meter.setValue(50)
    assert meter.lit_segments() == 4
    meter.setValue(100)   # full scale: all segments, marker included
    assert meter.lit_segments() == appmod.MeterLamp.SEGMENTS


def test_lcd_surfaces_paint(qapp):
    # Smoke: every pass-14 LCD surface paints offscreen without
    # raising (glass fill, ghost grid, ink drawing).
    wave = appmod.Waveform(appmod.COLOR_WAVE_BLUE)
    wave.resize(420, 100)
    assert not wave.grab().isNull()
    readout = appmod.DotReadout()
    readout.setText('120.0 BPM')
    readout.resize(180, 28)
    assert not readout.grab().isNull()
    dial = appmod.JogDial(caption='SET BPM', size=148)
    dial.set_bpm(122)
    assert not dial.grab().isNull()
    meter = appmod.MeterLamp()
    meter.setValue(96)
    assert not meter.grab().isNull()
    frame = appmod.LcdWindow()
    frame.setObjectName('lcdlist')
    frame.resize(300, 120)
    assert not frame.grab().isNull()
