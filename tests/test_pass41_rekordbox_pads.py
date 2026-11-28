"""Pass 41 (user's headline principle, 2026-11-28: 操作性は
rekordbox と同じ感覚): pad behaviour matched to rekordbox.

- CUE returns AND stops: pressing it mid-play lands on the
  cue point PAUSED (it used to keep playing through the
  point — the needle moved and nothing else changed).
- A hot cue FIRES: pressing it on a paused deck starts
  playback from the hot cue (it used to just move the needle
  in silence). A hot-cue start is a play start, so AUTO SYNC
  engages exactly as for the PLAY button.
- performance.reconstruct mirrors both, so the Live export
  shows what he actually heard: the CUE press closes the
  segment at the cue, the hot-cue press opens one.
"""
import numpy as np

import performance
from engine import Engine, RATE, Track, compute_peaks


def _track(seconds=30, value=0.3):
    audio = np.full((int(seconds * RATE), 2), value,
                    dtype=np.float32)
    return Track('t', audio, compute_peaks(audio), 120.0, 1.0,
                 0, 1.0)


def _engine():
    eng = Engine()
    eng.command('load', 0, _track())
    eng.command('load', 1, _track())
    eng.apply_pending()
    return eng


def _drain(eng):
    events = []
    while not eng.events.empty():
        events.append(eng.events.get())
    return events


def test_cue_jump_pauses_at_the_cue():
    eng = _engine()
    eng.command('seek', 0, 5 * RATE)
    eng.command('cue_set', 0)
    eng.command('play', 0, True)
    eng.render(4800)
    assert eng.decks[0]['playing'] is True
    eng.command('cue_jump', 0)
    eng.render(512)
    deck = eng.decks[0]
    assert deck['playing'] is False
    assert deck['position'] == 5 * RATE
    # And it STAYS there — paused at the cue, ready to fire.
    eng.render(4800)
    assert eng.decks[0]['position'] == 5 * RATE


def test_cue_jump_while_paused_just_returns():
    eng = _engine()
    eng.command('seek', 0, 5 * RATE)
    eng.command('cue_set', 0)
    eng.command('seek', 0, 12 * RATE)
    eng.command('cue_jump', 0)
    eng.render(512)
    assert eng.decks[0]['playing'] is False
    assert eng.decks[0]['position'] == 5 * RATE


def test_hotcue_jump_fires_playback_from_paused():
    eng = _engine()
    eng.command('seek', 0, 8 * RATE)
    eng.command('hotcue_set', 0, 1)
    eng.command('seek', 0, 0)
    eng.render(512)
    assert eng.decks[0]['playing'] is False
    eng.command('hotcue_jump', 0, 1)
    eng.render(512)
    deck = eng.decks[0]
    assert deck['playing'] is True
    assert 8 * RATE <= deck['position'] <= 8 * RATE + 512


def test_hotcue_jump_while_playing_keeps_playing():
    eng = _engine()
    eng.command('seek', 0, 8 * RATE)
    eng.command('hotcue_set', 0, 1)
    eng.command('play', 0, True)
    eng.render(4800)
    eng.command('hotcue_jump', 0, 1)
    eng.render(512)
    assert eng.decks[0]['playing'] is True
    assert 8 * RATE <= eng.decks[0]['position'] <= 8 * RATE + 512


def test_hotcue_start_engages_auto_sync_like_play():
    eng = _engine()
    # Give both decks trustworthy grids at the same tempo so
    # AUTO SYNC (default on) has something to engage.
    for i in (0, 1):
        eng.decks[i]['track'].bpm = 120.0
    eng.command('play', 1, True)             # deck B playing
    eng.command('seek', 0, 8 * RATE)
    eng.command('hotcue_set', 0, 1)
    eng.command('seek', 0, 0)
    eng.render(512)
    _drain(eng)
    eng.command('hotcue_jump', 0, 1)         # deck A fires
    eng.render(512)
    actions = [e['action'] for e in _drain(eng)]
    assert eng.decks[0]['playing'] is True
    assert 'sync' in actions or 'sync_align' in actions


def _ev(frame, action, deck=None, value=None):
    return dict(frame=frame, sample_rate=RATE, action=action,
                deck=deck, value=value)


def test_reconstruct_mirrors_cue_stop_and_hotcue_fire():
    durations = {'/music/a.wav': 300.0}
    bpms = {'/music/a.wav': 120.0}
    events = [
        _ev(0, 'load', 0, '/music/a.wav'),
        _ev(100, 'set_bpm_anchor', None, 120.0),
        _ev(200, 'cue_set', 0),
        _ev(1000, 'play', 0, True),
        # CUE at 3 s: the segment must CLOSE there (he stopped
        # at the cue), not run on to the journal's end.
        _ev(3 * RATE, 'cue_jump', 0),
        # Hot cue set + fired at 5 s: a NEW segment opens.
        _ev(4 * RATE, 'hotcue_set', 0, 1),
        _ev(5 * RATE, 'hotcue_jump', 0, 1),
        _ev(8 * RATE, 'play', 0, False),
    ]
    recon = performance.reconstruct(events, durations=durations,
                                    bpms=bpms, set_bpm=120.0)
    segs = recon['segments'][0]
    assert len(segs) == 2
    first, second = segs
    # The timeline is rebased to the first play (frame 1000), so
    # the CUE close lands at 3 s − 1000 frames and the hot-cue
    # segment opens at 5 s − 1000 frames.
    assert abs(first['end_frame'] - (3 * RATE - 1000)) < RATE / 2
    assert abs(second['start_frame'] - (5 * RATE - 1000)) < RATE / 2
