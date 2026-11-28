"""Pass 36 (promised to the user 2026-11-28, after the ASIO
diagnosis stalled for hours on fetching audio-diagnostics.json
out of AppData by hand): the 診断を書き出す button beside the
device picker refreshes the ladder record (a fresh open attempt
via start_audio — the pass-25 writer's trigger), copies the file
to the Desktop as MixAble-audio-diagnostics.json, and shows a
dialog whose one-line outcome is read back from that file.

The summary + copy helpers are Qt-free; the click path runs
against the offscreen window with start_audio faked (the VM has
no DDJ-RB) and the Desktop redirected to a tmp dir.
"""
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

import i18n

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    from app import (audio_diag_summary, copy_audio_diagnostics,
                     read_audio_diagnostics, save_audio_diagnostics)
    HAVE_QT = True
except Exception:
    HAVE_QT = False

pytestmark = pytest.mark.skipif(not HAVE_QT,
                                reason='PySide6 unavailable')


# ------------------------------------------------------------------
# Fixture ladder records, shaped exactly like start_audio's diag.
# ------------------------------------------------------------------

RB_DEVICE = dict(index=3, name='DDJ-RB', hostapi='ASIO',
                 max_output_channels=4)

HEALTHY = dict(
    outcome='opened', rb_devices=[RB_DEVICE],
    attempts=[dict(device=3, name='DDJ-RB', hostapi='ASIO',
                   exclusive=False, ok=True, error=None)])

OPENED_DEFAULT = dict(
    outcome='opened', rb_devices=[],
    attempts=[dict(device=None, name=None, hostapi='',
                   exclusive=False, ok=True, error=None)])

RB_FAILED = dict(
    outcome='system_default_fallback', rb_devices=[RB_DEVICE],
    attempts=[dict(device=3, name='DDJ-RB', hostapi='ASIO',
                   exclusive=False, ok=False,
                   error='Error opening OutputStream: Unanticipated '
                         'host error'),
              dict(device=None, name=None, hostapi='',
                   exclusive=False, ok=True, error=None)])

RB_ABSENT = dict(
    outcome='system_default_fallback', rb_devices=[],
    attempts=[dict(device=None, name=None, hostapi='',
                   exclusive=False, ok=True, error=None)])

PLAIN_FALLBACK = dict(
    outcome='system_default_fallback', rb_devices=[RB_DEVICE],
    attempts=[dict(device=5, name='USB Audio', hostapi='WASAPI',
                   exclusive=True, ok=False, error='busy'),
              dict(device=None, name=None, hostapi='',
                   exclusive=False, ok=True, error=None)])

SILENT = dict(
    outcome='silent', rb_devices=[RB_DEVICE],
    attempts=[dict(device=3, name='DDJ-RB', hostapi='ASIO',
                   exclusive=False, ok=False, error='busy'),
              dict(device=None, name=None, hostapi='',
                   exclusive=False, ok=False, error='no default')])


# ------------------------------------------------------------------
# The one-line outcome, read from the record (Qt-free)
# ------------------------------------------------------------------

def test_summary_healthy_names_device_and_host():
    line = audio_diag_summary(HEALTHY, 'jp')
    assert 'DDJ-RB' in line and 'ASIO' in line
    line = audio_diag_summary(HEALTHY, 'en')
    assert 'DDJ-RB' in line and 'ASIO' in line


def test_summary_opened_on_system_default():
    assert 'システム既定' in audio_diag_summary(OPENED_DEFAULT, 'jp')
    assert 'system default' in audio_diag_summary(OPENED_DEFAULT, 'en')


def test_summary_rb_failed_says_so_with_the_error():
    line = audio_diag_summary(RB_FAILED, 'jp')
    assert 'DDJ-RBを開けませんでした' in line
    assert 'Unanticipated host error' in line
    assert 'could not be opened' in audio_diag_summary(RB_FAILED, 'en')


def test_summary_rb_absent_says_not_found():
    assert '見つかりませんでした' in audio_diag_summary(RB_ABSENT, 'jp')
    assert 'not found' in audio_diag_summary(RB_ABSENT, 'en')


def test_summary_plain_fallback_and_silent():
    assert 'システム既定' in audio_diag_summary(PLAIN_FALLBACK, 'jp')
    assert '開けませんでした' in audio_diag_summary(SILENT, 'jp')
    assert 'could be opened' in audio_diag_summary(SILENT, 'en')


