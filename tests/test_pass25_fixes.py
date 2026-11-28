"""Pass 25 (user reports, 2026-11-28 morning):
- 「RBのへっどふぉんとかは使えない PC本体から音出る」 — the audio
  ladder fell to the PC's own speakers with no explanation. Now
  the RB name match is broader, the ladder is diagnosable
  (rung states + open errors, written to audio-diagnostics.json
  and shown on the device picker), and 'auto' keeps retrying
  like MIDI does.
- 「トラックリストにドラックドロップできない」 — pass 24's panel
  never called setAcceptDrops(True), so a real drag was never
  delivered. Now the panel and every descendant accept drops
  behind one event filter with a hover highlight, folders open
  up, and a file-less drop still explains itself.
- The Live base: a template.als placed beside the app is used
  automatically (his proposal: 「もう最初のファイルの中に組み
  込んでおいて」), ahead of the remembered base and the picker.
"""
import gzip
import json
import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
import soundfile as sf

import perf_als
from audio_config import (asio_ddj_rb_channels, diagnose_ladder,
                          pick_auto, rb_name_match)
from engine import RATE

try:
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QApplication, QWidget
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False


# ------------------------------------------------------------------
# RB name matching + ladder diagnosis (Qt-free)
# ------------------------------------------------------------------

class FakeAudio:
    def __init__(self, hosts, devices):
        self._hosts = hosts
        self._devices = devices

    def query_hostapis(self):
        return self._hosts

    def query_devices(self, device=None, kind=None):
        if device is not None:
            return self._devices[device]
        if kind == 'output':
            for d in self._devices:
                if d['max_output_channels'] >= 1:
                    return d
            raise ValueError('no output device')
        return self._devices


def _device(name, hostapi, channels=2):
    return dict(name=name, hostapi=hostapi,
                max_input_channels=0, max_output_channels=channels)


HOSTS = [dict(name='MME', default_output_device=0),
         dict(name='Windows WASAPI', default_output_device=2),
         dict(name='ASIO', default_output_device=-1)]


def test_rb_name_match_variants():
    assert rb_name_match('DDJ-RB', 'ASIO')
    assert rb_name_match('DDJ RB', 'Windows WASAPI')     # space
    assert rb_name_match('DDJ_RB ASIO', 'ASIO')          # underscore
    assert rb_name_match('PIONEER DDJ-RB', 'ASIO')
    assert rb_name_match('PIONEER DJ', 'ASIO')           # maker only, ASIO
    assert rb_name_match('Pioneer RB controller', 'Windows WASAPI')
    assert not rb_name_match('Pioneer Speakers', 'Windows WASAPI')
    assert not rb_name_match('Speakers', 'ASIO')
    assert not rb_name_match('', 'ASIO')


def test_pick_auto_catches_space_spelled_asio_rb():
    audio = FakeAudio(HOSTS, [_device('Speakers', 0),
                              _device('DDJ RB', 2, channels=4)])
    assert pick_auto(audio) == (1, 'ASIO')
    assert asio_ddj_rb_channels(audio) == 4


def test_diagnose_ladder_reports_rungs():
    audio = FakeAudio(HOSTS, [_device('Speakers', 0),
                              _device('DDJ-RB', 1),
                              _device('DDJ RB', 2, channels=4)])
    diag = diagnose_ladder(audio)
    assert [d['name'] for d in diag['rb_devices']] == ['DDJ-RB', 'DDJ RB']
    rungs = {r['rung']: r for r in diag['rungs']}
    assert rungs['asio_rb']['status'] == 'selected'
    assert rungs['asio_rb']['device'] == 2
    assert rungs['wasapi_rb']['status'] == 'present'
    assert diag['pick'] == {'device': 2, 'hostapi': 'ASIO'}


