"""Pass 37 (user, 2026-11-28: 「ASIOをRBのにできてないのなおっ
た？ヘッドホンもそこからでないし」): the audio ladder grows the
topologies between the 4-channel paths and the PC default —
paired WASAPI endpoints and an RB master-only rung — and the
engine grows a REAL headphone cue bus: monitored decks render
post-EQ / post-gain, pre-crossfader, at deck volume into a cue
buffer whenever the caller provides one.

Proven here, without hardware:
- plan_auto() orders the topologies for each device landscape
  (4ch single / paired 2+2 / single 2ch / none);
- start_audio lands on the right topology against a fake
  sounddevice, records mode + cue in the diagnostics, and the
  status line is honest about the cue pair;
- the engine's cue routing matrix: which output (master / cue)
  carries which deck under each monitor combination, at deck
  volume, unaffected by the crossfader, following deck gain.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

from audio_config import plan_auto
from engine import Engine, RATE, Track, compute_peaks


# ------------------------------------------------------------------
# plan_auto topologies, over a fake sounddevice enumeration
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


def _rig(devices):
    return FakeAudio(HOSTS, devices)


def _kinds(plans):
    return [p['kind'] for p in plans]


def test_plan_single4_on_asio_rb():
    plans = plan_auto(_rig([_device('Speakers', 0),
                            _device('DDJ-RB', 1),
                            _device('DDJ-RB', 2, channels=4)]))
    assert plans[0]['kind'] == 'single4'
    assert plans[0]['device'] == 2 and plans[0]['channels'] == 4
    assert plans[-1]['device'] is None      # system default last


def test_plan_single4_on_wasapi_rb_when_no_asio():
    plans = plan_auto(_rig([_device('Speakers', 0),
                            _device('DDJ-RB', 1, channels=4),
                            _device('Headphones', 1)]))
    assert plans[0]['kind'] == 'single4'
    assert plans[0]['device'] == 1


def test_plan_paired_two_wasapi_rb_endpoints():
    plans = plan_auto(_rig([_device('DDJ-RB', 1),
                            _device('DDJ-RB', 1),
                            _device('Speakers', 0)]))
    kinds = _kinds(plans)
    assert 'paired' in kinds
    paired = plans[kinds.index('paired')]
    assert paired['device'] == 0 and paired['cue_device'] == 1
    # The pair is tried before any master-only rung.
    assert kinds.index('paired') < kinds.index('master_only')


def test_plan_master_only_single_2ch_rb():
    plans = plan_auto(_rig([_device('Speakers', 0),
                            _device('DDJ-RB', 1),
                            _device('Headphones', 1)]))
    kinds = _kinds(plans)
    assert 'single4' not in kinds and 'paired' not in kinds
    assert plans[0]['kind'] == 'master_only'
    assert plans[0]['device'] == 1 and plans[0]['channels'] == 2


def test_plan_no_rb_falls_to_defaults():
    plans = plan_auto(_rig([_device('Speakers', 0),
                            _device('Speakers', 1),
                            _device('Headphones', 1)]))
    kinds = _kinds(plans)
    assert set(kinds) == {'default'}
    assert plans[0]['device'] == 2          # WASAPI default first
    assert plans[-1]['device'] is None       # system default last


# ------------------------------------------------------------------
# The engine cue routing matrix (offline render)
# ------------------------------------------------------------------

def _const_track(value, seconds=5):
    audio = np.full((int(seconds * RATE), 2), value,
                    dtype=np.float32)
    return Track('const', audio, compute_peaks(audio), 120.0,
                 1.0, 0, 1.0)


def _rig_engine():
    """Deck A constant 0.4 at volume 1.0; deck B constant 0.2
    at volume 0.5 (cue contribution 0.1). Both playing."""
    eng = Engine()
    eng.command('load', 0, _const_track(0.4))
    eng.command('load', 1, _const_track(0.2))
    eng.command('volume', 1, 0.5)
    eng.command('play', 0, True)
    eng.command('play', 1, True)
    return eng


def _render(eng, blocks=6):
    cue = np.zeros((512, 2), dtype=np.float32)
    out = None
    for _ in range(blocks):
        out = eng.render(512, cue)
    return out, cue


def test_cue_silent_when_nothing_monitored():
    eng = _rig_engine()
    out, cue = _render(eng)
    assert np.max(np.abs(cue)) == 0.0
    assert np.max(np.abs(out)) > 0.3        # master still plays


def test_cue_carries_monitored_deck_at_deck_volume():
    eng = _rig_engine()
    eng.cue_decks = {0}
    _, cue = _render(eng)
    # Deck A post volume*gain (1.0 * 1.0), PRE crossfader: the
    # crossfader sits at 0.5, so a post-crossfader cue would
    # read ~0.28, not 0.4.
    assert np.allclose(cue, 0.4, atol=0.02)


def test_cue_follows_deck_volume_and_gain():
    eng = _rig_engine()
    eng.cue_decks = {1}
    _, cue = _render(eng)
    assert np.allclose(cue, 0.1, atol=0.02)  # 0.2 * volume 0.5
    eng.command('gain', 1, 0.5)
    _, cue = _render(eng)
    assert np.allclose(cue, 0.05, atol=0.02)


def test_cue_both_decks_sum_and_ignore_crossfader():
    eng = _rig_engine()
    eng.cue_decks = {0, 1}
    eng.command('cross', None, 0.0)          # master: deck A only
    out, cue = _render(eng)
    assert np.allclose(cue, 0.5, atol=0.02)  # both decks, in full
    # The master really did obey the crossfader (A only).
    assert np.allclose(out, 0.4, atol=0.03)


def test_render_without_cue_buffer_unchanged():
    eng = _rig_engine()
    eng.cue_decks = {0, 1}
    out = None
    for _ in range(6):
        out = eng.render(512)
    assert np.max(np.abs(out)) > 0.3


# ------------------------------------------------------------------
# start_audio topologies, against a fake sounddevice
# ------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False

pytestmark_qt = pytest.mark.skipif(not HAVE_QT,
                                   reason='PySide6 unavailable')


class _FakeStream:
    latency = 0.01

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class _FakeSDTable:
    """sounddevice stand-in over a device table; records every
    OutputStream call's device / channels / callback."""

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
        self.calls.append(kwargs)
        if kwargs.get('device') in self.fail_devices:
            raise RuntimeError('cannot open')
        return _FakeStream()


