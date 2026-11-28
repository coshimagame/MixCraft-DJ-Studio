"""Pass 24 (waveform detail, user report 2026-11-27: 「波形がまだ
ガタガタのまま」): peaks are computed at a fixed fine hop instead
of the old whole-track ≤1400 buckets, and the paint data is a true
per-pixel-column max (engine.window_columns), so neither the
flowing waveform nor the overview can alias transients away.

Pure engine math — no Qt needed.
"""
import numpy as np
import pytest
import soundfile as sf

from engine import (PEAK_HOP, RATE, compute_peaks, load_track,
                    peak_span, window_columns)


# ------------------------------------------------------------------
# compute_peaks
# ------------------------------------------------------------------

def test_compute_peaks_buckets_at_the_fixed_hop():
    audio = np.zeros((PEAK_HOP * 3, 2), dtype=np.float32)
    audio[10, 0] = 0.5            # bucket 0, left channel
    audio[PEAK_HOP + 20, 1] = -0.8  # bucket 1, right channel, negative
    audio[2 * PEAK_HOP + 5, 0] = 0.25
    peaks = compute_peaks(audio)
    assert len(peaks) == 3
    assert peaks[0] == np.float32(0.5)   # max over BOTH channels, abs
    assert peaks[1] == np.float32(0.8)
    assert peaks[2] == np.float32(0.25)


def test_compute_peaks_pads_the_tail_bucket():
    audio = np.zeros((PEAK_HOP + 10, 2), dtype=np.float32)
    audio[-1, 0] = 0.7
    peaks = compute_peaks(audio)
    assert len(peaks) == 2
    assert peaks[1] == np.float32(0.7)   # padding never invents level


def test_compute_peaks_empty():
    assert len(compute_peaks(np.zeros((0, 2), dtype=np.float32))) == 0


def test_peak_resolution_only_goes_up():
    # The standing rule from pass 23/24: ~188 buckets per second at
    # 48 kHz. Coarsening this constant again must fail loudly.
    assert PEAK_HOP <= 256
    assert RATE / PEAK_HOP >= 150


def test_load_track_peaks_are_fine(tmp_path):
    path = tmp_path / 'tone.wav'
    audio = np.zeros((RATE * 3, 2), dtype=np.float32)
    audio[RATE * 2, 0] = 0.9      # one transient at t = 2.0 s
    sf.write(str(path), audio, RATE)
    track = load_track(str(path))
    expected = (RATE * 3 + PEAK_HOP - 1) // PEAK_HOP
    assert len(track.peaks) == expected
    assert len(track.peaks) > 1400 // 20  # nowhere near the old cap
    bucket = (RATE * 2) // PEAK_HOP
    assert track.peaks[bucket] == pytest.approx(0.9, abs=0.01)


# ------------------------------------------------------------------
# window_columns
# ------------------------------------------------------------------

def test_window_columns_identity_when_columns_match_buckets():
    peaks = np.array([0.1, 0.9, 0.2, 0.3], dtype=np.float32)
    cols = window_columns(peaks, total=400, start=0, end=400, width=4)
    assert list(cols) == list(peaks)


def test_window_columns_takes_the_true_max_per_column():
    peaks = np.array([0.1, 0.9, 0.2, 0.3], dtype=np.float32)
    cols = window_columns(peaks, total=400, start=0, end=400, width=2)
    assert list(cols) == [np.float32(0.9), np.float32(0.3)]


def test_window_columns_outside_the_track_stays_zero():
    peaks = np.array([0.5, 0.6], dtype=np.float32)
    # Window starts 200 frames before the track (unclamped by design).
    cols = window_columns(peaks, total=200, start=-200, end=200, width=4)
    assert cols[0] == 0 and cols[1] == 0
    assert cols[2] == np.float32(0.5)
    assert cols[3] == np.float32(0.6)


def test_window_columns_sub_bucket_window_has_no_gaps():
    # Zoomed so far in that several columns share one bucket: the
    # nearest bucket fills them — no zero holes inside the track.
    peaks = np.array([0.25, 0.75], dtype=np.float32)
    cols = window_columns(peaks, total=200, start=50, end=150, width=8)
    assert (cols > 0).all()
    assert cols.max() == np.float32(0.75)


def test_window_columns_preserves_the_span_max():
    rng = np.random.default_rng(3)
    peaks = rng.random(5000).astype(np.float32)
    total = 5000 * PEAK_HOP
    start, end = total // 3, total // 3 + RATE * 16
    cols = window_columns(peaks, total, start, end, 700)
    lo, hi = peak_span(len(peaks), total, start, end)
    assert cols.max() == peaks[lo:hi].max()
    assert len(cols) == 700


def test_window_columns_degenerate_inputs():
    peaks = np.array([0.5], dtype=np.float32)
    assert len(window_columns(peaks, 100, 0, 100, 0)) == 0
    assert len(window_columns(np.zeros(0, dtype=np.float32), 0, 0, 0, 10)) == 10
