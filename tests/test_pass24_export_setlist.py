"""Pass 24, items 4+5 (user reports 2026-11-27):
- 「Liveにデータ書き出しできない」 — the base-.als picker opened
  unexplained in the app's own folder and he stalled. Now: the
  pick is remembered, the picker starts at Live's Templates
  folder and explains itself, unusable files are refused on the
  spot with a plain reason, and every outcome is a message box.
- 「セットリストにドラッグしても音楽は入らない」 — the set-list
  panel takes drops: audio files become real entries, anything
  else is reported by name; a row double-click loads the entry
  onto the standby deck.

The pure halves (validate_base, settings, start dir, entry
building) are tested without Qt dialogs; the window flows run
offscreen with the dialog classes faked.
"""
import gzip
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
import soundfile as sf

import perf_als
from engine import RATE

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False


# ------------------------------------------------------------------
# Fixtures: synthetic .als files
# ------------------------------------------------------------------

def _als(path, tracks_xml):
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<Ableton><LiveSet><Tracks>{tracks_xml}</Tracks>'
           '</LiveSet></Ableton>')
    Path(path).write_bytes(gzip.compress(xml.encode()))
    return str(path)


_DONOR_TRACK = (
    '<AudioTrack><DeviceChain><MainSequencer><ClipSlotList>'
    '<ClipSlot><ClipSlot><Value><AudioClip Id="1"/></Value>'
    '</ClipSlot></ClipSlot></ClipSlotList></MainSequencer>'
    '</DeviceChain></AudioTrack>')
_BARE_TRACK = '<AudioTrack/>'


# ------------------------------------------------------------------
# validate_base (perf_als, Qt-free)
# ------------------------------------------------------------------

def test_validate_base_missing(tmp_path):
    reason, donor = perf_als.validate_base(tmp_path / 'nope.als')
    assert reason == 'missing' and donor is False


def test_validate_base_not_an_als(tmp_path):
    bad = tmp_path / 'notes.als'
    bad.write_text('just some text', encoding='utf-8')
    reason, _ = perf_als.validate_base(bad)
    assert reason == 'not_als'


def test_validate_base_needs_two_audio_tracks(tmp_path):
    path = _als(tmp_path / 'one.als', _BARE_TRACK)
    reason, _ = perf_als.validate_base(path)
    assert reason == 'few_tracks'


def test_validate_base_ok_without_donor(tmp_path):
    path = _als(tmp_path / 'ok.als', _BARE_TRACK + _BARE_TRACK)
    reason, donor = perf_als.validate_base(path)
    assert reason is None and donor is False


def test_validate_base_ok_with_donor(tmp_path):
    path = _als(tmp_path / 'donor.als', _DONOR_TRACK + _BARE_TRACK)
    reason, donor = perf_als.validate_base(path)
    assert reason is None and donor is True


# ------------------------------------------------------------------
# Settings + picker start dir (app module, Qt-free halves)
# ------------------------------------------------------------------

@pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')
def test_settings_roundtrip(tmp_path):
    assert appmod.load_app_settings(tmp_path) == {}
    appmod.save_app_settings(tmp_path, {'live_base_als': 'C:/x.als'})
    assert appmod.load_app_settings(tmp_path) == {'live_base_als': 'C:/x.als'}
    (tmp_path / appmod.SETTINGS_FILENAME).write_text('{broken',
                                                     encoding='utf-8')
    assert appmod.load_app_settings(tmp_path) == {}


@pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')
def test_live_base_start_dir_prefers_templates(tmp_path):
    home = tmp_path / 'home'
    templates = home / 'Documents' / 'Ableton' / 'User Library' / 'Templates'
    templates.mkdir(parents=True)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    settings = {'live_base_dir': str(elsewhere)}
    assert appmod.live_base_start_dir(settings, home=home) == str(templates)
    templates.rmdir()
    assert appmod.live_base_start_dir(settings, home=home) == str(elsewhere)
    assert appmod.live_base_start_dir({}, home=home) == ''


