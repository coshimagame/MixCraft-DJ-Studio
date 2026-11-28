"""Pass 35 (user request 2026-11-28: 「オートシンク」
「拍子をあわせるのとかできないの？」): AUTO SYNC.

While the mode is on (the default), a deck that starts playing
against a playing deck engages sync by itself: tempo match,
beat-phase alignment (pass 26) and — when BOTH tracks carry
bar information — a whole-beat shift so the bar lines coincide
too. Bar information is a bar offset the user stepped (the
analysis cache marks it known) or a rekordbox anchor cue; the
analysis default of 0 is NOT information, and a track without
it gets beat-phase only (the deck's sync_bar says False — the
app posts the 「小節情報なし」 note). Untrusted grids stay
tempo-only. With the mode off, SYNC is exactly pass 26.

Fixtures are hand-built Track objects with exact grids (the
test_sync_phase pattern), so bar coincidence is checked against
raw phase arithmetic in beats, not the engine's own helpers.
"""
import numpy as np
import pytest
import soundfile as sf

from analysis import AnalysisCache
from engine import Engine, RATE, Track, load_track
import performance

MS30_BEATS = 0.030 / 0.5        # 30 ms at 120 BPM, in beats


def make_track(bpm, offset, conf, seconds=60, name='t.wav',
               bar_offset=0, bar_known=False, anchor=None):
    frames = int(seconds * RATE)
    track = Track(name, np.zeros((frames, 2), dtype='float32'),
                  np.ones(16, dtype='float32'), bpm, 1.0,
                  beat_offset=offset, beat_confidence=conf,
                  bar_offset=bar_offset, bar_offset_known=bar_known)
    if anchor is not None:
        track.anchor_cue = anchor
    return track


def beat_phase(pos, offset, bpm):
    return ((pos - offset) / (RATE * 60.0 / bpm)) % 1.0


def bar_phase(pos, offset, bpm, bar_offset):
    return ((pos - offset) / (RATE * 60.0 / bpm)
            - bar_offset) % 4.0


def phase_err_beats(a, b):
    """Circular distance between two beat phases, in beats."""
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def bar_err_beats(a, b):
    d = abs(a - b) % 4.0
    return min(d, 4.0 - d)


def drain(e):
    out = []
    while not e.events.empty():
        out.append(e.events.get())
    return out


def auto_engine(master_kw=None, slave_kw=None):
    """Deck A playing, deck B loaded and stopped. Both grids
    trusted; bar offsets known unless a kw says otherwise."""
    mk = dict(bpm=120.0, offset=0, bar_offset=0, bar_known=True)
    mk.update(master_kw or {})
    sk = dict(bpm=124.0, offset=9000, bar_offset=2, bar_known=True)
    sk.update(slave_kw or {})
    e = Engine()
    e.command('load', 0, make_track(name='a.wav', conf=0.9, **mk))
    e.command('load', 1, make_track(name='b.wav', conf=0.9, **sk))
    e.apply_pending()
    return e


# ------------------------------------------------------------------
# The headline: play-start engage aligns beats AND bars
# ------------------------------------------------------------------

def test_auto_sync_is_on_by_default():
    assert Engine().auto_sync is True


def test_auto_sync_aligns_beat_and_bar_on_start():
    e = auto_engine()
    e.command('seek', 0, 200000)
    e.command('play', 0, True)
    e.command('seek', 1, 100000)
    e.apply_pending()
    drain(e)
    e.command('play', 1, True)
    e.apply_pending()
    d = e.decks[1]
    assert d['sync'] is True
    assert d['rate'] == pytest.approx(120.0 / 124.0)
    assert d['sync_mode'] == 'phase'
    assert d['sync_bar'] is True
    # Beat phases coincide (the pass-26 budget: 30 ms).
    err = phase_err_beats(
        beat_phase(d['position'], 9000, 124.0),
        beat_phase(e.decks[0]['position'], 0, 120.0))
    assert err * (60.0 / 124.0) < 0.030
    # And so do the BAR phases: A's bars start at beats
    # (k - 0) % 4 == 0 of its grid, B's at (k - 2) % 4 == 0.
    berr = bar_err_beats(
        bar_phase(d['position'], 9000, 124.0, 2),
        bar_phase(e.decks[0]['position'], 0, 120.0, 0))
    assert berr < 0.01
    # The engagement is journalled as sync + sync_align (in
    # that order) so the replay can reproduce it exactly.
    events = drain(e)
    kinds = [(ev['action'], ev['deck']) for ev in events]
    assert kinds.index(('sync', 1)) < kinds.index(('sync_align', 1))
    align = [ev for ev in events if ev['action'] == 'sync_align'][0]
    assert align['value'] == int(round(d['position']))


