"""Pass 23 (audio output / ASIO): the 'auto' ladder prefers the
DDJ-RB on its ASIO host, then the DDJ-RB on WASAPI, then the
WASAPI default — the pre-pass-23 behaviour — and explicit picks
are always respected. Verified against a fake sounddevice; no
real device involved.
"""
from audio_config import (asio_ddj_rb_channels, list_outputs,
                          pick_auto, resolve_output)


class FakeAudio:
    """Minimal sounddevice stand-in with full device records."""

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


def test_auto_prefers_ddj_rb_on_asio():
    audio = _rig([_device('Speakers', 0),
                  _device('DDJ-RB', 1),          # WASAPI RB
                  _device('DDJ-RB', 2, channels=4)])  # ASIO RB
    assert pick_auto(audio) == (2, 'ASIO')
    assert resolve_output('auto', audio) == (2, 'ASIO')


def test_auto_falls_to_ddj_rb_on_wasapi_without_asio():
    audio = _rig([_device('Speakers', 0), _device('DDJ-RB', 1),
                  _device('Headphones', 1)])
    assert pick_auto(audio) == (1, 'Windows WASAPI')


def test_auto_keeps_wasapi_default_without_rb():
    audio = _rig([_device('Speakers', 0), _device('Speakers', 1),
                  _device('Headphones', 1)])
    # WASAPI host's default_output_device is index 2 in HOSTS.
    assert pick_auto(audio) == (2, 'Windows WASAPI')


def test_auto_system_default_when_no_wasapi():
    hosts = [dict(name='MME', default_output_device=0),
             dict(name='ASIO', default_output_device=-1)]
    audio = FakeAudio(hosts, [_device('Speakers', 0)])
    device, hostapi = resolve_output('auto', audio)
    assert device is None
    assert hostapi == 'MME'


def test_explicit_selection_respected():
    audio = _rig([_device('Speakers', 0), _device('DDJ-RB', 2)])
    assert resolve_output(0, audio) == (0, 'MME')
    assert resolve_output(None, audio) == (None, 'MME')


def test_asio_ddj_rb_channels_observed_not_used():
    audio = _rig([_device('DDJ-RB', 2, channels=4)])
    assert asio_ddj_rb_channels(audio) == 4
    audio = _rig([_device('DDJ-RB', 1, channels=2)])
    assert asio_ddj_rb_channels(audio) is None


def test_list_outputs_skips_input_only_devices():
    audio = _rig([_device('Mic', 0, channels=0),
                  _device('Speakers', 0, channels=2)])
    out = list_outputs(audio)
    assert [d['name'] for d in out] == ['Speakers']
    assert out[0]['hostapi'] == 'MME'


# ------------------------------------------------------------------
# start_audio's attempt chain, against a fake sounddevice module.
# The shipped PortAudio exposes no ASIO host (CI smoke inventory),
# so WASAPI exclusive is the realistic low-latency path: exclusive
# first, shared on the same device second, system default last —
# and the app never stays silent.
# ------------------------------------------------------------------

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False


class _FakeStream:
    latency = 0.01

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class _FakeSD:
    """sounddevice stand-in: one WASAPI device named DDJ-RB."""

    def __init__(self, fail_extras=(), fail_all=False):
        self.calls = []
        self.fail_extras = set(fail_extras)
        self.fail_all = fail_all

    @staticmethod
    def WasapiSettings(exclusive=False):
        return ('wasapi', bool(exclusive))

    def query_hostapis(self):
        return [dict(name='Windows WASAPI', default_output_device=0)]

    def query_devices(self, device=None, kind=None):
        record = dict(name='DDJ-RB', hostapi=0,
                      max_input_channels=0, max_output_channels=2)
        if device is not None or kind == 'output':
            return record
        return [record]

    def OutputStream(self, **kwargs):
        extra = kwargs.get('extra_settings')
        self.calls.append((kwargs.get('device'), extra))
        if self.fail_all or extra in self.fail_extras:
            raise RuntimeError('cannot open')
        return _FakeStream()


import pytest

pytestmark_qt = pytest.mark.skipif(not HAVE_QT,
                                   reason='PySide6 unavailable')


@pytest.fixture(scope='module')
def _qapp():
    return QApplication.instance() or QApplication([])


@pytestmark_qt
def test_start_audio_tries_exclusive_then_shared(_qapp, monkeypatch):
    win = appmod.Window(audio=False)
    fake = _FakeSD(fail_extras={('wasapi', True)})
    monkeypatch.setattr(appmod, 'sd', fake)
    win.devices.setCurrentIndex(0)   # 'auto'
    win.start_audio()
    try:
        assert fake.calls[0][1] == ('wasapi', True)    # exclusive first
        assert fake.calls[1][1] == ('wasapi', False)   # shared second
        assert win.stream is not None
        assert 'DDJ-RB' in win._status_fn(win.lang)
    finally:
        win.close()


@pytestmark_qt
def test_start_audio_falls_back_to_system_default(_qapp, monkeypatch):
    win = appmod.Window(audio=False)
    fake = _FakeSD(fail_extras={('wasapi', True), ('wasapi', False)})
    monkeypatch.setattr(appmod, 'sd', fake)
    win.devices.setCurrentIndex(0)
    win.start_audio()
    try:
        assert fake.calls[-1] == (None, None)          # default last
        assert win.stream is not None                  # never silent
        assert 'DDJ-RB' in win._status_fn(win.lang)    # fallback names it
    finally:
        win.close()