# ------------------------------------------------------------------
# Entry building from a dropped file (Qt-free)
# ------------------------------------------------------------------

@pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')
def test_file_track_entry_uses_tags_then_filename(tmp_path):
    tagged = tmp_path / 'whatever.wav'
    with sf.SoundFile(str(tagged), 'w', samplerate=RATE, channels=2) as f:
        f.title = 'Tag Title'
        f.artist = 'Tag Artist'
        f.write(np.zeros((RATE * 2, 2), dtype=np.float32))
    entry = appmod.file_track_entry(tagged)
    assert entry['title'] == 'Tag Title'
    assert entry['artist'] == 'Tag Artist'
    assert entry['duration_seconds'] == 2
    assert entry['path'] == str(tagged)

    plain = tmp_path / 'Some Artist - Some Song.wav'
    sf.write(str(plain), np.zeros((RATE, 2), dtype=np.float32), RATE)
    entry = appmod.file_track_entry(plain)
    assert entry['title'] == 'Some Song'
    assert entry['artist'] == 'Some Artist'
    assert entry['duration_seconds'] == 1


# ------------------------------------------------------------------
# Window flows (offscreen, dialogs faked)
# ------------------------------------------------------------------

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


class FakeBox:
    calls = []

    @staticmethod
    def information(parent, title, text):
        FakeBox.calls.append(('info', title, text))

    @staticmethod
    def warning(parent, title, text):
        FakeBox.calls.append(('warn', title, text))


@pytest.fixture()
def boxes(monkeypatch):
    FakeBox.calls = []
    monkeypatch.setattr(appmod, 'QMessageBox', FakeBox)
    return FakeBox.calls


def _fresh_settings(window, tmp_path):
    window.data_dir = tmp_path
    window.settings = {}
    window.journal.history.clear()
    window.deck_a_bpm = 122.0
    window.setlist_data = None


def test_drop_adds_entries_and_reports_rejects(window, tmp_path):
    _fresh_settings(window, tmp_path)
    song = tmp_path / 'Drop Artist - Drop Song.wav'
    sf.write(str(song), np.zeros((RATE, 2), dtype=np.float32), RATE)
    junk = tmp_path / 'notes.txt'
    junk.write_text('x', encoding='utf-8')
    window.add_dropped_to_setlist([str(song), str(junk)])
    assert window.setlist_data is not None
    tracks = window.setlist_data['tracks']
    assert len(tracks) == 1
    assert tracks[0]['title'] == 'Drop Song'
    assert tracks[0]['path'] == str(song)
    assert len(window.clip_tracks) == 1
    status = window._status_fn(window.lang)
    assert '1 曲追加' in status
    assert 'notes.txt' in status
    # The panel itself carries the drop handler.
    assert window.setlist_panel.drop_handler is not None
    # Rows are double-clickable SetlistRow frames now.
    from app import SetlistRow
    rows = [window.setlist_rows.itemAt(i).widget()
            for i in range(window.setlist_rows.count())]
    assert any(isinstance(r, SetlistRow) for r in rows)


def test_double_click_entry_without_file_says_so(window, tmp_path,
                                                 monkeypatch):
    _fresh_settings(window, tmp_path)
    window.setlist_data = {'tracks': [{'title': 'Phone Only',
                                       'path': None}]}
    loaded = []
    monkeypatch.setattr(window, 'load',
                        lambda i, p: loaded.append((i, p)))
    window.load_setlist_entry(0)
    assert loaded == []
    assert window._status_fn(window.lang) == \
        appmod.i18n.tr('setlist_entry_no_file', window.lang)


def test_double_click_loads_the_standby_deck(window, tmp_path,
                                             monkeypatch):
    _fresh_settings(window, tmp_path)
    song = tmp_path / 'song.wav'
    sf.write(str(song), np.zeros((RATE, 2), dtype=np.float32), RATE)
    window.setlist_data = {'tracks': [{'title': 'Song',
                                       'path': str(song)}]}
    loaded = []
    monkeypatch.setattr(window, 'load',
                        lambda i, p: loaded.append((i, p)))
    window.load_setlist_entry(0)
    assert loaded == [(0, str(song))]       # both stopped: Deck A
    window.engine.decks[0]['playing'] = True
    try:
        window.load_setlist_entry(0)
        assert loaded[-1] == (1, str(song))  # A playing: standby is B
    finally:
        window.engine.decks[0]['playing'] = False