def test_auto_sync_bar_shift_never_crosses_the_start():
    # Geometry with forward = 3 beats (backward would be -1):
    # A sits on bar phase 0; B starts a quarter beat off a beat
    # whose bar phase is 1.25, so after the -0.25 beat phase
    # shift B's bar phase is 1.0 and the bar gap is 3 forward /
    # 1 back. The backward step would cross B's start position,
    # so the next bar forward (+3) must be taken.
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav',
                                    bar_offset=0, bar_known=True))
    e.command('load', 1, make_track(120.0, 0, 0.9, name='b.wav',
                                    bar_offset=0, bar_known=True))
    e.command('seek', 0, 192000)          # k=8: bar phase 0
    e.command('play', 0, True)
    e.command('seek', 1, 222000)          # k=9.25: bar phase 1.25
    e.apply_pending()
    e.command('play', 1, True)
    e.apply_pending()
    d = e.decks[1]
    assert d['sync_bar'] is True
    assert d['position'] == pytest.approx(288000, abs=2)   # +3 beats net of the -0.25 phase shift
    assert d['position'] > 222000                          # never backwards past the start
    berr = bar_err_beats(bar_phase(d['position'], 0, 120.0, 0),
                         bar_phase(e.decks[0]['position'], 0, 120.0, 0))
    assert berr < 0.01


def test_bar_shift_helper_prefers_the_smaller_seek_when_free():
    # The same 3-forward geometry, but with the floor far below
    # the slave's position (room before the start): the helper
    # itself must pick -1 beat, the smaller absolute seek.
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav',
                                    bar_offset=0, bar_known=True))
    e.command('load', 1, make_track(120.0, 0, 0.9, name='b.wav',
                                    bar_offset=0, bar_known=True))
    e.apply_pending()
    e.decks[0]['position'] = 192000.0     # bar phase 0
    slave_pos = 216000.0                  # k=9: bar phase 1
    frames, known = e._bar_shift_frames(
        e.decks[1], e.decks[0], slave_pos, floor_pos=0)
    assert known is True
    assert frames == pytest.approx(-24000.0)   # -1 beat, not +3


# ------------------------------------------------------------------
# Honesty: no bar info -> beat phase only; no grid -> tempo only
# ------------------------------------------------------------------

def test_auto_sync_without_slave_bar_info_aligns_beat_only():
    e = auto_engine(slave_kw=dict(bar_known=False))
    e.command('seek', 0, 200000)
    e.command('play', 0, True)
    e.command('seek', 1, 100000)
    e.apply_pending()
    e.command('play', 1, True)
    e.apply_pending()
    d = e.decks[1]
    assert d['sync_mode'] == 'phase'
    assert d['sync_bar'] is False
    # Beat aligned...
    err = phase_err_beats(
        beat_phase(d['position'], 9000, 124.0),
        beat_phase(e.decks[0]['position'], 0, 120.0))
    assert err * (60.0 / 124.0) < 0.030
    # ...by at most the pass-26 half-beat shift — no whole-beat
    # bar jump was smuggled in...
    beat_b = RATE * 60.0 / 124.0
    assert abs(d['position'] - 100000) <= 0.5 * beat_b + 2
    # ...and the bars really are still apart in this fixture
    # (2 beats), i.e. nothing was faked.
    berr = bar_err_beats(
        bar_phase(d['position'], 9000, 124.0, 2),
        bar_phase(e.decks[0]['position'], 0, 120.0, 0))
    assert berr > 1.0


def test_auto_sync_without_master_bar_info_aligns_beat_only():
    e = auto_engine(master_kw=dict(bar_known=False))
    e.command('seek', 0, 200000)
    e.command('play', 0, True)
    e.command('seek', 1, 100000)
    e.apply_pending()
    e.command('play', 1, True)
    e.apply_pending()
    assert e.decks[1]['sync_mode'] == 'phase'
    assert e.decks[1]['sync_bar'] is False


