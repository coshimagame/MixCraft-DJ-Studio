"""Pass 39 (user, 2026-11-27: performance + recording complete
in the app, Live as the post-fix studio): in-app master WAV
recording.

- WavRecorder writes the blocks it is fed (48 kHz stereo
  float), off the calling thread; start/stop are idempotent;
  a write failure is recorded, never raised;
- the window's REC toggle records what the callback renders:
  an offline synthetic performance lands in a WAV whose length
  and energy match the mix; rec_start / rec_stop reach the
  journal;
- with no stream open the toggle explains itself in the status
  bar and records nothing;
- performance.reconstruct treats rec events as inert — the
  Live export ignores them.
"""
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
import soundfile as sf

import performance
from app import WavRecorder
from engine import RATE

FRAMES = 512


# ------------------------------------------------------------------
# WavRecorder unit behaviour
# ------------------------------------------------------------------

def test_recorder_writes_fed_blocks(tmp_path):
    path = tmp_path / 'take.wav'
    rec = WavRecorder()
    rec.start(path)
    block = np.full((FRAMES, 2), 0.25, dtype=np.float32)
    for _ in range(10):
        rec.feed(block)
    assert rec.stop() == path
    data, rate = sf.read(str(path), dtype='float32')
    assert rate == RATE and data.shape == (10 * FRAMES, 2)
    assert np.allclose(data, 0.25, atol=1e-6)
    assert rec.frames == 10 * FRAMES
    assert rec.error is None


def test_recorder_start_stop_idempotent(tmp_path):
    rec = WavRecorder()
    assert rec.stop() is None or rec.path is None
    path = tmp_path / 'take.wav'
    assert rec.start(path) == path
    assert rec.start(path) == path          # second start: no-op
    rec.feed(np.zeros((FRAMES, 2), dtype=np.float32))
    assert rec.stop() == path
    assert rec.stop() == path                # second stop: no-op
    assert path.exists()


def test_recorder_write_error_is_recorded_not_raised(tmp_path):
    blocker = tmp_path / 'blocked'
    blocker.write_text('a file, not a folder', encoding='utf-8')
    rec = WavRecorder()
    rec.start(blocker / 'take.wav')          # parent is a file
    rec.feed(np.zeros((FRAMES, 2), dtype=np.float32))
    deadline = time.time() + 5
    while rec.error is None and time.time() < deadline:
        time.sleep(0.02)
    assert rec.error is not None
    rec.stop()                               # must not raise


# ------------------------------------------------------------------
# The window toggle, offline
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


class _FakeSD:
    @staticmethod
    def WasapiSettings(exclusive=False):
        return ('wasapi', bool(exclusive))

    def query_hostapis(self):
        return [dict(name='Windows WASAPI', default_output_device=0)]

    def query_devices(self, device=None, kind=None):
        record = dict(name='DDJ-RB', hostapi=0, max_input_channels=0,
                      max_output_channels=2)
        if device is not None or kind == 'output':
            return record
        return [record]

    def OutputStream(self, **kwargs):
        return _FakeStream()


@pytest.fixture(scope='module')
def _qapp():
    return QApplication.instance() or QApplication([])


def _const_track(value=0.4, seconds=5):
    from engine import Track, compute_peaks
    audio = np.full((int(seconds * RATE), 2), value,
                    dtype=np.float32)
    return Track('const', audio, compute_peaks(audio), 120.0,
                 1.0, 0, 1.0)


@pytestmark_qt
def test_window_recording_captures_the_mix(_qapp, monkeypatch,
                                           tmp_path):
    win = appmod.Window(audio=False)
    monkeypatch.setattr(appmod, 'sd', _FakeSD())
    monkeypatch.setattr(appmod.QMessageBox, 'information',
                        staticmethod(lambda *a, **k: None))
    win.data_dir = tmp_path
    win.devices.setCurrentIndex(0)
    win.start_audio()
    try:
        assert win.stream is not None
        win.engine.command('load', 0, _const_track())
        win.engine.command('play', 0, True)
        win.toggle_recording()
        assert win._recording is True
        outdata = np.zeros((FRAMES, 2), dtype=np.float32)
        for _ in range(20):
            win.callback(outdata, FRAMES, None, None)
        win.toggle_recording()               # stop -> dialog stubbed
        assert win._recording is False
        takes = list(tmp_path.glob('MixAble-*.wav'))
        assert len(takes) == 1
        data, rate = sf.read(str(takes[0]), dtype='float32')
        assert rate == RATE
        # ~20 blocks were rendered; the take holds them all.
        assert len(data) == 20 * FRAMES
        # The mix's energy: a 0.4 constant through cross 0.5
        # (gain cos(pi/4)) and master 1.0.
        assert np.allclose(data[FRAMES:], 0.4 * np.cos(np.pi / 4),
                           atol=0.02)
        # The journal carries the toggle.
        win.engine.apply_pending()
        win.journal.drain(win.engine)
        actions = [e['action'] for e in win.journal.history]
        assert 'rec_start' in actions and 'rec_stop' in actions
    finally:
        win.close()


@pytestmark_qt
def test_recording_toggle_without_stream_is_honest(_qapp,
                                                   tmp_path):
    win = appmod.Window(audio=False)
    win.data_dir = tmp_path
    try:
        assert win.stream is None
        win.toggle_recording()
        assert win._recording is False
        assert win.rec_button.isChecked() is False
        assert '録音できません' in win._status_fn(win.lang)
        assert list(tmp_path.glob('MixAble-*.wav')) == []
    finally:
        win.close()


# ------------------------------------------------------------------
# The Live export ignores rec events
# ------------------------------------------------------------------

def _ev(frame, action, deck=None, value=None):
    return dict(frame=frame, sample_rate=RATE, action=action,
                deck=deck, value=value)


def test_reconstruct_ignores_rec_events():
    base = [_ev(0, 'load', 0, '/music/a.wav'),
            _ev(1000, 'set_bpm_anchor', None, 125.0),
            _ev(2000, 'play', 0, True),
            _ev(400000, 'volume', 0, 0.7)]
    with_rec = [_ev(0, 'load', 0, '/music/a.wav'),
                _ev(1000, 'set_bpm_anchor', None, 125.0),
                _ev(1500, 'rec_start', None, '/data/MixAble-x.wav'),
                _ev(2000, 'play', 0, True),
                _ev(400000, 'volume', 0, 0.7),
                _ev(500000, 'rec_stop', None, '/data/MixAble-x.wav')]
    durations = {'/music/a.wav': 300.0}
    bpms = {'/music/a.wav': 125.0}
    plain = performance.reconstruct(base, durations=durations,
                                    bpms=bpms, set_bpm=125.0)
    recd = performance.reconstruct(with_rec, durations=durations,
                                   bpms=bpms, set_bpm=125.0)
    assert recd['segments'] == plain['segments']
    assert len(recd['segments'][0]) == 1
