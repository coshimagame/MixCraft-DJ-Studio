"""Pass 28 (user report 2026-11-28: 「タンテの解像度をもっと
あげてほしい」): the jog path, end to end.

What actually limited resolution before this pass (audit):
  * Step: one DDJ-RB jog tick moved 220 frames ≈ 4.6 ms — but
    the MIDI layer computed each step as an ABSOLUTE seek from
    the engine position at that instant. Messages drained in
    one poll burst all aimed at nearly the same target, so a
    burst collapsed to roughly one step: fast spins lost most
    of their travel.
  * The seek went through the quantize snap path, so with
    quantize on, fine jog moves could be pulled to beats.

The fix: the controller sends a signed DELTA (1 ms per unit
tick, JOG_STEP_FRAMES = RATE//1000); the engine's 'jog' action
adds deltas to the live position — they accumulate in the
command queue itself — with no snap. The replay treats 'jog'
as the same relative jump and the pass-27 absorb/coalesce
keeps a fine-jog storm from fragmenting the export.
"""
import numpy as np
import pytest

import performance
from engine import RATE, Engine, Track


def _track(frames=RATE * 30, bpm=120.0):
    return Track('jog.wav', np.zeros((frames, 2), dtype='float32'),
                 np.ones(64, dtype='float32'), bpm, 0.0)


def test_jog_step_is_one_millisecond():
    import midi_input
    assert midi_input.JOG_STEP_FRAMES == RATE // 1000 == 48
    # The pass 23-27 absolute step was 220 frames (~4.6 ms).
    assert midi_input.JOG_STEP_FRAMES < 220


def test_jog_deltas_accumulate_across_a_burst():
    e = Engine()
    e.command('load', 0, _track())
    e.command('seek', 0, RATE * 10)
    for _ in range(5):
        e.command('jog', 0, -480)      # 10 ms back, per message
    e.apply_pending()
    # Delta form: every message's step survives (5 x -480).
    # The old absolute-seek form collapsed a burst like this
    # to roughly the last message's step.
    assert e.decks[0]['position'] == RATE * 10 - 5 * 480


def test_jog_does_not_snap_with_quantize_on():
    e = Engine()
    track = _track()
    track.beat_offset = 0              # grid: beats every 24000
    e.command('load', 0, track)
    e.command('quantize', 0, True)
    e.command('seek', 0, 24000)        # on a beat (snap-safe)
    e.command('jog', 0, 100)
    e.apply_pending()
    # Exactly +100 frames — a snap would have pulled it back
    # to 24000 (or on to 48000).
    assert e.decks[0]['position'] == 24100


def test_jog_journals_and_replays_to_the_same_position():
    e = Engine()
    e.command('load', 0, _track())
    e.command('play', 0, True)
    e.render(RATE)                     # 1 s of playback
    for _ in range(10):
        e.command('jog', 0, 48)
    e.apply_pending()
    events = []
    while not e.events.empty():
        events.append(e.events.get())
    jogs = [x for x in events if x['action'] == 'jog']
    assert len(jogs) == 10
    assert all(x['value'] == 48 for x in jogs)
    recon = performance.reconstruct(
        events, durations={'jog.wav': 30.0},
        bpms={'jog.wav': 120.0}, set_bpm=120.0)
    plan = performance.build_perf_plan(
        recon, 120.0,
        {'jog.wav': {'bpm': 120.0, 'duration_seconds': 30.0,
                     'name': 'jog'}})
    clips = plan['clips']['A']
    assert len(clips) == 1
    # The clip's end position carries the accumulated jog
    # travel: 1 s played (2 beats) + 480 frames (0.02 beats).
    assert clips[0]['end_beats'] == pytest.approx(2.02, abs=0.01)


def test_fine_jog_storm_exports_to_few_clips():
    events = [dict(frame=0, sample_rate=RATE, action='load',
                   deck=0, value='jog.wav'),
              dict(frame=100, sample_rate=RATE, action='play',
                   deck=0, value=True)]
    frame = RATE                            # storm from 1 s in
    for n in range(200):
        events.append(dict(frame=frame + n * 4800,
                           sample_rate=RATE, action='jog', deck=0,
                           value=48 if n % 3 else -48))
    events.append(dict(frame=frame + 200 * 4800 + RATE,
                       sample_rate=RATE, action='play', deck=0,
                       value=False))
    recon = performance.reconstruct(
        events, durations={'jog.wav': 30.0},
        bpms={'jog.wav': 120.0}, set_bpm=120.0)
    plan = performance.build_perf_plan(
        recon, 120.0,
        {'jog.wav': {'bpm': 120.0, 'duration_seconds': 30.0,
                     'name': 'jog'}})
    # 200 one-millisecond jogs during playback must not shatter
    # the performance into 200 clips (pass-27 absorb rule).
    assert len(plan['clips']['A']) <= 2
