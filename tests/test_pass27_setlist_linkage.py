"""Pass 27 — set list <-> performance linkage.

The user's real-machine verdict on pass 26: 「ミックスリストと
演奏中の曲の相互連携が取れてなくて矛盾してる」. The old panel
derived its row badges from the set-list FILE's snapshot
(started / current_index, or per-track status fields) — values
written by whoever exported the JSON (usually the phone page)
and never updated by the PC app when a deck actually played.
So a freshly imported list could claim rows were played /
playing that no deck had ever touched, and a real performance
never moved the badges at all, while the deck ON AIR chips
(live from the engine) said something else entirely.

Pass 27 makes the engine the single source of truth: rows are
claimed by file path when a deck plays a matching file, the
played ledger fills as claims sound, and 'next' follows the
first unplayed row after the furthest sounded one. These
tests drive the real Window + engine through every load route
(set-list double-click, deck drag & drop, the loaded()
completion the 読込 button / file dialog end in) and pin the
resulting row states.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

pytest.importorskip('PySide6')
import soundfile as sf

from PySide6.QtCore import QMimeData, QUrl
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


def _setup(win, tmp_path):
    """Three listed tracks + one off-list file; the list is
    installed the way open_setlist leaves it (live state reset)."""
    files = [_wav(tmp_path, 'one.wav', 440.0),
             _wav(tmp_path, 'two.wav', 550.0),
             _wav(tmp_path, 'three.wav', 660.0)]
    off = _wav(tmp_path, 'offlist.wav', 330.0)

    def track(title, path):
        return {'title': title, 'artist': 'T', 'bpm': 120,
                'key': '8A', 'duration_seconds': 6,
                'preview_url': None, 'preview_source': None,
                'source_url': None, 'local_preview': None,
                'path': str(path)}

    win.setlist_data = {
        'format': 'mixcraft-setlist', 'version': 1, 'set_bpm': 120,
        'started': False, 'current_index': -1, 'target_minutes': 60,
        'tracks': [track('One', files[0]), track('Two', files[1]),
                   track('Three', files[2])]}
    win._reset_setlist_live()
    win.render_setlist()
    return files, off


def _load(win, deck, path):
    """The completion every load route ends in."""
    win.loaded(deck, load_track(str(path)), '')


def _play(win, deck, on=True):
    win.engine.command('play', deck, on)
    win.engine.apply_pending()
    win.tick()


def _statuses(win):
    return win._setlist_live_statuses()


def _badge_texts(win):
    texts = []
    for i in range(win.setlist_rows.count()):
        container = win.setlist_rows.itemAt(i).widget()
        for j in range(container.layout().count()):
            widget = container.layout().itemAt(j).widget()
            if isinstance(widget, QLabel) and widget.width() == 84:
                texts.append(widget.text())
    return texts


def test_double_click_play_stop_then_next_entry(window, tmp_path):
    files, _ = _setup(window, tmp_path)
    # Route the loader synchronously so the double-click entry
    # point itself is what runs (it picks deck + path).
    window.load = lambda i, p: _load(window, i, p)
    window.load_setlist_entry(0)
    window.tick()   # loaded() lands the track via the engine queue
    assert window.engine.decks[0]['track'] is not None
    assert _statuses(window) == ['queued', 'queued', 'queued']

    _play(window, 0)
    assert _statuses(window) == ['playing', 'next', 'queued']
    assert _badge_texts(window) == ['再生中', '次', '待機']

    _play(window, 0, on=False)
    assert _statuses(window) == ['played', 'next', 'queued']

    # The next entry double-clicks onto the standby deck and
    # takes over; the 'next' marker moves past it.
    window.load_setlist_entry(1)
    _play(window, 1)
    assert _statuses(window) == ['played', 'playing', 'next']


def test_off_list_file_play_leaves_rows_untouched(window, tmp_path):
    _, off = _setup(window, tmp_path)
    _load(window, 0, off)
    _play(window, 0)
    # No row may light up for a file that is not in the list,
    # and with nothing in the list sounded there is no 'next'.
    assert _statuses(window) == ['queued', 'queued', 'queued']
    assert window._deck_claims == [None, None]
    assert window._setlist_played == set()


def test_deck_drop_of_listed_file_matches_double_click(window,
                                                       tmp_path):
    files, _ = _setup(window, tmp_path)
    window.load = lambda i, p: _load(window, i, p)

    class DropEvent:
        def __init__(self, path):
            self._mime = QMimeData()
            self._mime.setUrls([QUrl.fromLocalFile(str(path))])

        def mimeData(self):
            return self._mime

        def acceptProposedAction(self):
            pass

    window.decks[1].dropEvent(DropEvent(files[1]))
    _play(window, 1)
    # Same state the double-click route produces for this
    # entry: its row is now-playing, claimed by path; the row
    # before it was never played, so it stays queued and the
    # marker after the playing row is 'next'.
    assert window._deck_claims[1] == 1
    assert _statuses(window) == ['queued', 'playing', 'next']


def test_pause_marks_played_and_resume_relights_same_row(window,
                                                         tmp_path):
    files, _ = _setup(window, tmp_path)
    _load(window, 0, files[0])
    _play(window, 0)
    assert _statuses(window)[0] == 'playing'
    _play(window, 0, on=False)          # pause / stop
    assert _statuses(window) == ['played', 'next', 'queued']
    _play(window, 0)                    # resume the same file
    assert _statuses(window) == ['playing', 'next', 'queued']
    assert window._deck_claims[0] == 0


def test_imported_snapshot_alone_lights_nothing(window, tmp_path):
    """THE contradiction case: a phone-exported list whose
    snapshot says the set is mid-way (started, current_index=2)
    must show all rows queued until a deck actually plays —
    the old panel lit rows 0-1 played and row 2 playing from
    the file alone, contradicting the silent decks."""
    files, _ = _setup(window, tmp_path)
    window.setlist_data['started'] = True
    window.setlist_data['current_index'] = 2
    window._reset_setlist_live()
    window.render_setlist()
    assert _statuses(window) == ['queued', 'queued', 'queued']
    assert _badge_texts(window) == ['待機', '待機', '待機']