def test_diagnose_ladder_without_rb():
    audio = FakeAudio(HOSTS, [_device('Speakers', 0),
                              _device('Speakers', 1),
                              _device('Headphones', 1)])
    diag = diagnose_ladder(audio)
    assert diag['rb_devices'] == []
    rungs = {r['rung']: r for r in diag['rungs']}
    assert rungs['asio_rb']['status'] == 'not_present'
    assert rungs['wasapi_rb']['status'] == 'not_present'
    assert rungs['wasapi_default']['status'] == 'selected'


# ------------------------------------------------------------------
# Window flows (offscreen)
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


class _FakeStream:
    latency = 0.01

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class FakeSD:
    """sounddevice stand-in with a controllable device table.
    fail_devices: indices whose OutputStream raises."""

    def __init__(self, devices, fail_devices=()):
        self._devices = devices
        self.fail_devices = set(fail_devices)
        self.calls = []

    @staticmethod
    def WasapiSettings(exclusive=False):
        return ('wasapi', bool(exclusive))

    def query_hostapis(self):
        return HOSTS

    def query_devices(self, device=None, kind=None):
        if device is not None:
            return self._devices[device]
        if kind == 'output':
            return self._devices[0]
        return self._devices

    def OutputStream(self, **kwargs):
        device = kwargs.get('device')
        self.calls.append(device)
        if device in self.fail_devices:
            raise RuntimeError('Error opening stream: device busy')
        return _FakeStream()


def _rig_speakers_only():
    return FakeSD([_device('Speakers', 0), _device('Speakers', 1),
                   _device('Headphones', 1)])


def _rig_asio_rb(fail=()):
    return FakeSD([_device('Speakers', 0), _device('DDJ RB', 2, 4),
                   _device('Speakers', 1)], fail_devices=fail)


def _use_auto(window, monkeypatch, fake, tmp_path):
    monkeypatch.setattr(appmod, 'sd', fake)
    window.data_dir = tmp_path
    window.devices.setCurrentIndex(0)   # 'auto'


def test_start_audio_records_why_the_rb_rung_failed(window, tmp_path,
                                                    monkeypatch):
    fake = _rig_asio_rb(fail={1})
    _use_auto(window, monkeypatch, fake, tmp_path)
    window.start_audio()
    diag = window.audio_diag
    assert diag['outcome'] == 'system_default_fallback'
    attempt = [a for a in diag['attempts'] if a['device'] == 1][0]
    assert attempt['ok'] is False
    assert 'device busy' in attempt['error']
    # The record is written beside the settings for him to send.
    written = json.loads((tmp_path / appmod.AUDIO_DIAG_FILENAME)
                         .read_text(encoding='utf-8'))
    assert written['outcome'] == 'system_default_fallback'
    assert written['rb_devices'][0]['name'] == 'DDJ RB'
    # Status bar: fallback + the busy possibility, in plain words.
    status = window._status_fn(window.lang)
    assert '使用中の可能性' in status
    # Picker tooltip carries the failure and its error text.
    assert 'device busy' in window.devices.toolTip()
    assert window._audio_on_rb is False


def test_start_audio_on_rb_marks_state(window, tmp_path, monkeypatch):
    fake = _rig_asio_rb()
    _use_auto(window, monkeypatch, fake, tmp_path)
    window.start_audio()
    assert window.audio_diag['outcome'] == 'opened'
    assert window._audio_on_rb is True
    assert '使用中' in window.devices.toolTip()


def test_start_audio_without_rb_has_no_busy_hint(window, tmp_path,
                                                 monkeypatch):
    fake = _rig_speakers_only()
    _use_auto(window, monkeypatch, fake, tmp_path)
    window.start_audio()
    assert window.audio_diag['rb_devices'] == []
    assert '見つかりません' in window.devices.toolTip()
    assert '使用中の可能性' not in window._status_fn(window.lang)


