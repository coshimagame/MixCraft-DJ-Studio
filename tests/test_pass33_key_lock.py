"""Pass 33 (user request 2026-11-28: 「bpm変えても音変わらないよう
にできる？」): KEY LOCK — pitch-preserving time-stretch per deck.

The engine used to resample (vinyl-style: rate changes pitch).
With key lock ON (the default) a WSOLA stage in the read path
keeps the pitch at the song's own while the tempo follows the
rate; OFF restores the resample path exactly. These tests prove
the behaviour numerically — the user can otherwise only judge by
ear:

- pitch invariance at rates 0.95 / 1.0 / 1.05 with key lock ON
  (dominant frequency within 0.5% of the source tone) while the
  consumed-source duration scales by 1/rate (deck position
  accounting identical to the resample path);
- pitch following the rate with key lock OFF (the old behaviour);
- a beat grid staying on the SET BPM timeline with key lock on:
  beep times land at beat/rate and the beeps keep their pitch;
- jump hygiene: seek / cue / loop during stretch produce no
  NaN/runaway buffers and no smear across the jump (a two-tone
  track measures the NEW side's pitch right after the jump);
- the toggle is journalled and replays as state.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from scipy.signal import find_peaks

from engine import Engine, RATE, Track, compute_peaks
import performance


# ------------------------------------------------------------------
# Synthetic material + measurement helpers
# ------------------------------------------------------------------

def tone_track(freq=440.0, seconds=30, chord=(), name='tone'):
    t = np.arange(int(seconds * RATE)) / RATE
    mono = np.sin(2 * np.pi * freq * t)
    for f in chord:
        mono = mono + np.sin(2 * np.pi * f * t)
    mono = (mono / (1 + len(chord)) * 0.8).astype(np.float32)
    audio = np.column_stack([mono, mono]).astype(np.float32)
    return Track(name, audio, compute_peaks(audio), 120.0, 1.0, 0, 1.0)


def two_tone_track():
    """440 Hz for the first 10 s, 660 Hz after — a jump from one
    half to the other is audible in the measurement as a pitch
    change, and any smear across the jump would read as a mix."""
    t = np.arange(30 * RATE) / RATE
    mono = np.where(t < 10, np.sin(2 * np.pi * 440 * t),
                    np.sin(2 * np.pi * 660 * t)).astype(np.float32) * 0.8
    audio = np.column_stack([mono, mono]).astype(np.float32)
    return Track('two', audio, compute_peaks(audio), 120.0, 1.0, 0, 1.0)


def beep_track(bpm=120.0, freq=880.0, seconds=30):
    """150 ms Hann-windowed beeps exactly on the beats (first
    beat at 1.0 s). A tempo change must move the beeps; a pitch
    change must not touch their frequency."""
    t = np.arange(int(seconds * RATE)) / RATE
    mono = np.zeros(int(seconds * RATE), dtype=np.float32)
    period = 60.0 / bpm
    k = 0
    while 1.0 + k * period < seconds - 0.5:
        s = int((1.0 + k * period) * RATE)
        e = s + int(0.15 * RATE)
        mono[s:e] = (np.sin(2 * np.pi * freq * t[s:e])
                     * np.hanning(e - s) * 0.8).astype(np.float32)
        k += 1
    audio = np.column_stack([mono, mono]).astype(np.float32)
    return Track('beeps', audio, compute_peaks(audio), bpm, 1.0, 0, 1.0)


def dom_freq(x):
    """Dominant frequency of a stereo block, parabolic-interpolated."""
    x = x[:, 0].astype(np.float64)
    x = x - x.mean()
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    k = int(np.argmax(spectrum[10:])) + 10
    a, b, c = spectrum[k - 1], spectrum[k], spectrum[k + 1]
    denom = a - 2 * b + c
    delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
    return (k + delta) * RATE / len(x)


def render(engine, seconds, block=1024):
    out, left = [], int(seconds * RATE)
    while left > 0:
        n = min(block, left)
        out.append(engine.render(n))
        left -= n
    return np.concatenate(out)


def playing_engine(track, rate, key_lock=True, start_s=5.0):
    eng = Engine()
    eng.command('load', 0, track)
    eng.command('key_lock', 0, key_lock)
    eng.command('tempo', 0, rate)
    if start_s:
        eng.command('seek', 0, int(start_s * RATE))
    eng.command('play', 0, True)
    return eng


# ------------------------------------------------------------------
# Pitch invariance (key lock ON) / pitch shift (key lock OFF)
# ------------------------------------------------------------------

@pytest.mark.parametrize('rate', [0.95, 1.0, 1.05])
def test_pitch_preserved_with_key_lock(rate):
    eng = playing_engine(tone_track(440.0), rate, key_lock=True)
    out = render(eng, 3.0)
    assert np.isfinite(out).all()
    freq = dom_freq(out[int(0.5 * RATE):])
    assert abs(freq - 440.0) / 440.0 < 0.005, freq
    # Source consumption scales by 1/rate: in 3.0 output seconds
    # the deck advanced exactly 3.0 * rate source seconds — the
    # same accounting the resample path (and the journal) uses.
    pos = eng.decks[0]['position'] / RATE
    assert pos == pytest.approx(5.0 + 3.0 * rate, abs=0.02)


@pytest.mark.parametrize('rate', [0.95, 1.05])
def test_pitch_follows_rate_without_key_lock(rate):
    eng = playing_engine(tone_track(440.0), rate, key_lock=False)
    out = render(eng, 3.0)
    freq = dom_freq(out[int(0.5 * RATE):])
    assert abs(freq - 440.0 * rate) / (440.0 * rate) < 0.01, freq
    pos = eng.decks[0]['position'] / RATE
    assert pos == pytest.approx(5.0 + 3.0 * rate, abs=0.02)


def test_chord_pitch_preserved_with_key_lock():
    # A chord, not just a sine: both partials must survive at
    # their own frequencies (a stretcher that merely re-tunes a
    # single tone would fail here).
    eng = playing_engine(tone_track(440.0, chord=(554.37,)), 1.05)
    out = render(eng, 3.0)[int(0.5 * RATE):]
    x = out[:, 0].astype(np.float64)
    spectrum = np.abs(np.fft.rfft((x - x.mean()) * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / RATE)
    for target in (440.0, 554.37):
        band = (freqs > target * 0.98) & (freqs < target * 1.02)
        peak = spectrum[band].max()
        assert peak > 0.25 * spectrum.max(), target


@pytest.mark.parametrize('rate', [0.85, 1.18])
def test_stretch_range_edges_still_clean(rate):
    # The edges of the supported-clean range: output stays
    # finite, full-length and on-pitch.
    eng = playing_engine(tone_track(440.0), rate, key_lock=True)
    out = render(eng, 2.0)
    assert len(out) == 2 * RATE
    assert np.isfinite(out).all()
    freq = dom_freq(out[int(0.5 * RATE):])
    assert abs(freq - 440.0) / 440.0 < 0.01, freq


def test_key_lock_defaults_on_and_survives_load():
    eng = Engine()
    assert eng.decks[0]['key_lock'] is True
    assert eng.decks[1]['key_lock'] is True
    eng.command('key_lock', 0, False)
    eng.command('load', 0, tone_track())
    eng.render(64)   # applies the queued commands
    assert eng.decks[0]['key_lock'] is False   # kept across the load
    assert eng.decks[1]['key_lock'] is True


# ------------------------------------------------------------------
# Beat grid stays on the (SET BPM) timeline with key lock on
# ------------------------------------------------------------------

def test_beat_grid_alignment_with_key_lock():
    rate = 1.05
    eng = playing_engine(beep_track(), rate, key_lock=True, start_s=0.0)
    out = render(eng, 6.0)
    env = np.abs(out[:, 0])
    smooth = np.convolve(env, np.ones(240) / 240, mode='same')
    peaks, _ = find_peaks(smooth, height=0.15, distance=int(0.3 * RATE))
    times = peaks / RATE
    # Source beats at 1.0 + k*0.5 s, beep centres +0.075 s; on the
    # output timeline they land at source_time / rate.
    expected = [(1.0 + k * 0.5 + 0.075) / rate for k in range(8)]
    assert len(times) >= 8
    for got, want in zip(times, expected):
        assert abs(got - want) < 0.015, (got, want)
    # …and the beeps themselves kept their pitch (not 880 * rate).
    first = int(times[2] * RATE)
    beep = out[first - int(0.05 * RATE):first + int(0.05 * RATE)]
    assert abs(dom_freq(beep) - 880.0) / 880.0 < 0.01


# ------------------------------------------------------------------
# Jump hygiene
# ------------------------------------------------------------------

def test_seek_during_stretch_no_smear():
    eng = playing_engine(two_tone_track(), 1.05, start_s=0.0)
    out = render(eng, 1.0)
    assert abs(dom_freq(out[24000:]) - 440.0) < 2.0
    eng.command('seek', 0, 20 * RATE)
    out = render(eng, 1.0)
    assert np.isfinite(out).all()
    # The new side only: the second half of the block is purely
    # the 660 Hz side — nothing of the old position smeared in.
    assert abs(dom_freq(out[24000:]) - 660.0) < 2.0
    assert eng.decks[0]['position'] == pytest.approx(21.05 * RATE, abs=RATE * 0.02)


def test_cue_and_hotcue_jumps_during_stretch():
    eng = playing_engine(two_tone_track(), 0.95, start_s=0.0)
    eng.command('seek', 0, 20 * RATE)
    eng.command('cue_set', 0)
    eng.command('seek', 0, 2 * RATE)
    render(eng, 0.5)
    eng.command('cue_jump', 0)
    out = render(eng, 1.0)
    assert np.isfinite(out).all()
    assert abs(dom_freq(out[24000:]) - 660.0) < 2.0
    eng.command('hotcue_set', 0, 1)
    eng.command('seek', 0, 1 * RATE)
    render(eng, 0.25)
    eng.command('hotcue_jump', 0, 1)
    out = render(eng, 0.5)
    assert np.isfinite(out).all()
    assert abs(dom_freq(out[12000:]) - 660.0) < 2.0


def test_loop_during_stretch_stays_in_loop_and_finite():
    eng = playing_engine(two_tone_track(), 1.05, start_s=0.0)
    eng.command('seek', 0, int(19.5 * RATE))
    eng.command('loop_in', 0)
    eng.command('seek', 0, int(20.5 * RATE))
    eng.command('loop_out', 0)
    eng.command('seek', 0, int(19.5 * RATE))
    eng.command('play', 0, True)
    out = render(eng, 3.0)
    assert np.isfinite(out).all()
    assert np.abs(out).max() > 0.3   # real audio, not silence/runaway
    pos = eng.decks[0]['position'] / RATE
    assert 19.5 <= pos <= 20.5
    eng.command('loop_exit', 0)
    out = render(eng, 1.0)
    assert np.isfinite(out).all()
    assert eng.decks[0]['position'] / RATE > 20.5


def test_track_end_with_key_lock_stops_cleanly():
    eng = playing_engine(tone_track(440.0, seconds=6), 1.05, start_s=5.0)
    out = render(eng, 2.0)
    assert np.isfinite(out).all()
    assert eng.decks[0]['playing'] is False
    assert eng.decks[0]['position'] == 6 * RATE


def test_toggle_mid_playback_switches_paths_without_runaway():
    eng = playing_engine(tone_track(440.0), 1.05, key_lock=True)
    render(eng, 1.0)
    eng.command('key_lock', 0, False)
    out = render(eng, 1.0)
    assert np.isfinite(out).all()
    assert abs(dom_freq(out[24000:]) - 462.0) < 3.0   # resampled now
    eng.command('key_lock', 0, True)
    out = render(eng, 1.0)
    assert np.isfinite(out).all()
    assert abs(dom_freq(out[24000:]) - 440.0) < 2.0   # stretched again


# ------------------------------------------------------------------
# Journal + replay
# ------------------------------------------------------------------

def test_key_lock_toggle_is_journalled_and_replayed():
    eng = Engine()
    eng.command('load', 0, tone_track())
    eng.command('key_lock', 0, False)
    eng.command('key_lock', 1, True)
    eng.render(64)
    events = []
    while not eng.events.empty():
        events.append(eng.events.get())
    toggles = [(e['deck'], e['value']) for e in events
               if e['action'] == 'key_lock']
    assert toggles == [(0, False), (1, True)]
    # Replay carries the flag as deck state (it renders no audio,
    # so nothing else about the reconstruction may change).
    recon = performance._Reconstructor({'tone': 30.0}, {'tone': 120.0})
    recon.run([dict(frame=0, sample_rate=RATE, action='load', deck=0,
                    value='tone'),
               dict(frame=10, sample_rate=RATE, action='key_lock',
                    deck=0, value=False)])
    assert recon.decks[0]['key_lock'] is False
    assert recon.decks[1]['key_lock'] is True


# ------------------------------------------------------------------
# UI: the deck-strip chip
# ------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False


@pytest.fixture(scope='module')
def window():
    if not HAVE_QT:
        pytest.skip('PySide6 unavailable')
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()


@pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')
def test_key_lock_chip_on_both_decks_starts_on_and_drives_engine(window):
    for i, deck in enumerate(window.decks):
        chip = deck.key_lock
        assert chip.isCheckable()
        assert chip.isChecked() is True        # default ON
        assert chip.text() == 'キーロック'      # jp default language
        chip.setChecked(False)                 # user toggle
        window.engine.apply_pending()
        assert window.engine.decks[i]['key_lock'] is False
        window.tick()                          # chip follows engine
        assert deck.key_lock_on is False
        chip.setChecked(True)
        window.engine.apply_pending()
        assert window.engine.decks[i]['key_lock'] is True
        window.tick()
        assert deck.key_lock_on is True
