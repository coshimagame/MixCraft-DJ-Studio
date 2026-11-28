"""Pass 32, Rule C — DDJ-RB BROWSE + LOAD, rekordbox style
(user 2026-11-28: 「RBのbackとかいてあるのぶ…それでミックス
リストの曲選んで左右のロードでデッキに乗せる、それは
rekordboxと同じ仕様にしてほしい」).

BROWSE turn (browser channel CC 64, difference-count) walks
a selection cursor in the set list; LOAD 1 / LOAD 2 (notes
70 / 71) load the cursor row onto deck A / deck B through
the same door as a double-click. Mouse clicks and the knob
share the one cursor. BROWSE push (note 65) stays
unassigned — no honest use yet.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

from midi_input import MidiController, translate

pytest.importorskip('PySide6')
import soundfile as sf

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

import app as appmod
import i18n
from engine import RATE


# ------------------------------------------------------------------
# Pure mapping table
# ------------------------------------------------------------------

def test_translate_browse_difference_count():
    # CW detents: the value IS the count (0x01-0x1E).
    assert translate('cc', 6, 64, 1) == ('browse', 1)
    assert translate('cc', 6, 64, 30) == ('browse', 30)
    # CCW detents: 0x7F-0x62 is -1..-30 (value minus 128).
    assert translate('cc', 6, 64, 127) == ('browse', -1)
    assert translate('cc', 6, 64, 98) == ('browse', -30)
    # No movement, no intent; other channels are not BROWSE.
    assert translate('cc', 6, 64, 0) is None
    assert translate('cc', 0, 64, 1) is None


def test_translate_load_buttons_and_unassigned_push():
    assert translate('note', 6, 70, 127) == ('load_row', 0)
    assert translate('note', 6, 71, 127) == ('load_row', 1)
    assert translate('note', 6, 70, 0) is None      # note-off
    # BROWSE push stays unassigned in pass 32.
    assert translate('note', 6, 65, 127) is None


# ------------------------------------------------------------------
# Controller against the real window, with a fake backend
# (the same double as test_midi_input's — no hardware, no
# rtmidi needed)
# ------------------------------------------------------------------

class FakePort:
    def __init__(self):
        self.sent = []
        self.closed = False

    def close(self):
        self.closed = True


class FakeBackend:
    available = True

    def __init__(self, inputs=('DDJ-RB',), outputs=('DDJ-RB',)):
        self._inputs = list(inputs)
        self._outputs = list(outputs)
        self.callback = None
        self.out_port = None

    def input_names(self):
        return list(self._inputs)

    def output_names(self):
        return list(self._outputs)

    def open_input(self, name, callback):
        self.callback = callback
        return FakePort()

    def open_output(self, name):
        self.out_port = FakePort()
        return self.out_port

    def send_note(self, port, channel, note, velocity):
        port.sent.append((channel, note, velocity))

    def inject(self, message):
        self.callback(message)


@pytest.fixture()
def window(qapp=None):
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:
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


def _list_with_files(win, paths):
    """A planned list whose rows carry real files, installed
    the way open_setlist leaves it: clean live state, cursor
    on the first row."""
    win.setlist_data = {
        'format': 'mixcraft-setlist', 'version': 1, 'set_bpm': 125,
        'started': False, 'current_index': -1, 'target_minutes': 60,
        'tracks': [appmod.file_track_entry(p) for p in paths]}
    win._reset_setlist_live()
    win._cursor_to_first()
    win.render_setlist()


def _rig(win):
    backend = FakeBackend()
    ctl = MidiController(win, backend=backend)
    win.midi = ctl
    ctl.poll(now=100.0)
    assert ctl.connected
    return backend, ctl


def _send(backend, ctl, message, at):
    backend.inject(message)
    ctl.poll(now=at)


def test_browse_walks_and_clamps_the_cursor(window, tmp_path):
    paths = [_wav(tmp_path, f'Track {n}.wav', 400.0 + 40 * n)
             for n in range(3)]
    _list_with_files(window, paths)
    backend, ctl = _rig(window)
    assert window._setlist_cursor == ('planned', 0)
    _send(backend, ctl, ('cc', 6, 64, 1), 100.1)      # one detent CW
    assert window._setlist_cursor == ('planned', 1)
    _send(backend, ctl, ('cc', 6, 64, 30), 100.2)     # a fast spin
    assert window._setlist_cursor == ('planned', 2)   # clamps at the end
    _send(backend, ctl, ('cc', 6, 64, 127), 100.3)    # one detent CCW
    assert window._setlist_cursor == ('planned', 1)
    _send(backend, ctl, ('cc', 6, 64, 98), 100.4)     # hard CCW spin
    assert window._setlist_cursor == ('planned', 0)   # clamps, no wrap
    # The frame is painted on the cursor row only.
    frames = [window.setlist_rows.itemAt(i).widget().property('browse_cursor')
              for i in range(window.setlist_rows.count())]
    assert frames == [True, None, None]
    ctl.close()


def test_load_buttons_load_the_cursor_row_to_the_named_deck(
        window, tmp_path):
    paths = [_wav(tmp_path, f'Track {n}.wav', 400.0 + 40 * n)
             for n in range(3)]
    _list_with_files(window, paths)
    backend, ctl = _rig(window)
    calls = []
    window.load = lambda deck, path: calls.append((deck, str(path)))
    _send(backend, ctl, ('cc', 6, 64, 1), 100.1)      # cursor -> row 2
    _send(backend, ctl, ('note', 6, 71, 127), 100.2)  # LOAD 2 -> deck B
    assert calls == [(1, str(paths[1]))]
    _send(backend, ctl, ('note', 6, 70, 127), 100.3)  # LOAD 1 -> deck A
    assert calls == [(1, str(paths[1])), (0, str(paths[1]))]
    ctl.close()


def test_load_on_a_fileless_row_explains_itself(window, tmp_path):
    _list_with_files(window, [])
    window.setlist_data['tracks'] = [{
        'title': 'Proud (Extended Mix)', 'artist': 'Qrion',
        'bpm': None, 'key': None, 'duration_seconds': 323,
        'preview_url': None, 'preview_source': None,
        'source_url': None, 'local_preview': None, 'path': None}]
    window._reset_setlist_live()
    window._cursor_to_first()
    window.render_setlist()
    backend, ctl = _rig(window)
    calls = []
    window.load = lambda deck, path: calls.append((deck, str(path)))
    _send(backend, ctl, ('note', 6, 70, 127), 100.1)  # LOAD 1
    assert calls == []                                # nothing loaded
    assert window._status_fn(window.lang) == \
        i18n.tr('setlist_entry_no_file', window.lang)
    ctl.close()


def test_mouse_click_moves_the_same_cursor(window, tmp_path):
    paths = [_wav(tmp_path, f'Track {n}.wav', 400.0 + 40 * n)
             for n in range(3)]
    _list_with_files(window, paths)
    row = window.setlist_rows.itemAt(2).widget()
    event = QMouseEvent(QEvent.Type.MouseButtonPress,
                        QPointF(5, 5), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    row.mousePressEvent(event)
    assert window._setlist_cursor == ('planned', 2)
    frames = [window.setlist_rows.itemAt(i).widget().property('browse_cursor')
              for i in range(window.setlist_rows.count())]
    assert frames == [None, None, True]
