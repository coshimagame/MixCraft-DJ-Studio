import numpy as np
import pytest
from engine import Engine, RATE, Track
from analysis import BeatGrid, AnalysisCache, estimate_key, keys_compatible
from automix import TrackInfo, plan_transition, plan_candidates, plan_set
from midi_map import PROFILES, MidiLearn, dispatch, led_feedback, save_mapping, load_mapping
from library import Library, import_rekordbox_xml

def track(value=0.3, frames=48000, bpm=120):
    return Track('t.wav', np.full((frames, 2), value, dtype='float32'), np.ones(10), bpm, 1)

def prepared(frames=48000):
    e = Engine()
    for i in range(2):
        e.command('load', i, track(0.2 if i == 0 else 0.4, frames))
        e.command('volume', i, 1); e.command('play', i, True)
    e.command('master', value=1)
    e.render(512)  # settle smoothing ramps
    return e

def test_smoothing_prevents_clicks():
    e = prepared()
    e.command('volume', 0, 0)   # abrupt mute mid-playback
    out = e.render(512)
    diffs = np.abs(np.diff(out[:, 0]))
    assert diffs.max() < 0.05   # an instant mute of a 0.2 signal would jump 0.2

def test_soft_limiter_bounds_and_transparency():
    e = prepared()
    out = e.render(256)
    assert np.max(np.abs(out)) <= 0.98
    e2 = Engine()
    for i in range(2):
        e2.command('load', i, track(5)); e2.command('play', i, True)
    e2.render(512)
    out = e2.render(256)
    assert np.max(np.abs(out)) <= 0.98
    assert e2.overloads >= 1

def test_eq_kill_low_removes_dc_keeps_high():
    # DC is pure "low band": killing low must silence a DC track after settling
    e = Engine()
    e.command('load', 0, track(0.4)); e.command('volume', 0, 1)
    e.command('master', value=1); e.command('cross', value=0)
    e.command('eq_low', 0, 0.0); e.command('play', 0, True)
    for _ in range(40): out = e.render(512)
    assert np.max(np.abs(out)) < 0.05

def test_eq_neutral_is_transparent():
    e = prepared()
    out = e.render(256)
    assert np.allclose(out, 0.6/np.sqrt(2), atol=1e-5)

def test_filter_lowpass_attenuates_high_frequency():
    sr = RATE
    t = np.arange(sr) / sr
    audio = np.stack([np.sin(2*np.pi*8000*t)*0.5]*2, axis=1).astype('float32')
    e = Engine()
    e.command('load', 0, Track('hf.wav', audio, np.ones(10), 120, 1))
    e.command('volume', 0, 1); e.command('master', value=1); e.command('cross', value=0)
    e.command('filter', 0, 0.0); e.command('play', 0, True)
    for _ in range(20): out = e.render(512)
    assert np.sqrt(np.mean(out**2)) < 0.1

def test_gain_doubles_level():
    e = prepared()
    base = e.render(128).copy()
    e.command('gain', 0, 2.0); e.command('gain', 1, 2.0)
    e.render(512)
    out = e.render(128)
    assert np.allclose(out, base*2, atol=0.02)

def test_cue_and_hotcue():
    e = prepared()
    e.command('play', 0, False); e.command('play', 1, False); e.render(10)
    e.command('seek', 0, 1000); e.render(10)
    e.command('cue_set', 0); e.render(10)
    e.command('seek', 0, 5000); e.render(10)
    e.command('cue_jump', 0); e.render(10)
    assert e.decks[0]['position'] == 1000
    e.command('seek', 0, 2000); e.command('hotcue_set', 0, 3); e.render(10)
    e.command('seek', 0, 0); e.render(10)
    e.command('hotcue_jump', 0, 3); e.render(10)
    assert e.decks[0]['position'] == 2000

def test_loop_wraps():
    e = prepared(frames=100000)
    e.command('seek', 0, 1000); e.command('loop_in', 0); e.render(10)
    e.command('seek', 0, 3000); e.command('loop_out', 0); e.render(10)
    e.command('seek', 0, 2500); e.render(10)
    for _ in range(10): e.render(512)
    assert 1000 <= e.decks[0]['position'] <= 3000 + 512
    e.command('loop_exit', 0); e.render(10)
    assert not e.decks[0]['loop_active']

def test_quantize_snaps_seek_to_beat():
    e = prepared()
    e.command('play', 0, False); e.command('play', 1, False)
    e.command('quantize', 0, True); e.render(10)
    e.command('seek', 0, 12345); e.render(10)
    beat = RATE * 60 / 120
    assert (e.decks[0]['position'] % beat) == 0

