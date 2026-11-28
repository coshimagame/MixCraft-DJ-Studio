"""Pass 28 (user report 2026-11-28: 「小節があってない」): bar
(downbeat) phase. Analysis detects the BEAT phase only, so a
track's bar lines could sit 1–3 beats off Live's grid in the
export. The fix is a per-track bar offset (0–3 beats):

  * stored on the Track and persisted in the analysis cache,
  * set from a stepper beside the deck readout (and the
    focused readout's arrow keys), moving the waveform's bar
    ticks,
  * honoured by the Live export: the whole timeline shifts by
    Δ ∈ [0,4) so the FIRST played track's next downbeat lands
    exactly on a multiple-of-4 beat. Relative placement is
    untouched — later clips keep their performed alignment.
    (This deliberately supersedes the pure pass-27 beat-0
    rebase for grid-known tracks: the first sound may start up
    to ~4 beats in, because its downbeat must sit on a bar
    line. Without grid metadata Δ = 0 and nothing changes.)
"""
import sqlite3
from pathlib import Path

import pytest

import performance
from analysis import AnalysisCache
from engine import RATE


def _result(**kw):
    base = dict(bpm=120.0, confidence=0.9, key=None, camelot=None,
                key_confidence=0.0, beat_offset=0,
                beat_confidence=0.9, bar_offset=0)
    base.update(kw)
    return base


def _real_file(tmp_path):
    path = tmp_path / 'x.wav'
    path.write_bytes(b'RIFF' + b'\x00' * 64)
    return str(path)


def test_cache_round_trips_bar_offset(tmp_path):
    cache = AnalysisCache(tmp_path / 'analysis.db')
    path = _real_file(tmp_path)
    cache.put(path, _result(bar_offset=2))
    row = cache.get(path)
    assert row['bar_offset'] == 2
    cache.set_bar_offset(path, 3)
    row = cache.get(path)
    assert row['bar_offset'] == 3
    assert row['bpm'] == 120.0          # nothing else moved
    absent = str(tmp_path / 'absent.wav')
    Path(absent).write_bytes(b'RIFF')
    cache.set_bar_offset(absent, 1)     # no row -> no-op
    assert cache.get(absent) is None


def test_cache_migrates_a_pre_pass28_database(tmp_path):
    import analysis
    path = _real_file(tmp_path)
    db = tmp_path / 'old.db'
    conn = sqlite3.connect(db)
    # The pre-pass-28 shape: 9 columns, no bar_offset.
    conn.execute(
        'CREATE TABLE analysis (path TEXT PRIMARY KEY,'
        ' fingerprint TEXT, bpm REAL, confidence REAL,'
        ' beat_offset INTEGER, key_name TEXT, camelot TEXT,'
        ' key_confidence REAL, beat_confidence REAL)')
    conn.execute(
        'INSERT INTO analysis VALUES (?,?,?,?,?,?,?,?,?)',
        (str(Path(path).resolve()), analysis.fingerprint(path),
         122.0, 0.8, 5, None, None, 0.0, 0.7))
    conn.commit()
    conn.close()
    cache = AnalysisCache(db)
    row = cache.get(path)
    assert row['beat_offset'] == 5
    assert row['bar_offset'] is None      # unknown, not 0
    cache.set_bar_offset(path, 1)
    assert cache.get(path)['bar_offset'] == 1


def _journal(wav, seconds=8.0):
    return [dict(frame=0, sample_rate=RATE, action='load', deck=0,
                 value=wav),
            dict(frame=100, sample_rate=RATE, action='play',
                 deck=0, value=True),
            dict(frame=100 + int(seconds * RATE), sample_rate=RATE,
                 action='play', deck=0, value=False)]


def _plan(wav, meta):
    recon = performance.reconstruct(
        _journal(wav), durations={wav: 60.0}, bpms={wav: 120.0},
        set_bpm=120.0)
    return performance.build_perf_plan(recon, 120.0, {wav: meta})


def test_export_aligns_the_first_downbeat_to_a_bar_line():
    meta = {'bpm': 120.0, 'duration_seconds': 60.0, 'name': 'x',
            'beat_offset': 0, 'bar_offset': 1}
    plan = _plan('x.wav', meta)
    # Position 0 is grid beat 0; with bar_offset 1 the next
    # downbeat is 1 track beat later (at timeline beat 1), so
    # the whole timeline shifts by Δ = 3 and the downbeat lands
    # on beat 4 — a bar line.
    assert plan['bar_shift'] == pytest.approx(3.0)
    clip = plan['clips']['A'][0]
    assert clip['time_beats'] == pytest.approx(3.0)
    assert (clip['time_beats'] + 1.0) % 4.0 == pytest.approx(0.0)


def test_no_offset_and_no_grid_keep_the_old_behaviour():
    meta = {'bpm': 120.0, 'duration_seconds': 60.0, 'name': 'x',
            'beat_offset': 0, 'bar_offset': 0}
    plan = _plan('x.wav', meta)
    assert plan['bar_shift'] == 0.0
    assert plan['clips']['A'][0]['time_beats'] == 0.0
    meta = {'bpm': 120.0, 'duration_seconds': 60.0, 'name': 'x'}
    plan = _plan('x.wav', meta)
    assert plan['bar_shift'] == 0.0


def test_bar_tick_predicate_shifts_with_the_offset():
    import app as appmod
    for off in range(4):
        bars = [k for k in range(16)
                if appmod._is_bar_tick(k, off, 4)]
        assert bars == [off, off + 4, off + 8, off + 12]