def test_summary_unknown_for_empty_records():
    for bad in (None, {}, {'outcome': '???'}, 'not a dict'):
        assert audio_diag_summary(bad, 'jp') == \
            i18n.tr('diag_outcome_unknown', 'jp')


# ------------------------------------------------------------------
# Read / copy helpers (Qt-free)
# ------------------------------------------------------------------

def test_read_roundtrip_and_missing(tmp_path):
    assert read_audio_diagnostics(tmp_path) is None
    save_audio_diagnostics(tmp_path, HEALTHY)
    assert read_audio_diagnostics(tmp_path)['outcome'] == 'opened'
    (tmp_path / appmod.AUDIO_DIAG_FILENAME).write_text(
        '{broken', encoding='utf-8')
    assert read_audio_diagnostics(tmp_path) is None


def test_copy_lands_on_desktop_under_the_findable_name(tmp_path):
    data_dir = tmp_path / 'data'
    desktop = tmp_path / 'Desktop'
    save_audio_diagnostics(data_dir, RB_FAILED)
    dest = copy_audio_diagnostics(data_dir, desktop)
    assert dest == desktop / 'MixAble-audio-diagnostics.json'
    assert json.loads(dest.read_text(encoding='utf-8')) == \
        json.loads((data_dir / appmod.AUDIO_DIAG_FILENAME)
                   .read_text(encoding='utf-8'))
    # A second export overwrites, never duplicates.
    save_audio_diagnostics(data_dir, HEALTHY)
    again = copy_audio_diagnostics(data_dir, desktop)
    assert again == dest
    assert read_audio_diagnostics(data_dir)['outcome'] == 'opened'
    assert json.loads(dest.read_text(encoding='utf-8'))['outcome'] \
        == 'opened'


def test_copy_missing_source_is_none(tmp_path):
    assert copy_audio_diagnostics(tmp_path / 'empty',
                                  tmp_path / 'Desktop') is None
    assert not (tmp_path / 'Desktop').exists()


# ------------------------------------------------------------------
# The click path (offscreen window; start_audio + Desktop faked)
# ------------------------------------------------------------------

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
def rig(window, monkeypatch, tmp_path):
    """The window with a fake refresh (writes `rig['diag']` as
    the ladder record, or nothing when it is None), a tmp data
    dir and a tmp Desktop. Returns (state, desktop_dir)."""
    state = {'diag': None, 'refreshes': 0}
    data_dir = tmp_path / 'data'
    desktop = tmp_path / 'Desktop'

    def fake_start_audio():
        state['refreshes'] += 1
        if state['diag'] is not None:
            save_audio_diagnostics(data_dir, state['diag'])

    FakeBox.calls = []
    monkeypatch.setattr(window, 'start_audio', fake_start_audio)
    monkeypatch.setattr(window, 'data_dir', data_dir)
    monkeypatch.setattr(appmod, 'desktop_dir', lambda: desktop)
    monkeypatch.setattr(appmod, 'QMessageBox', FakeBox)
    return state, desktop


def test_click_rb_failed_writes_desktop_copy_and_dialog(window, rig):
    state, desktop = rig
    state['diag'] = RB_FAILED
    window.diag_button.click()
    assert state['refreshes'] == 1          # refresh came first
    dest = desktop / 'MixAble-audio-diagnostics.json'
    assert dest.is_file()
    kind, title, text = FakeBox.calls[-1]
    assert kind == 'info'
    assert 'MixAble-audio-diagnostics.json' in text
    assert 'DDJ-RBを開けませんでした' in text


def test_click_healthy_dialog_names_the_active_output(window, rig):
    state, desktop = rig
    state['diag'] = HEALTHY
    window.diag_button.click()
    assert (desktop / 'MixAble-audio-diagnostics.json').is_file()
    text = FakeBox.calls[-1][2]
    assert 'DDJ-RB' in text and 'ASIO' in text


def test_click_without_any_record_says_so_plainly(window, rig):
    state, desktop = rig
    state['diag'] = None                    # refresh wrote nothing
    window.diag_button.click()
    assert state['refreshes'] == 1
    assert not (desktop / 'MixAble-audio-diagnostics.json').exists()
    text = FakeBox.calls[-1][2]
    assert text == i18n.tr('diag_missing', 'jp')


def test_button_present_enabled_and_relabels(window):
    assert window.diag_button.isEnabled()
    assert window.diag_button.text() == '診断を書き出す'
    window.set_language('en')
    try:
        assert window.diag_button.text() == 'EXPORT DIAGNOSTICS'
    finally:
        window.set_language('jp')
