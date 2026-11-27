"""Pass 21 (performance capture robustness): the journal must
not lose a performance when no audio stream is running.

The engine mints journal events in _apply, which the audio
callback normally drives. With a dead/absent device there is no
callback — Engine.apply_pending (pumped by Window.tick when the
stream is None) applies the queue so the gestures still land in
the journal. The journal itself appends + flushes on every
drain (Window.tick, every 40 ms), so a crash keeps everything
up to the last tick.
"""
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from engine import Engine, Journal


def test_apply_pending_mints_events_without_render():
    engine = Engine()
    engine.command('volume', 0, 0.5)
    engine.command('cross', value=0.25)
    assert engine.events.empty()      # nothing applied yet
    engine.apply_pending()
    assert not engine.events.empty()


def test_journal_flushes_events_to_disk_incrementally(tmp_path):
    engine = Engine()
    journal = Journal(tmp_path / 'session.jsonl')
    engine.command('play', 0, True)
    engine.apply_pending()
    journal.drain(engine)
    # The line is on disk already — no export/close needed.
    lines = (tmp_path / 'session.jsonl').read_text(
        encoding='utf-8').strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])['action'] == 'play'
    journal.file.close()


def test_window_tick_pumps_queue_when_stream_dead():
    try:
        from PySide6.QtWidgets import QApplication
        import app as appmod
    except Exception:
        pytest.skip('PySide6 unavailable')
    qapp = QApplication.instance() or QApplication([])
    try:
        window = appmod.Window(audio=False)
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    assert window.stream is None
    window.engine.command('cross', value=0.25)
    window.tick()
    actions = [e['action'] for e in window.journal.history]
    assert 'cross' in actions
    window.close()
    qapp.processEvents()
