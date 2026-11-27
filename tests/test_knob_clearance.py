"""Pass 18 (knob ring clearance): the Knob's painted ring — arc
radius 22 + tick overhang 5 + pen ≈ 28px around the centre — was
clipped by the old 64x52 cell, whose centre sat only 22px from
the top. The cell is 64x64 with the centre 30px down.

The scan test paints a knob offscreen and finds the topmost
pixel of the full-sweep track arc (drawn in COLOR_BORDER_HI):
before the fix the clipped arc's visible edge sat at y=0; now
the whole ring lives at y >= 3. The centre tick's clearance is
pinned by the geometry test (CENTRE_Y - reach >= 2).

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
def qapp():
    return QApplication.instance() or QApplication([])


def _track_top(knob):
    """Topmost y of the knob's track-arc colour in its grab
    (None if the arc never painted). AA edge pixels blend toward
    the background, so match the stroke colour with tolerance."""
    target = QColor(appmod.COLOR_BORDER_HI)
    image = knob.grab().toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if (abs(c.red() - target.red()) <= 6
                    and abs(c.green() - target.green()) <= 6
                    and abs(c.blue() - target.blue()) <= 6):
                return y
    return None


def test_knob_cell_contains_the_ring(qapp):
    assert appmod.Knob.WIDTH == 64
    assert appmod.Knob.HEIGHT == 64
    # The paint reach above the centre (arc 22 + ticks 5 + pen ~1)
    # must fit above CENTRE_Y with room to spare.
    reach = appmod.Knob.DIAMETER / 2 + 5 + 5 + 1
    assert appmod.Knob.CENTRE_Y - reach >= 2


def test_knob_ring_clears_the_top_edge(qapp):
    for bipolar, value in [(True, 50), (True, 0), (True, 100),
                           (False, 80)]:
        knob = appmod.Knob('LOW', appmod.COLOR_TEXT_SUB,
                           bipolar=bipolar)
        knob.setRange(0, 100)
        knob.setValue(value)
        knob.resize(appmod.Knob.WIDTH, appmod.Knob.HEIGHT)
        top = _track_top(knob)
        assert top is not None and top >= 3, (bipolar, value, top)


def test_window_knobs_use_the_roomy_cell(qapp):
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:  # no audio stack / display in this env
        pytest.skip(f'Window cannot be built here: {exc}')
    try:
        knobs = [win.master]
        for deck in win.decks:
            knobs += list(deck.controls.values())
        assert len(knobs) == 11   # 5 per deck + MASTER
        for knob in knobs:
            assert knob.width() == appmod.Knob.WIDTH
            assert knob.height() == appmod.Knob.HEIGHT
            top = _track_top(knob)
            assert top is not None and top >= 3
    finally:
        win.close()
        qapp.processEvents()