def test_auto_sync_untrusted_grid_stays_tempo_only():
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav',
                                    bar_offset=0, bar_known=True))
    e.command('load', 1, make_track(124.0, 9000, 0.2, name='b.wav',
                                    bar_offset=2, bar_known=True))
    e.command('seek', 0, 200000)
    e.command('play', 0, True)
    e.command('seek', 1, 100000)
    e.apply_pending()
    drain(e)
    e.command('play', 1, True)
    e.apply_pending()
    d = e.decks[1]
    assert d['sync'] is True
    assert d['sync_mode'] == 'tempo'
    assert d['sync_bar'] is None
    assert d['rate'] == pytest.approx(120.0 / 124.0)
    assert d['position'] == 100000        # never shifted
    assert not [ev for ev in drain(e)
                if ev['action'] == 'sync_align']


def test_rekordbox_anchor_counts_as_bar_info():
    # The slave's bar knowledge is its Hot Cue A (a downbeat
    # by the user's own marking); bars count from the cue.
    beat_b = RATE * 60.0 / 124.0
    anchor = int(9000 + 20 * beat_b)
    e = Engine()
    e.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav',
                                    bar_offset=0, bar_known=True))
    e.command('load', 1, make_track(124.0, 9000, 0.9, name='b.wav',
                                    anchor=anchor))
    e.command('seek', 0, 192000)          # master bar phase 0
    e.command('play', 0, True)
    start = anchor + 6.5 * beat_b         # bar phase 2.5 from cue
    e.command('seek', 1, int(start))
    e.apply_pending()
    e.command('play', 1, True)
    e.apply_pending()
    d = e.decks[1]
    assert d['sync_bar'] is True
    slave_bar = ((d['position'] - anchor) / beat_b) % 4.0
    master_bar = bar_phase(e.decks[0]['position'], 0, 120.0, 0)
    assert bar_err_beats(slave_bar, master_bar) < 0.01


# ------------------------------------------------------------------
# Mode off: exactly pass 26
# ------------------------------------------------------------------

def test_auto_off_play_does_not_engage_and_manual_sync_skips_bars():
    e = auto_engine()
    e.command('auto_sync', None, False)
    e.command('seek', 0, 200000)
    e.command('play', 0, True)
    e.command('seek', 1, 100000)
    e.apply_pending()
    e.command('play', 1, True)
    e.apply_pending()
    d = e.decks[1]
    assert d['sync'] is False
    assert d['rate'] == 1.0
    assert d['position'] == 100000
    # The manual pad: tempo + beat phase, NO bar alignment.
    e.command('sync', 1, True)
    e.apply_pending()
    assert d['sync_mode'] == 'phase'
    assert d['sync_bar'] is None
    err = phase_err_beats(
        beat_phase(d['position'], 9000, 124.0),
        beat_phase(e.decks[0]['position'], 0, 120.0))
    assert err * (60.0 / 124.0) < 0.030
    berr = bar_err_beats(
        bar_phase(d['position'], 9000, 124.0, 2),
        bar_phase(e.decks[0]['position'], 0, 120.0, 0))
    assert berr > 1.0                     # bars left as performed


def test_auto_sync_mode_toggle_is_journalled():
    e = Engine()
    e.command('auto_sync', None, False)
    e.apply_pending()
    assert e.auto_sync is False
    events = drain(e)
    assert [(ev['action'], ev['value']) for ev in events] == [
        ('auto_sync', False)]


def test_replay_carries_the_auto_sync_mode():
    recon = performance._Reconstructor({}, {})
    assert recon.auto_sync is True
    recon.run([dict(frame=0, sample_rate=RATE, action='auto_sync',
                    deck=None, value=False)])
    assert recon.auto_sync is False


# ------------------------------------------------------------------
# Replay / export: the bar-aligned performance is reproduced
# ------------------------------------------------------------------

def test_journal_replay_reproduces_the_bar_aligned_start():
    e = auto_engine()
    e.command('seek', 0, 200000)
    e.command('play', 0, True)
    e.command('seek', 1, 100000)
    e.command('play', 1, True)
    e.apply_pending()
    aligned = e.decks[1]['position']
    e.render(4800)
    e.command('cross', value=0.5)
    e.apply_pending()
    events = drain(e)
    recon = performance.reconstruct(
        events,
        durations={'a.wav': 60.0, 'b.wav': 60.0},
        bpms={'a.wav': 120.0, 'b.wav': 124.0},
        set_bpm=120.0)
    segs = recon['segments'][1]
    assert len(segs) == 1
    assert segs[0]['offset_frames'] == pytest.approx(aligned, abs=2)
    assert segs[0]['rate'] == pytest.approx(120.0 / 124.0)