def test_sync_sets_rate_from_bpm():
    e = Engine()
    e.command('load', 0, track(bpm=120)); e.command('load', 1, track(bpm=60))
    e.render(10)
    e.command('sync', 1, True); e.render(10)
    assert e.decks[1]['rate'] == pytest.approx(2.0)

def test_beatgrid_math():
    g = BeatGrid(120, offset=100)
    assert g.beat_frames == RATE/2
    assert g.nearest_beat(100 + 13000) == 100 + 24000  # one beat at 120 BPM = 24000 frames
    assert g.nearest_bar(100) == 100
    g.nudge(50); assert g.offset == 150

def test_key_estimate_and_compatibility():
    t = np.arange(RATE*4)/RATE
    # A minor triad (A2+C3+E3-ish frequencies)
    audio = sum(np.sin(2*np.pi*f*t) for f in (110, 130.81, 164.81)) * 0.2
    audio = np.stack([audio]*2, axis=1).astype('float32')
    key, camelot, conf = estimate_key(audio)
    assert key is not None and camelot is not None and conf > 0
    assert keys_compatible('A minor', '8A', 'C major', '8B') is True
    assert keys_compatible('A minor', '8A', 'Eb major', '5B') is False
    assert keys_compatible(None, None, 'C major', '8B') is None

def test_analysis_cache_roundtrip(tmp_path):
    import soundfile as sf
    p = tmp_path/'a.wav'
    sf.write(p, np.zeros((RATE, 2), dtype='float32'), RATE)
    cache = AnalysisCache(tmp_path/'c.db')
    cache.put(p, dict(bpm=123.0, confidence=0.5, beat_offset=10, key='A minor', camelot='8A', key_confidence=0.7))
    got = cache.get(p)
    assert got['bpm'] == 123.0 and got['camelot'] == '8A'
    cache.close()

def test_automix_plan_is_phrase_aligned():
    a = TrackInfo('a.wav', 120, RATE*240, energy=0.6)
    b = TrackInfo('b.wav', 122, RATE*240, key='A minor', camelot='8A')
    a.key, a.camelot = 'C major', '8B'
    plan = plan_transition(a, b, 32)
    bar = RATE*2  # 120bpm, 4 beats
    assert plan.mix_out_frame % bar == 0
    assert plan.overlap_bars == 32
    assert plan.bass_swap_frame > plan.mix_out_frame
    assert plan.key_ok is True
    plans = plan_candidates(a, b)
    assert [p.overlap_bars for p in plans] and len(plans) == 4
    assert len(plan_set([a, b, a])) == 2

def test_midi_profiles_dispatch_and_learn(tmp_path):
    for name in PROFILES: assert PROFILES[name]['cc']
    cmd = dispatch(APC := PROFILES['APC40 mkII'], 'cc', 0, 56, 127)
    assert cmd == ('cross', None, 1.0)
    learn = MidiLearn()
    learn.arm('filter', 1)
    assert learn.feed('cc', 0, 99, 64) == ('filter', 1)
    assert dispatch(learn.mapping, 'cc', 0, 99, 127) == ('filter', 1, 1.0)
    leds = led_feedback('APC40 mkII', [True, False])
    assert leds and leds[0][2] == 1
    save_mapping(learn.mapping, tmp_path/'m.json')
    assert load_mapping(tmp_path/'m.json') == learn.mapping

def test_library_and_rekordbox(tmp_path):
    lib = Library(tmp_path/'lib.db')
    lib.add('/music/a.wav', 'Song A', 120, 'A minor', '8A')
    lib.add('/music/b.wav', 'Song B', 122)
    assert len(lib.search('song a')) == 1
    lib.set_playlist('set1', ['/music/a.wav', '/music/b.wav'])
    assert len(lib.playlist('set1')) == 2
    xml = tmp_path/'rekordbox.xml'
    xml.write_text('<DJ_PLAYLISTS><COLLECTION>'
                   '<TRACK Name="X" AverageBpm="124.0" Tonality="Am" Location="file://localhost/music/x.wav"/>'
                   '</COLLECTION></DJ_PLAYLISTS>', encoding='utf-8')
    tracks = import_rekordbox_xml(xml)
    assert tracks[0]['bpm'] == 124.0 and tracks[0]['path'].endswith('x.wav')
    lib.close()
