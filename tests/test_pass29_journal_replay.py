"""Pass 29 — the export replayed from the user's REAL journal
shape (2026-11-28).

Passes 27–28 verified the export against synthetic journals and
a base reconstructed from his earlier export — and his real
session still came out wrong in Live: track 1 showed two chunks
with a gap, track 2 (deck B) showed nothing at all. This time
the ground truth is his own journal (mixcraft-performance-
events.json, the copy pass 28 saves beside the .als): 1448
events, and this module rebuilds a journal of exactly that
shape and pins what the replay must produce from it:

  - deck 0 loads "Romain Garcia - Heya (Extended Mix).wav" and
    plays from +2.24 s; its parameter activity (volume / EQ /
    filter / gain / master) is dense, and at +48..59 s there is
    a scratch passage: 351 DDJ-RB jog ticks of ±3024 frames.
  - deck 1 loads "Hidden Empire - Signals.wav" at +11.20 s but
    only PLAYS at +64.51 s; its only other events are 47 jog
    ticks of +3024 frames at +69.5 s.
  - there are NO stop / pause events anywhere — both decks are
    still playing when the journal ends at +83.41 s, so segment
    ends are defined by the journal's end, not by stop events.

What the old replay did with this shape (measured): deck 0
shattered into 50 segments — one per jog tick, because a
±3024-frame tick is far beyond the 0.1-beat absorb window —
and the plan held 50 clips for deck A, 45 of them sub-beat
slivers whose Live-time spans overlap each other (a clip's
length comes from its offset span; the slivers' start times
are dense). Deck B's five clips carried three sub-beat slivers
of the same kind from its jog burst. The fix (performance.py):
a jog is a continuous-control gesture at any size and never
cuts a segment — it re-anchors it — so one continuous play is
one segment; deliberate edits (seek / cue / hotcue) still cut.
A net-backward segment (spinback) falls back to its performed
timeline span so the clip stays valid (CurrentEnd > start).
"""
import pytest

import performance
from engine import RATE

from test_pass27_export_fragments import _clips, _make_wav, \
    _write_base
import als_writer
import perf_als

HEYA = r'C:\Users\wa-ma\Desktop\260828bandcamp' \
       r'\Romain Garcia - Heya (Extended Mix).wav'
SIGNALS = r'C:\Users\wa-ma\Desktop\260828bandcamp' \
          r'\Hidden Empire - Signals.wav'
BASE_FRAME = 707584            # the real journal's first frame
SET_BPM = 125.0                # the real journal's anchor
DURATIONS = {HEYA: 324.1, SIGNALS: 379.3}
BPMS = {HEYA: 121.97, SIGNALS: 122.0}
META = {HEYA: {'bpm': 121.97, 'duration_seconds': 324.1,
               'name': 'Romain Garcia - Heya (Extended Mix)'},
        SIGNALS: {'bpm': 122.0, 'duration_seconds': 379.3,
                  'name': 'Hidden Empire - Signals'}}


def _ev(seconds, action, deck=None, value=None):
    return dict(frame=BASE_FRAME + int(seconds * RATE),
                sample_rate=RATE, action=action, deck=deck,
                value=value)


def _real_shape_events():
    """The real journal's layout, event class for event class
    (counts compressed where only the shape matters — except
    the jog bursts, whose ±3024-frame ticks ARE the defect)."""
    events = [_ev(0.00, 'load', 0, HEYA),
              _ev(0.00, 'set_bpm_anchor', None, 125.0),
              _ev(2.24, 'play', 0, True),
              _ev(11.20, 'load', 1, SIGNALS)]
    # Deck 0's parameter activity: volume rides, EQ sweeps, a
    # filter sweep, a few gain / master touches.
    for k in range(20):
        events.append(_ev(28.0 + k * 0.6, 'volume', 0,
                          0.5 + 0.02 * k))
        events.append(_ev(30.0 + k * 0.6, 'eq_low', 0,
                          0.5 - 0.01 * k))
    for k in range(8):
        events.append(_ev(50.0 + k * 0.7, 'filter', 0,
                          0.5 - 0.05 * k))
        events.append(_ev(24.0 + k, 'master', None, 0.8))
    events.append(_ev(80.5, 'gain', 0, 0.98))
    # The scratch passage: hardware jog ticks, ±3024 frames,
    # +48.06 s .. +59.29 s (the real journal has 351 of them).
    frame = 48.06
    for k in range(60):
        events.append(_ev(frame, 'jog', 0,
                          3024 if k % 3 else -3024))
        frame += 11.23 / 60
    # Deck 1 plays late; one forward jog burst at +69.5 s
    # (47 ticks of +3024 in the real journal).
    events.append(_ev(64.51, 'play', 1, True))
    for k in range(47):
        events.append(_ev(69.5 + k * 0.005, 'jog', 1, 3024))
    # The journal just ends — no stop / pause anywhere.
    events.append(_ev(83.41, 'gain', 0, 0.98))
    return events