# ------------------------------------------------------------------
# Bar-offset provenance: the analysis cache
# ------------------------------------------------------------------

def test_cache_marks_only_stepped_offsets_known(tmp_path):
    path = tmp_path / 'x.wav'
    sf.write(path, np.zeros((RATE, 2), dtype='float32'), RATE)
    cache = AnalysisCache(tmp_path / 'c.db')
    cache.put(str(path), dict(bpm=120.0, confidence=0.9,
                              beat_offset=0, key=None, camelot=None,
                              key_confidence=0.0, beat_confidence=0.9,
                              bar_offset=0))
    row = cache.get(str(path))
    assert not row['bar_offset_known']        # analysis default
    cache.set_bar_offset(str(path), 2)
    row = cache.get(str(path))
    assert row['bar_offset'] == 2
    assert row['bar_offset_known'] == 1       # the user stated it
    cache.close()


def test_load_track_carries_the_known_flag(tmp_path):
    path = tmp_path / 'x.wav'
    audio = np.zeros((int(2 * RATE), 2), dtype='float32')
    sf.write(path, audio, RATE)
    cache = AnalysisCache(tmp_path / 'c.db')
    cache.put(str(path), dict(bpm=120.0, confidence=0.9,
                              beat_offset=0, key=None, camelot=None,
                              key_confidence=0.0, beat_confidence=0.9,
                              bar_offset=0))
    track = load_track(str(path), cache)
    assert track.bar_offset_known is False
    cache.set_bar_offset(str(path), 3)
    track = load_track(str(path), cache)
    assert track.bar_offset == 3
    assert track.bar_offset_known is True
    cache.close()


# ------------------------------------------------------------------
# UI: the chip, the modal pad, the status note
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
def test_auto_sync_chip_starts_on_and_drives_engine(window):
    chip = window.auto_sync_chip
    assert chip.isCheckable()
    assert chip.isChecked() is True            # default ON
    assert chip.text() == 'オートシンク'        # jp default language
    assert window.engine.auto_sync is True
    chip.setChecked(False)
    window.engine.apply_pending()
    assert window.engine.auto_sync is False
    window.tick()
    assert window.auto_sync_on is False
    chip.setChecked(True)
    window.engine.apply_pending()
    assert window.engine.auto_sync is True
    window.tick()
    assert window.auto_sync_on is True


@pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')
def test_sync_pad_switches_auto_off_then_works_manually(window):
    eng = window.engine
    assert eng.auto_sync is True
    # AUTO on: a pad press is the mode's off-switch — it must
    # NOT engage the deck's own sync.
    window.decks[0].sync.click()
    eng.apply_pending()
    assert eng.auto_sync is False
    assert eng.decks[0]['sync'] is False
    window.tick()                              # chip follows
    assert window.auto_sync_chip.isChecked() is False
    # AUTO off: the same pad is the pass-26 manual engage.
    window.decks[1].sync.click()
    eng.apply_pending()
    assert eng.decks[1]['sync'] is True
    window.decks[1].sync.click()
    eng.apply_pending()
    assert eng.decks[1]['sync'] is False
    # Restore the default for any later test in this module.
    window.auto_sync_chip.setChecked(True)
    eng.apply_pending()
    assert eng.auto_sync is True


@pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')
def test_status_bar_notes_missing_bar_info(window):
    eng = window.engine
    eng.command('load', 0, make_track(120.0, 0, 0.9, name='a.wav',
                                      bar_offset=0, bar_known=True))
    eng.command('load', 1, make_track(124.0, 9000, 0.9, name='b.wav',
                                      bar_offset=2, bar_known=False))
    eng.command('seek', 0, 200000)
    eng.command('play', 0, True)
    eng.command('seek', 1, 100000)
    eng.command('play', 1, True)
    eng.apply_pending()
    assert eng.decks[1]['sync_bar'] is False
    window.tick()
    assert '小節情報なし' in window.status.text()
