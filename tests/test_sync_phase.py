"""Pass 26 (REAL SYNC): beat-grid estimation with a confidence
measure, SYNC as tempo match + beat-phase alignment, the
tempo-only fallback for untrusted grids, the phase lock, and
journal replay reproducing the aligned performance.

Grid tests use synthetic click tracks with known BPM and known
first-beat offsets; engine tests use hand-built Track objects
with exact grids so the alignment math is checked against raw
phase arithmetic, not against the engine's own helper alone.
"""
import numpy as np
import pytest
import soundfile as sf

from analysis import GRID_CONFIDENT, AnalysisCache, estimate_beat_grid
from engine import Engine, RATE, Track, apply_cue_anchor, load_track
import performance

MS30 = int(0.030 * RATE)     # the brief's phase-error budget


# ------------------------------------------------------------------
# Synthetic material
# ------------------------------------------------------------------

def click_audio(bpm, offset_s, seconds, drift=0.0, seed=0):
    """Clicks exactly on the beats. `drift` stretches the period
    linearly across the track (0.05 = the last period is 5% long)
    — the live-recording failure mode the confidence must catch."""
    rng = np.random.default_rng(seed)
    audio = np.zeros((int(seconds * RATE), 2), dtype='float32')
    burst = (rng.standard_normal(1200)
             * np.exp(-np.arange(1200) / 250.0)).astype('float32')
    period = 60.0 / bpm
    total_beats = seconds / period
    t, k = offset_s, 0
    while t < seconds - 0.1:
        i = int(t * RATE)
        audio[i:i + len(burst), 0] += burst
        audio[i:i + len(burst), 1] += burst
        k += 1
        t += period * (1.0 + drift * (k / total_beats))
    return audio


def pad_audio(seconds=20):
    t = np.arange(int(seconds * RATE)) / RATE
    mono = sum(np.sin(2 * np.pi * f * t)
               for f in (110.0, 220.0, 277.18, 329.63)) * 0.15
    return np.column_stack([mono, mono]).astype('float32')


def write(tmp_path, name, audio):
    path = tmp_path / name
    sf.write(path, audio, RATE)
    return str(path)


# ------------------------------------------------------------------
# Beat grid estimation
# ------------------------------------------------------------------

def test_grid_finds_offset_and_confidence(tmp_path):
    path = write(tmp_path, 'a.wav', click_audio(124.0, 0.37, 45))
    track = load_track(path)
    assert track.bpm == pytest.approx(124.0, abs=0.5)
    assert abs(track.beat_offset - 0.37 * RATE) < MS30
    assert track.beat_confidence >= GRID_CONFIDENT


def test_grid_zero_offset(tmp_path):
    path = write(tmp_path, 'b.wav', click_audio(120.0, 0.0, 40))
    track = load_track(path)
    offset = track.beat_offset
    beat = RATE * 60 / track.bpm
    # Offset is modulo one beat: accept a whole number of beats.
    assert min(offset, beat - offset) < MS30
    assert track.beat_confidence >= GRID_CONFIDENT


def test_grid_pad_has_no_confidence():
    offset, conf, _ = estimate_beat_grid(pad_audio(), 120.0)
    assert conf < GRID_CONFIDENT


def test_grid_drifting_tempo_has_no_confidence():
    audio = click_audio(120.0, 0.1, 60, drift=0.05)
    _, conf, _ = estimate_beat_grid(audio, 120.0)
    assert conf < GRID_CONFIDENT


def test_cache_stores_beat_confidence(tmp_path):
    path = write(tmp_path, 'c.wav', click_audio(122.0, 0.2, 30))
    cache = AnalysisCache(tmp_path / 'c.db')
    track = load_track(path, cache)
    row = cache.get(path)
    assert row['beat_confidence'] == pytest.approx(
        track.beat_confidence)
    again = load_track(path, cache)
    assert again.beat_offset == track.beat_offset
    assert again.beat_confidence == track.beat_confidence
    cache.close()


def test_pre26_cache_row_is_enriched(tmp_path):
    path = write(tmp_path, 'd.wav', click_audio(122.0, 0.2, 30))
    cache = AnalysisCache(tmp_path / 'd.db')
    # A row as pass <=25 wrote it: no beat_confidence at all.
    cache.put(path, dict(bpm=122.0, confidence=0.9, beat_offset=0,
                         key=None, camelot=None, key_confidence=0.0))
    assert cache.get(path)['beat_confidence'] is None
    track = load_track(path, cache)
    assert track.beat_confidence >= GRID_CONFIDENT
    assert abs(track.beat_offset - 0.2 * RATE) < MS30
    assert cache.get(path)['beat_confidence'] is not None
    cache.close()


