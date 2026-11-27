"""Pass 23 (window fit, user report 2026-11-27: the window opened
larger than his screen at >100% Windows display scaling): the
window opens clamped to the primary screen's available geometry,
centred inside it, and stays freely resizable (no fixed size).

Pass 24 (user report 2026-11-27: 「縦横比がおかしい」「サイズ変え
ると切れたりする」): the pass-23 clamp compared the CLIENT size
against the available geometry, which is in FRAME terms — the
frame overflowed the work area by the title-bar size. The window
now re-fits its frame into the available geometry at first show;
the layout's minimum shrank (958x778, from 1133x794) so mid-size
windows need no horizontal scroll; and where the content truly
does not fit, the scroll bars reach everything (verified by
scrolling to the extremes and checking the far widgets' visible
regions).

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


def test_window_has_a_sane_minimum_size(window):
    # Pass 24: drag-resizing cannot crush the window below a
    # usable floor (and the floor fits any realistic screen).
    assert window.minimumSize().width() == 760
    assert window.minimumSize().height() == 500


def test_layout_minimum_shrank(window):
    # Pass 24 trims (transport padding, readout/chip heights,
    # waveform minimum, pad widths): the root layout's floor is
    # 958x778, down from 1133x794 — a 1100px-wide window no
    # longer needs a horizontal scroll bar.
    root = window.centralWidget().widget()
    minimum = root.layout().minimumSize()
    assert minimum.width() <= 1000
    assert minimum.height() <= 790


@pytest.fixture()
def shown_window():
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    win.show()
    qapp.processEvents()
    yield win
    win.close()


def test_frame_fits_available_geometry_when_shown(shown_window):
    screen = QApplication.primaryScreen()
    if screen is None:
        pytest.skip('no primary screen in this environment')
    avail = screen.availableGeometry()
    frame = shown_window.frameGeometry()
    assert frame.left() >= avail.left()
    assert frame.top() >= avail.top()
    assert frame.right() <= avail.right()
    assert frame.bottom() <= avail.bottom()


def test_scroll_reaches_the_bottom_and_right(shown_window):
    # At a small size the status bar starts below the fold; the
    # vertical scroll bar must bring it fully into view (the
    # pass-23/24 complaint was content reading as CUT OFF).
    qapp = QApplication.instance()
    shown_window.resize(900, 560)
    qapp.processEvents()
    scroll = shown_window.centralWidget()
    vbar, hbar = scroll.verticalScrollBar(), scroll.horizontalScrollBar()
    assert vbar.maximum() > 0
    assert shown_window.status.visibleRegion().isEmpty()
    vbar.setValue(vbar.maximum())
    qapp.processEvents()
    assert not shown_window.status.visibleRegion().isEmpty()
    # Same for the right edge: the set list panel is fully
    # visible only after scrolling right.
    panel = shown_window.setlist_rows.parentWidget()
    while panel.objectName() != 'setlist':
        panel = panel.parentWidget()
    assert panel.visibleRegion().boundingRect().width() < panel.width()
    assert hbar.maximum() > 0
    hbar.setValue(hbar.maximum())
    qapp.processEvents()
    assert panel.visibleRegion().boundingRect().width() == panel.width()