def _recon():
    return performance.reconstruct(
        _real_shape_events(), durations=DURATIONS, bpms=BPMS,
        set_bpm=SET_BPM)


def test_real_shape_one_segment_per_deck():
    recon = _recon()
    # One continuous play per deck = one segment, however much
    # jogging happened inside it. Segment ends come from the
    # journal's end (83.41 s, rebased to 81.17 s): no stop
    # events exist in this shape.
    assert len(recon['segments'][0]) == 1
    assert len(recon['segments'][1]) == 1
    seg_b = recon['segments'][1][0]
    assert seg_b['track'] == SIGNALS
    assert seg_b['start_frame'] / RATE == pytest.approx(
        64.51 - 2.24, abs=0.05)
    assert seg_b['end_frame'] / RATE == pytest.approx(
        83.41 - 2.24, abs=0.05)


def test_real_shape_one_clip_per_deck_at_performed_times():
    plan = performance.build_perf_plan(_recon(), SET_BPM, META)
    assert len(plan['clips']['A']) == 1
    assert len(plan['clips']['B']) == 1
    clip_b = plan['clips']['B'][0]
    assert clip_b['name'] == 'Hidden Empire - Signals'
    # Deck B started 62.27 s after deck A: 129.7 beats at the
    # set BPM — bar 33's downbeat neighbourhood, where his
    # screenshot's second chunk sat (on the wrong track, under
    # deck A's name).
    assert clip_b['time_beats'] == pytest.approx(129.7, abs=1.0)
    assert clip_b['end_beats'] > clip_b['offset_beats']
    clip_a = plan['clips']['A'][0]
    assert clip_a['time_beats'] == pytest.approx(0.0, abs=0.01)
    assert clip_a['end_beats'] > clip_a['offset_beats']


def test_real_shape_export_writes_signals_to_track_two(tmp_path):
    """End to end through perf_als.generate with the real-shape
    journal: deck B's song lands on track 2, named as itself,
    as ONE clip — and deck A is one clip too."""
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    wav_b = _make_wav(tmp_path / 'Beta.wav')
    events = []
    for event in _real_shape_events():
        event = dict(event)
        if event['action'] == 'load':
            event['value'] = str(wav_a) if event['deck'] == 0 \
                else str(wav_b)
        events.append(event)
    meta = {str(wav_a): META[HEYA], str(wav_b): META[SIGNALS]}
    durations = {str(wav_a): 324.1, str(wav_b): 379.3}
    bpms = {str(wav_a): 121.97, str(wav_b): 122.0}
    base = _write_base(tmp_path)
    out = tmp_path / 'perf.als'
    report = perf_als.generate(base, events, out, set_bpm=SET_BPM,
                               durations=durations, bpms=bpms)
    assert report['decks'] == {'A': 1, 'B': 1}
    root = als_writer.load_als(out).getroot()
    tracks = als_writer._audio_tracks(root)
    clips_a = _clips(tracks[0])
    clips_b = _clips(tracks[1])
    assert len(clips_a) == 1
    assert len(clips_b) == 1
    # The clip carries deck B's OWN file identity (here the
    # performed file is Beta.wav — on his machine this is the
    # 'Hidden Empire - Signals' stem), not deck A's.
    name_b = als_writer._get_value(clips_b[0], 'Name')
    assert name_b == 'Beta'
    file_ref = clips_b[0].find('.//FileRef')
    assert 'Beta.wav' in als_writer._get_value(file_ref, 'Path')
    assert float(clips_b[0].get('Time')) == pytest.approx(
        129.7, abs=1.0)


def test_spinback_segment_stays_a_valid_clip():
    """A jog that drags the position behind the segment's start
    (a spinback) must not produce CurrentEnd <= CurrentStart:
    the plan falls back to the performed timeline span."""
    events = [_ev(0, 'load', 0, HEYA),
              _ev(1, 'seek', 0, 100 * RATE),
              _ev(1, 'play', 0, True),
              _ev(3, 'jog', 0, -10 * RATE),   # spin back 10 s
              _ev(6, 'play', 0, False)]
    recon = performance.reconstruct(
        events, durations=DURATIONS, bpms=BPMS, set_bpm=SET_BPM)
    plan = performance.build_perf_plan(recon, SET_BPM, META)
    clips = plan['clips']['A']
    assert len(clips) == 1
    clip = clips[0]
    assert clip['end_beats'] > clip['offset_beats']
    # 5 s of performed timeline, straight through.
    assert (clip['end_beats'] - clip['offset_beats']) == \
        pytest.approx(5 * 121.97 / 60, abs=0.3)