# ------------------------------------------------------------------
# Engine alignment (exact hand-set grids)
# ------------------------------------------------------------------

def make_track(bpm, offset, conf, seconds=30, name='t.wav'):
    frames = int(seconds * RATE)
    return Track(name, np.zeros((frames, 2), dtype='float32'),
                 np.ones(16, dtype='float32'), bpm, 1.0,
                 beat_offset=offset, beat_confidence=conf)


def raw_phase(pos, offset, bpm):
    return ((pos - offset) / (RATE * 60.0 / bpm)) % 1.0


def raw_error_seconds(e, s=1, m=0):
    ds, dm = e.decks[s], e.decks[m]
    err = (raw_phase(dm['position'], dm['track'].beat_offset,
                     dm['track'].bpm)
           - raw_phase(ds['position'], ds['track'].beat_offset,
                       ds['track'].bpm)) % 1.0
    err = min(err, 1.0 - err)
    return err * 60.0 / ds['track'].bpm


def loaded_engine(conf_b=0.9, offset_b=9000):
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav'))
    e.command('load', 1, make_track(124.0, offset_b, conf_b,
                                    name='b.wav'))
    e.apply_pending()
    return e


def drain(e):
    out = []
    while not e.events.empty():
        out.append(e.events.get())
    return out


def test_sync_aligns_phase_while_playing():
    e = loaded_engine()
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.apply_pending()
    assert raw_error_seconds(e) > 0.030      # starts misaligned
    e.command('sync', 1, True)
    e.apply_pending()
    assert e.decks[1]['sync_mode'] == 'phase'
    assert e.decks[1]['rate'] == pytest.approx(120.0 / 124.0)
    assert raw_error_seconds(e) < 0.030


def test_sync_start_is_quantized_to_the_master_phase():
    e = loaded_engine()
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 50000)
    e.apply_pending()
    e.command('sync', 1, True)               # B still stopped
    e.apply_pending()
    e.command('play', 1, True)
    e.apply_pending()
    assert e.decks[1]['playing']
    assert raw_error_seconds(e) < 0.030


def test_sync_low_confidence_falls_back_to_tempo_only():
    e = loaded_engine(conf_b=0.2)
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.apply_pending()
    drain(e)
    e.command('sync', 1, True)
    e.apply_pending()
    assert e.decks[1]['sync_mode'] == 'tempo'
    assert e.decks[1]['rate'] == pytest.approx(120.0 / 124.0)
    assert e.decks[1]['position'] == 100000  # never shifted
    assert not [ev for ev in drain(e) if ev['action'] == 'sync_align']


def test_phase_lock_corrects_drift_and_journals_it():
    e = loaded_engine()
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.command('sync', 1, True)
    e.apply_pending()
    drain(e)
    # Simulate accumulated drift: a tenth of a beat out.
    beat_b = RATE * 60 / 124.0
    e.decks[1]['position'] += 0.1 * beat_b
    e.render(512)
    assert raw_error_seconds(e) < 0.030
    aligns = [ev for ev in drain(e)
              if ev['action'] == 'sync_align' and ev['deck'] == 1]
    # The event records the position at the nudge (the block's
    # first frame); the deck then advanced rate x 512 within the
    # same render.
    assert aligns and aligns[-1]['value'] == pytest.approx(
        e.decks[1]['position'] - e.decks[1]['rate'] * 512, abs=1)


def test_cue_on_a_beat_anchors_a_slightly_off_grid():
    true_offset = 9000
    beat_b = RATE * 60 / 124.0
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav'))
    # B's grid is 0.1 beats late; its cue sits on a TRUE beat.
    e.command('load', 1, make_track(
        124.0, int(true_offset + 0.1 * beat_b), 0.9, name='b.wav'))
    e.apply_pending()
    cue_pos = int(true_offset + 40 * beat_b)
    e.command('seek', 1, cue_pos); e.command('cue_set', 1)
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.command('sync', 1, True)
    e.apply_pending()
    ds = e.decks[1]
    err = (raw_phase(e.decks[0]['position'], 0, 120.0)
           - raw_phase(ds['position'], true_offset, 124.0)) % 1.0
    assert min(err, 1.0 - err) * 60.0 / 124.0 < 0.030