def test_export_without_performance_explains(window, tmp_path, boxes,
                                             monkeypatch):
    _fresh_settings(window, tmp_path)
    monkeypatch.setattr(appmod.Window, '_ask_live_base',
                        lambda self: pytest.fail('picker must not open'))
    window.export_live()
    assert boxes and boxes[0][0] == 'info'
    assert '演奏の記録がありません' in boxes[0][2]


def test_export_uses_the_remembered_base(window, tmp_path, boxes,
                                         monkeypatch):
    _fresh_settings(window, tmp_path)
    base = _als(tmp_path / 'base.als', _DONOR_TRACK + _BARE_TRACK)
    window.settings['live_base_als'] = base
    window.journal.history.append({'action': 'load', 'deck': 0,
                                   'value': 'x.wav'})
    monkeypatch.setattr(appmod.Window, '_ask_live_base',
                        lambda self: pytest.fail('picker must not open'))
    out_path = str(tmp_path / 'out.als')

    class FakeDialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return out_path, ''
    monkeypatch.setattr(appmod, 'QFileDialog', FakeDialog)
    seen = {}

    def fake_generate(base_arg, history, out_arg, **kwargs):
        seen.update(base=base_arg, out=out_arg)
        return {'clips_written': 3,
                'envelopes_written': {'volume_A': [1, 2]},
                'warnings': []}
    monkeypatch.setattr(appmod.perf_als, 'generate', fake_generate)
    window.export_live()
    assert seen == {'base': base, 'out': out_path}
    assert boxes and boxes[0][0] == 'info'
    assert out_path in boxes[0][2]
    assert base in boxes[0][2]


def test_export_repicks_a_bad_remembered_base(window, tmp_path, boxes,
                                              monkeypatch):
    _fresh_settings(window, tmp_path)
    bad = tmp_path / 'bad.als'
    bad.write_text('not a set', encoding='utf-8')
    good = _als(tmp_path / 'good.als', _DONOR_TRACK + _BARE_TRACK)
    window.settings['live_base_als'] = str(bad)
    window.journal.history.append({'action': 'load', 'deck': 0,
                                   'value': 'x.wav'})
    monkeypatch.setattr(appmod.Window, '_ask_live_base',
                        lambda self: good)
    out_path = str(tmp_path / 'out.als')

    class FakeDialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return out_path, ''
    monkeypatch.setattr(appmod, 'QFileDialog', FakeDialog)
    monkeypatch.setattr(appmod.perf_als, 'generate',
                        lambda *a, **k: {'clips_written': 1,
                                         'envelopes_written': {},
                                         'warnings': []})
    window.export_live()
    # The bad memory was reported, then replaced by the new pick.
    assert any(c[0] == 'warn' and '使えませんでした' in c[2]
               for c in boxes)
    assert window.settings['live_base_als'] == good
    assert appmod.load_app_settings(tmp_path)['live_base_als'] == good


def test_export_failure_is_a_message_box(window, tmp_path, boxes,
                                         monkeypatch):
    _fresh_settings(window, tmp_path)
    base = _als(tmp_path / 'base.als', _DONOR_TRACK + _BARE_TRACK)
    window.settings['live_base_als'] = base
    window.journal.history.append({'action': 'load', 'deck': 0,
                                   'value': 'x.wav'})
    out_path = str(tmp_path / 'out.als')

    class FakeDialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return out_path, ''
    monkeypatch.setattr(appmod, 'QFileDialog', FakeDialog)

    def boom(*a, **k):
        raise RuntimeError('kaboom')
    monkeypatch.setattr(appmod.perf_als, 'generate', boom)
    window.export_live()
    assert boxes and boxes[0][0] == 'warn'
    assert 'kaboom' in boxes[0][2]