def test_audio_retry_picks_up_a_late_rb(window, tmp_path, monkeypatch):
    fake = _rig_speakers_only()
    _use_auto(window, monkeypatch, fake, tmp_path)
    window.start_audio()
    assert window._audio_on_rb is False
    assert window.stream is not None
    # The RB appears (plugged in / driver freed): the signature
    # changes, so the next retry window re-runs the ladder.
    fake2 = _rig_asio_rb()
    monkeypatch.setattr(appmod, 'sd', fake2)
    window._audio_retry_due = 0.0
    window._maybe_retry_audio(time.monotonic())
    assert fake2.calls == [1]
    assert window._audio_on_rb is True


def test_audio_retry_fires_when_midi_connects(window, tmp_path,
                                              monkeypatch):
    fake = _rig_asio_rb(fail={1})
    _use_auto(window, monkeypatch, fake, tmp_path)
    window.start_audio()
    calls_before = len(fake.calls)
    assert window._audio_on_rb is False
    # MIDI connecting proves the hardware is awake — retry now,
    # and this time the driver is free.
    fake.fail_devices.clear()
    window.midi.connected = True
    try:
        window._maybe_retry_audio(time.monotonic())
    finally:
        window.midi.connected = False
    assert len(fake.calls) > calls_before
    assert window._audio_on_rb is True


def test_audio_retry_respects_an_explicit_pick(window, tmp_path,
                                               monkeypatch):
    fake = _rig_speakers_only()
    _use_auto(window, monkeypatch, fake, tmp_path)
    window.start_audio()
    window.stream = None            # even a dead stream must not
    window.devices.setCurrentIndex(1)   # 'System default' (None)
    calls_before = len(fake.calls)
    window._audio_retry_due = 0.0
    window._maybe_retry_audio(time.monotonic())
    assert len(fake.calls) == calls_before


# ------------------------------------------------------------------
# Set-list drops: filter on children, folders, empty drops
# ------------------------------------------------------------------