def test_cue_far_from_beats_does_not_anchor():
    true_offset = 9000
    beat_b = RATE * 60 / 124.0
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav'))
    e.command('load', 1, make_track(
        124.0, int(true_offset + 0.1 * beat_b), 0.9, name='b.wav'))
    e.apply_pending()
    cue_pos = int(true_offset + 40.4 * beat_b)   # 0.4 beats off-grid
    e.command('seek', 1, cue_pos); e.command('cue_set', 1)
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.command('sync', 1, True)
    e.apply_pending()
    ds = e.decks[1]
    err = (raw_phase(e.decks[0]['position'], 0, 120.0)
           - raw_phase(ds['position'], true_offset, 124.0)) % 1.0
    # The bad grid was trusted as-is: ~0.1 beats of true error.
    assert min(err, 1.0 - err) * 60.0 / 124.0 > 0.040


# ------------------------------------------------------------------
# rekordbox hot-cue anchor (user 2026-11-28: the cues he placed in
# rekordbox should feed the beat matching)
# ------------------------------------------------------------------

def _cue(num, start, type_='0', name=''):
    return dict(name=name, type=type_, start=start, end=None,
                num=num)


def test_apply_cue_anchor_picks_hot_cue_a():
    track = make_track(124.0, 0, 0.2)
    cues = [_cue(-1, 1.0, name='memory'),      # memory cue: no
            _cue(1, 30.0, name='B'),
            _cue(0, 0.37, name='A'),
            _cue(2, 60.0, type_='4', name='loop C')]
    assert apply_cue_anchor(track, cues) == int(round(0.37 * RATE))
    assert track.anchor_cue == int(round(0.37 * RATE))


def test_apply_cue_anchor_needs_a_hot_cue():
    track = make_track(124.0, 0, 0.2)
    assert apply_cue_anchor(track, [_cue(-1, 1.0)]) is None
    assert apply_cue_anchor(track, []) is None
    assert apply_cue_anchor(track, None) is None
    assert track.anchor_cue is None


def test_rekordbox_anchor_trumps_analysis_and_low_confidence():
    true_offset = 9000
    beat_b = RATE * 60 / 124.0
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav'))
    # B: analysis phase is 0.3 beats WRONG and unconfident — on
    # analysis alone SYNC would be tempo-only. The user's Hot
    # Cue A sits on a true beat and re-phases the grid.
    slave = make_track(124.0, int(true_offset + 0.3 * beat_b), 0.2,
                       name='b.wav')
    apply_cue_anchor(slave, [_cue(0, (true_offset + 20 * beat_b)
                                  / RATE)])
    e.command('load', 1, slave)
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.command('sync', 1, True)
    e.apply_pending()
    assert e.decks[1]['sync_mode'] == 'phase'
    err = (raw_phase(e.decks[0]['position'], 0, 120.0)
           - raw_phase(e.decks[1]['position'], true_offset,
                       124.0)) % 1.0
    assert min(err, 1.0 - err) * 60.0 / 124.0 < 0.030


# ------------------------------------------------------------------
# Replay reproduces the alignment
# ------------------------------------------------------------------

def test_journal_replay_reproduces_the_phase_shift():
    e = loaded_engine()
    e.command('seek', 0, 200000); e.command('play', 0, True)
    e.command('seek', 1, 100000); e.command('play', 1, True)
    e.command('sync', 1, True)
    e.apply_pending()
    aligned = e.decks[1]['position']
    # Let time pass so the replay has segments of real length
    # (a trailing innocuous event pins the journal's end frame).
    e.render(4800)
    e.command('cross', value=0.5)
    e.apply_pending()
    events = drain(e)
    assert [ev for ev in events if ev['action'] == 'sync_align']
    recon = performance.reconstruct(
        events,
        durations={'a.wav': 30.0, 'b.wav': 30.0},
        bpms={'a.wav': 120.0, 'b.wav': 124.0},
        set_bpm=120.0)
    offsets = [seg['offset_frames'] for seg in recon['segments'][1]]
    assert any(abs(o - aligned) <= 2 for o in offsets)
    rates = [seg['rate'] for seg in recon['segments'][1]]
    assert rates and all(r == pytest.approx(120.0 / 124.0)
                         for r in rates)


# ------------------------------------------------------------------
# App level: set-list cues reach the loaded track (offscreen)
# ------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QApplication
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False


@pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')
def test_setlist_cues_anchor_the_loaded_track(tmp_path):
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    path = write(tmp_path, 'anchored.wav',
                 click_audio(124.0, 0.37, 30))
    # The set list carries the rekordbox cues for this file, as
    # setlist_to_clip_tracks would emit them.
    win.clip_tracks = [dict(
        path=path, title='anchored', bpm=124.0, key=None,
        cues=[_cue(-1, 5.0, name='memory'), _cue(0, 0.37, name='A')])]
    track = load_track(path)
    win.loaded(1, track, '')
    assert track.anchor_cue == int(round(0.37 * RATE))
    win.close()
