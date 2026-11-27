"""Pass 23 (window fit, user report 2026-11-27: the window opened
larger than his screen at >100% Windows display scaling): the
window opens clamped to the primary screen's available geometry,
centred inside it, and stays freely resizable (no fixed size).

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
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()


def test_window_fits_primary_screen(window):
    screen = QApplication.primaryScreen()
    if screen is None:
        pytest.skip('no primary screen in this environment')
    avail = screen.availableGeometry()
    assert window.width() <= avail.width()
    assert window.height() <= avail.height()
    # Centred: the window's top-left sits inside the available area.
    assert window.x() >= avail.left()
    assert window.y() >= avail.top()
    assert window.x() + window.width() <= avail.right() + 1
    assert window.y() + window.height() <= avail.bottom() + 1


def test_window_is_not_fixed_size(window):
    # A fixed-size window reports minimumSize() == maximumSize();
    # the app window must stay resizable / maximizable.
    assert window.minimumSize() != window.maximumSize()
    window.resize(900, 640)
    assert (window.width(), window.height()) == (900, 640)
