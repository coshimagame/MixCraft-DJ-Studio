"""performance.py: the performance reconstruction (pass 21).

Pure (no Qt): synthetic event logs in the engine's own format
are replayed and the segments / automation series are checked
against the engine's state rules (engine.py _apply / _read_deck).
"""
import pytest

import performance
from engine import RATE


def F(seconds):
    return int(seconds * RATE)


def ev(frame, action, deck=None, value=None):
    return dict(frame=frame, sample_rate=RATE, action=action,
                deck=deck, value=value)


def test_segment_play_pause():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'play', 0, True),
              ev(F(10), 'play', 0, False)]
    recon = performance.reconstruct(events, durations={'a.wav': 24.0})
    segs = recon['segments'][0]
    assert len(segs) == 1
    assert segs[0]['track'] == 'a.wav'
    assert segs[0]['start_frame'] == 0
    assert segs[0]['end_frame'] == F(10)
    assert segs[0]['offset_frames'] == 0
    assert segs[0]['rate'] == 1.0
    assert recon['segments'][1] == []


def test_seek_splits_segment():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'play', 0, True),
              ev(F(5), 'seek', 0, F(12)), ev(F(8), 'play', 0, False)]
    recon = performance.reconstruct(events, durations={'a.wav': 24.0})
    segs = recon['segments'][0]
    assert [(s['start_frame'], s['end_frame'], s['offset_frames'])
            for s in segs] == [(0, F(5), 0), (F(5), F(8), F(12))]


def test_track_end_closes_and_replay_restarts():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'play', 0, True),
              ev(F(6), 'play', 0, True), ev(F(8), 'play', 0, False)]
    recon = performance.reconstruct(events, durations={'a.wav': 4.0})
    segs = recon['segments'][0]
    # First play runs into the track end at 4 s and stops there;
    # the play at 6 s restarts from 0 (the engine's rule).
    assert [(s['start_frame'], s['end_frame'], s['offset_frames'])
            for s in segs] == [(0, F(4), 0), (F(6), F(8), 0)]


def test_loop_wraps_split_segments():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'play', 0, True),
              ev(F(2), 'loop_in', 0), ev(F(4), 'loop_out', 0),
              ev(F(9), 'play', 0, False)]
    recon = performance.reconstruct(events, durations={'a.wav': 24.0})
    segs = recon['segments'][0]
    # Loop [2 s, 4 s): wraps at 4 (set at the loop end itself),
    # then every 2 s; each wrap starts a new segment at 2 s.
    assert [(s['start_frame'], s['end_frame'], s['offset_frames'])
            for s in segs] == [(0, F(4), 0), (F(4), F(6), F(2)),
                               (F(6), F(8), F(2)), (F(8), F(9), F(2))]


def test_preview_gap_is_excluded_and_state_restored():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'load', 1, 'b.wav'),
              ev(0, 'play', 0, True),
              ev(F(10), 'preview', 1, 8),
              ev(F(12), 'play', 1, True),      # during preview: dropped
              ev(F(30), 'volume', 0, 0.5)]     # after the preview
    recon = performance.reconstruct(
        events, durations={'a.wav': 24.0, 'b.wav': 24.0})
    segs = recon['segments'][0]
    # The audition (10 s + 8 s) is not performance; deck A resumes
    # from its pre-preview position (10 s in) at 18 s and plays on
    # to the log's end at 30 s (22 s into the track by then).
    assert [(s['start_frame'], s['end_frame']) for s in segs] == \
        [(0, F(10)), (F(18), F(30))]
    assert recon['segments'][1] == []
    assert any('試聴中' in w for w in recon['warnings'])


def test_automation_series_record_command_values():
    events = [ev(F(1), 'volume', 0, 0.5), ev(F(1), 'gain', 1, 1.5),
              ev(F(2), 'cross', None, 0.0), ev(F(3), 'cross', None, 1.0),
              ev(F(4), 'cross', None, 1.0)]   # duplicate: not re-recorded
    recon = performance.reconstruct(events)
    assert recon['automation'][0]['volume'] == [(0, 0.8), (F(1), 0.5)]
    assert recon['automation'][1]['gain'] == [(0, 1.0), (F(1), 1.5)]
    assert recon['automation']['cross'] == \
        [(0, 0.5), (F(2), 0.0), (F(3), 1.0)]
    assert recon['automation']['master'] == [(0, 0.8)]


def test_sync_rate_is_recomputed_from_bpms():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'load', 1, 'b.wav'),
              ev(F(1), 'sync', 1, True), ev(F(1), 'play', 1, True),
              ev(F(11), 'play', 1, False)]
    recon = performance.reconstruct(
        events, durations={'a.wav': 30.0, 'b.wav': 30.0},
        bpms={'a.wav': 120.0, 'b.wav': 125.0})
    segs = recon['segments'][1]
    assert len(segs) == 1
    assert segs[0]['rate'] == pytest.approx(120.0 / 125.0)


def test_load_events_accepts_export_forms(tmp_path):
    events = [ev(0, 'play', 0, True)]
    assert performance.load_events(events) == events
    assert performance.load_events({'schema': 1, 'events': events}) == events
    path = tmp_path / 'events.json'
    path.write_text('{"schema": 1, "events": '
                    '[{"frame": 0, "action": "play", "deck": 0, '
                    '"value": true}]}', encoding='utf-8')
    loaded = performance.load_events(path)
    assert loaded[0]['action'] == 'play'


def test_build_perf_plan_converts_and_bakes_crossfader():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'play', 0, True),
              ev(F(5), 'cross', None, 1.0), ev(F(10), 'play', 0, False)]
    recon = performance.reconstruct(events, durations={'a.wav': 24.0},
                                    bpms={'a.wav': 120.0})
    plan = performance.build_perf_plan(
        recon, 120.0,
        {'a.wav': {'bpm': 120.0, 'name': 'Song — Artist',
                   'duration_seconds': 24.0}})
    assert plan['warnings'] == []
    clip = plan['clips']['A'][0]
    assert clip['time_beats'] == 0.0
    assert clip['offset_beats'] == 0.0
    assert clip['end_beats'] == pytest.approx(20.0)
    assert clip['name'] == 'Song — Artist'
    # Volume A: 0.8 x cos(pi/4) at cross centre -> ~0 at cross
    # 1.0 (10 beats), held to the performance end (20 beats).
    volume_a = plan['envelopes']['volume_A']
    assert volume_a[0] == (0.0, pytest.approx(0.8 * 0.70710678))
    assert volume_a[1] == (pytest.approx(10.0), pytest.approx(0.0, abs=1e-9))
    assert volume_a[2] == (pytest.approx(20.0), pytest.approx(0.0, abs=1e-9))
    # Neutral EQ / GAIN are 0 dB lines; tempo is the set BPM.
    assert all(v == 0.0 for _, v in plan['envelopes']['eq_low_A'])
    assert all(v == 0.0 for _, v in plan['envelopes']['gain_A'])
    assert plan['envelopes']['tempo'] == [(0.0, 120.0), (20.0, 120.0)]


def test_build_perf_plan_warns_on_rate_mismatch():
    events = [ev(0, 'load', 0, 'a.wav'), ev(0, 'play', 0, True),
              ev(F(4), 'play', 0, False)]
    recon = performance.reconstruct(events, durations={'a.wav': 24.0},
                                    bpms={'a.wav': 100.0})
    plan = performance.build_perf_plan(
        recon, 122.0, {'a.wav': {'bpm': 100.0, 'duration_seconds': 24.0}})
    assert any('SET BPM' in w for w in plan['warnings'])