def _drop_event(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction,
                       mime, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    event._mime_keepalive = mime   # the event only borrows the mime
    return event


def _drag_event(paths):
    from PySide6.QtGui import QDragEnterEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDragEnterEvent(QPointF(5, 5), Qt.DropAction.CopyAction,
                            mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    event._mime_keepalive = mime
    return event


def _fresh_setlist(window):
    window.setlist_data = None
    window.clip_tracks = []
    window.render_setlist()


def test_every_panel_descendant_accepts_drops(window):
    _fresh_setlist(window)
    panel = window.setlist_panel
    assert panel.acceptDrops()
    descendants = panel.findChildren(QWidget)
    assert descendants
    assert all(w.acceptDrops() for w in descendants)


def test_drop_landing_on_a_child_widget_adds_the_file(window, tmp_path):
    _fresh_setlist(window)
    song = tmp_path / 'Child Drop - Song.wav'
    sf.write(str(song), np.zeros((RATE, 2), dtype=np.float32), RATE)
    panel = window.setlist_panel
    child = panel.findChildren(QWidget)[-1]
    filt = window._setlist_drop_filter
    # Hover first: the panel lights up so the target reads live.
    assert filt.eventFilter(child, _drag_event([str(song)])) is True
    assert panel.property('drop_active') is True
    event = _drop_event([str(song)])
    assert filt.eventFilter(child, event) is True
    assert event.isAccepted()
    assert panel.property('drop_active') is False
    tracks = window.setlist_data['tracks']
    assert len(tracks) == 1
    assert tracks[0]['path'] == str(song)


def test_folder_drop_adds_the_audio_inside(window, tmp_path):
    _fresh_setlist(window)
    folder = tmp_path / 'crate'
    folder.mkdir()
    for name in ('One.wav', 'Two.flac'):
        sf.write(str(folder / name),
                 np.zeros((RATE, 2), dtype=np.float32), RATE)
    (folder / 'notes.txt').write_text('x', encoding='utf-8')
    nested = folder / 'sub'
    nested.mkdir()
    sf.write(str(nested / 'Deep.wav'),
             np.zeros((RATE, 2), dtype=np.float32), RATE)
    window.add_dropped_to_setlist([str(folder)])
    titles = sorted(t['title'] for t in window.setlist_data['tracks'])
    # Non-recursive: the nested file stays out, by design.
    assert titles == ['One', 'Two']


def test_folder_without_audio_is_reported(window, tmp_path):
    _fresh_setlist(window)
    folder = tmp_path / 'empty crate'
    folder.mkdir()
    (folder / 'readme.txt').write_text('x', encoding='utf-8')
    window.add_dropped_to_setlist([str(folder)])
    assert window.setlist_data is None
    assert 'empty crate' in window._status_fn(window.lang)


def test_drop_with_no_local_files_explains_itself(window):
    _fresh_setlist(window)
    window.add_dropped_to_setlist([])
    assert window._status_fn(window.lang) == \
        appmod.i18n.tr('setlist_drop_no_local', window.lang)


# ------------------------------------------------------------------
# Bundled template.als (beside the app) beats remembered + picker
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


def test_bundled_template_path_lookup(tmp_path):
    assert appmod.bundled_template_path(roots=[tmp_path]) is None
    placed = Path(_als(tmp_path / 'template.als',
                       _DONOR_TRACK + _BARE_TRACK))
    assert appmod.bundled_template_path(roots=[tmp_path]) == placed


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


def _export_ready(window, tmp_path):
    window.data_dir = tmp_path
    window.settings = {}
    window.journal.history.clear()
    window.journal.history.append({'action': 'load', 'deck': 0,
                                   'value': 'x.wav'})
    window.deck_a_bpm = 122.0
    window.setlist_data = None


def _fake_save_dialog(monkeypatch, out_path):
    class FakeDialog:
        @staticmethod
        def getSaveFileName(*a, **k):
            return out_path, ''
    monkeypatch.setattr(appmod, 'QFileDialog', FakeDialog)


def test_export_prefers_the_bundled_template(window, tmp_path, boxes,
                                             monkeypatch):
    _export_ready(window, tmp_path)
    bundled = _als(tmp_path / 'template.als',
                   _DONOR_TRACK + _BARE_TRACK)
    remembered = _als(tmp_path / 'remembered.als',
                      _DONOR_TRACK + _BARE_TRACK)
    window.settings['live_base_als'] = remembered
    monkeypatch.setattr(appmod, 'bundled_template_path',
                        lambda: Path(bundled))
    monkeypatch.setattr(appmod.Window, '_ask_live_base',
                        lambda self: pytest.fail('picker must not open'))
    out_path = str(tmp_path / 'out.als')
    _fake_save_dialog(monkeypatch, out_path)
    seen = {}

    def fake_generate(base_arg, history, out_arg, **kwargs):
        seen.update(base=base_arg, out=out_arg)
        return {'clips_written': 1, 'envelopes_written': {},
                'warnings': []}
    monkeypatch.setattr(appmod.perf_als, 'generate', fake_generate)
    window.export_live()
    assert seen['base'] == bundled


def test_export_bad_bundled_template_warns_and_falls_back(
        window, tmp_path, boxes, monkeypatch):
    _export_ready(window, tmp_path)
    bad = tmp_path / 'template.als'
    bad.write_text('not a set', encoding='utf-8')
    good = _als(tmp_path / 'good.als', _DONOR_TRACK + _BARE_TRACK)
    window.settings['live_base_als'] = good
    monkeypatch.setattr(appmod, 'bundled_template_path',
                        lambda: bad)
    monkeypatch.setattr(appmod.Window, '_ask_live_base',
                        lambda self: pytest.fail('picker must not open'))
    out_path = str(tmp_path / 'out.als')
    _fake_save_dialog(monkeypatch, out_path)
    seen = {}

    def fake_generate(base_arg, history, out_arg, **kwargs):
        seen.update(base=base_arg)
        return {'clips_written': 1, 'envelopes_written': {},
                'warnings': []}
    monkeypatch.setattr(appmod.perf_als, 'generate', fake_generate)
    window.export_live()
    assert any(c[0] == 'warn' and 'template.als' in c[2]
               for c in boxes)
    assert seen['base'] == good