@pytest.fixture(scope='module')
def _qapp():
    return QApplication.instance() or QApplication([])


def _window_with(fake, monkeypatch):
    win = appmod.Window(audio=False)
    monkeypatch.setattr(appmod, 'sd', fake)
    win.devices.setCurrentIndex(0)           # 'auto'
    return win


@pytestmark_qt
def test_start_audio_single4_opens_four_channels(_qapp, monkeypatch):
    fake = _FakeSDTable([_device('Speakers', 0),
                         _device('DDJ-RB', 1),
                         _device('DDJ-RB', 2, channels=4)])
    win = _window_with(fake, monkeypatch)
    try:
        win.start_audio()
        assert win.audio_mode == 'single4'
        assert win._cue_live is True
        assert win.cue_stream is None        # cue rides the master
        master = [c for c in fake.calls if c.get('channels') == 4]
        assert master and master[0]['device'] == 2
        assert win.audio_diag['mode'] == 'single4'
        assert win.audio_diag['cue'] is True
        assert 'ヘッドホンキュー有効' in win._status_fn(win.lang)
    finally:
        win.close()


@pytestmark_qt
def test_start_audio_paired_opens_two_streams(_qapp, monkeypatch):
    fake = _FakeSDTable([_device('DDJ-RB', 1),
                         _device('DDJ-RB', 1),
                         _device('Speakers', 0)])
    win = _window_with(fake, monkeypatch)
    try:
        win.start_audio()
        assert win.audio_mode == 'paired'
        assert win.stream is not None and win.cue_stream is not None
        devices = {c.get('device') for c in fake.calls}
        assert devices == {0, 1}
        assert win.audio_diag['cue'] is True
    finally:
        win.close()


@pytestmark_qt
def test_start_audio_paired_cue_failure_falls_through(_qapp,
                                                      monkeypatch):
    fake = _FakeSDTable([_device('DDJ-RB', 1),
                         _device('DDJ-RB', 1),
                         _device('Speakers', 0)], fail_devices={1})
    win = _window_with(fake, monkeypatch)
    try:
        win.start_audio()
        # The pair cannot live without its cue endpoint; the
        # ladder degrades to the RB master-only rung, honestly.
        assert win.audio_mode == 'master_only'
        assert win._cue_live is False
        assert win.cue_stream is None
    finally:
        win.close()


@pytestmark_qt
def test_start_audio_master_only_status_is_honest(_qapp, monkeypatch):
    fake = _FakeSDTable([_device('Speakers', 0),
                         _device('DDJ-RB', 1),
                         _device('Headphones', 1)])
    win = _window_with(fake, monkeypatch)
    try:
        win.start_audio()
        assert win.audio_mode == 'master_only'
        assert win._cue_live is False
        status = win._status_fn(win.lang)
        assert 'DDJ-RB' in status
        # The status line says plainly: no headphone cue pair.
        assert 'キュー用の出口がありません' in status
        assert win.audio_diag['mode'] == 'master_only'
        assert win.audio_diag['cue'] is False
    finally:
        win.close()
