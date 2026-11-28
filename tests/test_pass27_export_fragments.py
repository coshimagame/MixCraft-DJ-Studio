"""Pass 27 — the exported set must contain the performance,
whole.

The user's real pass-26 export (mixcraft-performance.als,
dissected for this pass) held 98 arrangement clips for 3 songs:
pass 26's phase lock nudged BOTH decks in micro-seeks, the
journal recorded every nudge, and replay cut a new clip at
each one — ~0.09-beat slivers, invisible at normal Live zoom,
so the set looked empty. The clones' FileRefs were hybrids
too (performed <Path>, the DONOR's <Name>, relative-path
elements and <Data> blob), and the timeline kept the minutes
of setup silence before the first play, so even the head of
the arrangement was empty.

These tests pin the pass-27 fixes against synthetic journals
of the same shape (no real file needed):
  - sub-0.1-beat sync_align corrections are absorbed, never
    clip boundaries; coalescing joins position-continuous
    fragments; a musical-size align and a jog seek still cut;
  - mutually-synced decks never phase-lock (no nudge storm in
    the journal in the first place);
  - degenerate journals (nothing ever played) are refused in
    plain language — never a silently empty .als;
  - the base's own arrangement content is cleared, not leaked;
  - every clip's FileRef names the performed file in BOTH the
    <Path>/<Name> form and the genuine Live 10 <Data> blob,
    first clip included;
  - the timeline is rebased so the first played segment sits
    at Time 0;
  - writer-allocated Ids are unique and NextPointeeId is
    consistent (LomId stays the donor's — genuine Live saves
    ship LomId 0 on their own clips too).
"""
import gzip
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

import als_writer
import perf_als
from als_writer import AlsError
from engine import RATE, Engine, Track


def F(seconds):
    return int(seconds * RATE)


# --- base fixture (same shape as tests/test_perf_als.py) -----------

def _param(tag, value, target_id):
    return (f'<{tag}><Manual Value="{value}"/>'
            f'<AutomationTarget Id="{target_id}">'
            f'<LockEnvelope Value="0"/></AutomationTarget></{tag}>')


def _track(track_id, name, base, donor=False, eq=True):
    slot = (f'<ClipSlot Id="{track_id + 40}"><ClipSlot><Value>'
            + (DONOR_CLIP if donor else '') +
            '</Value></ClipSlot></ClipSlot>')
    devices = (
        '<Mixer>' + _param('Volume', '0.85', base) + '</Mixer>'
        '<StereoGain>' + _param('Gain', '0', base + 1) + '</StereoGain>'
        + ('<FilterEQ3>' + _param('GainLo', '0', base + 2)
           + _param('GainMid', '0', base + 3)
           + _param('GainHi', '0', base + 4) + '</FilterEQ3>' if eq else '')
        + '<AutoFilter>' + _param('Frequency', '0.5', base + 5)
        + '</AutoFilter>')
    # Genuine shape (pass 27 real-machine finding): the audio
    # arrangement container is MainSequencer/Sample on every
    # audio track; ClipTimeable is the MIDI timeable.
    arranger = ('<Sample><ArrangerAutomation><Events/><Envelopes/>'
                '</ArrangerAutomation></Sample>')
    return (f'<AudioTrack Id="{track_id}"><Name>'
            f'<EffectiveName Value="{name}"/></Name><DeviceChain>'
            + devices +
            '<MainSequencer><ClipSlotList>' + slot + '</ClipSlotList>'
            + arranger + '</MainSequencer></DeviceChain></AudioTrack>')


DONOR_CLIP = (
    '<AudioClip Id="42" Time="0">'
    '<LomId Value="0"/><CurrentStart Value="0"/><CurrentEnd Value="16"/>'
    '<Loop><LoopStart Value="0"/><LoopEnd Value="16"/>'
    '<StartRelative Value="0"/><LoopOn Value="false"/>'
    '<OutMarker Value="16"/><HiddenLoopStart Value="0"/>'
    '<HiddenLoopEnd Value="16"/></Loop>'
    '<Name Value="Donor Clip"/><IsWarped Value="false"/>'
    '<SampleRef><FileRef>'
    '<RelativePathType Value="3"/><RelativePath Value=""/>'
    '<Path Value="C:\\\\donor.wav"/><LivePackName Value=""/>'
    '<LivePackId Value=""/><OriginalFileSize Value="111"/>'
    '<OriginalCrc Value="222"/></FileRef>'
    '<LastModDate Value="333"/>'
    '<DefaultDuration Value="705600"/>'
    '<DefaultSampleRate Value="44100"/></SampleRef>'
    '<WarpMode Value="0"/><WarpMarkers>'
    '<WarpMarker Id="7" SecTime="0" BeatTime="0"/></WarpMarkers>'
    '<Envelopes/></AudioClip>')


def _base_xml(eq_b=True):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
        'Creator="Ableton Live 10.1.43">'
        '<LiveSet><NextPointeeId Value="900"/><Tracks>'
        + _track(10, 'Deck A', 100, donor=True)
        + _track(11, 'Deck B', 200, eq=eq_b)
        + '</Tracks>'
        '<MasterTrack><DeviceChain><Mixer>'
        + _param('Tempo', '100', 301) +
        '</Mixer><FreezeSequencer><AudioSequencer><Sample>'
        '<ArrangerAutomation><Events/><Envelopes/></ArrangerAutomation>'
        '</Sample></AudioSequencer></FreezeSequencer>'
        '</DeviceChain></MasterTrack>'
        '</LiveSet></Ableton>')


def _write_base(tmp_path, xml=None):
    path = tmp_path / 'base.als'
    path.write_bytes(gzip.compress(
        (xml or _base_xml()).encode('utf-8')))
    return path


def _make_wav(path, seconds=20.0, rate=44100):
    frames = int(seconds * rate)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b'\x00\x00\x00\x00' * frames)
    return path


def _ev(frame, action, deck=None, value=None):
    return dict(frame=frame, sample_rate=RATE, action=action,
                deck=deck, value=value)


def _arrangement(track):
    arranger = track.find('./DeviceChain/MainSequencer/Sample/'
                          'ArrangerAutomation')
    if arranger is None:    # MasterTrack: no MainSequencer
        arranger = track.find('./DeviceChain/FreezeSequencer/'
                              'AudioSequencer/Sample/'
                              'ArrangerAutomation')
    return arranger


def _clips(track):
    arranger = _arrangement(track)
    if arranger is None:
        return []
    return arranger.find('Events').findall('AudioClip')


def _generate(tmp_path, events, bpms):
    base = _write_base(tmp_path)
    out = tmp_path / 'perf.als'
    report = perf_als.generate(base, events, out, set_bpm=120.0,
                               bpms=bpms)
    root = als_writer.load_als(out).getroot()
    return report, als_writer._audio_tracks(root)


# --- fragmentation ---------------------------------------------------

def test_sync_align_storm_coalesces_to_one_clip_per_song(tmp_path):
    """The real file's pattern: a phase-lock nudge every 0.25 s,
    each shifting the position by 0.04 beats (past the engine's
    0.03-beat lock threshold, under the 0.1-beat absorb line).
    48 nudges over one 12 s play must export as ONE clip."""
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    wav_b = _make_wav(tmp_path / 'Beta.wav')
    events = [_ev(0, 'load', 0, str(wav_a)),
              _ev(0, 'load', 1, str(wav_b)),
              _ev(F(2), 'play', 0, True),
              _ev(F(3), 'play', 1, True)]
    for k in range(1, 49):
        events.append(_ev(F(2) + k * F(0.25), 'sync_align', 0,
                          k * F(0.25) + k * 960))
    events += [_ev(F(15), 'play', 0, False),
               _ev(F(15), 'play', 1, False)]
    report, tracks = _generate(
        tmp_path, events,
        {str(wav_a): 120.0, str(wav_b): 120.0})
    assert report['clips_written'] == 2
    assert len(_clips(tracks[0])) == 1
    assert len(_clips(tracks[1])) == 1


def test_big_sync_align_still_cuts(tmp_path):
    """A 0.4-beat align is a musical edit, not a correction:
    it stays a clip boundary."""
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    events = [_ev(0, 'load', 0, str(wav_a)),
              _ev(F(1), 'play', 0, True),
              _ev(F(3), 'sync_align', 0, F(2) + 9600),
              _ev(F(5), 'play', 0, False)]
    report, tracks = _generate(tmp_path, events,
                               {str(wav_a): 120.0})
    assert len(_clips(tracks[0])) == 2


def test_jog_seek_still_cuts(tmp_path):
    """An intentional jog seek (+5 s of track position) is a
    boundary and is never coalesced away."""
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    events = [_ev(0, 'load', 0, str(wav_a)),
              _ev(F(1), 'play', 0, True),
              _ev(F(3), 'seek', 0, F(2) + F(5)),
              _ev(F(5), 'play', 0, False)]
    report, tracks = _generate(tmp_path, events,
                               {str(wav_a): 120.0})
    clips = _clips(tracks[0])
    assert len(clips) == 2
    assert float(als_writer._get_value(
        clips[1], 'CurrentStart')) == pytest.approx(14.0)


# --- degenerate journals refuse --------------------------------------

@pytest.mark.parametrize('events', [
    [],                                            # nothing at all
    [_ev(0, 'load', 0, 'x.wav')],                  # loads only
    [_ev(0, 'cross', None, 0.5),                   # knobs only
     _ev(F(1), 'gain', 0, 0.8)],
    [_ev(0, 'load', 0, 'x.wav'),                   # frozen frames:
     _ev(0, 'play', 0, True),                      # played & stopped
     _ev(0, 'play', 0, False)],                    # at frame 0
])
def test_degenerate_journal_refused_not_silently_empty(tmp_path,
                                                       events):
    base = _write_base(tmp_path)
    out = tmp_path / 'perf.als'
    with pytest.raises(AlsError):
        perf_als.generate(base, events, out, set_bpm=120.0,
                          bpms={})
    assert not out.exists()


# --- base hygiene ------------------------------------------------------

def test_base_arrangement_residue_is_cleared(tmp_path):
    """A base that itself carries arrangement content (a set
    the user saved mid-work) must not leak it into the export:
    the deck tracks and the master are cleared first."""
    root = ET.fromstring(_base_xml())
    track_a = root.find('./LiveSet/Tracks/AudioTrack')
    events_el = _arrangement(track_a).find('Events')
    junk = ET.fromstring(DONOR_CLIP)
    junk.find('Name').set('Value', 'Junk Residue')
    events_el.append(junk)
    xml = ET.tostring(root, encoding='unicode')
    base = _write_base(tmp_path, xml=xml)

    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    out = tmp_path / 'perf.als'
    report = perf_als.generate(
        base, [_ev(0, 'load', 0, str(wav_a)),
               _ev(0, 'play', 0, True),
               _ev(F(2), 'play', 0, False)],
        out, set_bpm=120.0, bpms={str(wav_a): 120.0})
    assert report['arrangement_cleared'] >= 1
    tracks = als_writer._audio_tracks(
        als_writer.load_als(out).getroot())
    names = [als_writer._get_value(c, 'Name')
             for c in _clips(tracks[0])]
    assert names == ['Alpha']


# --- FileRef integrity -------------------------------------------------

def _real_form_base(tmp_path):
    """Rebuild the fixture's donor FileRef in the genuine
    Live 10 form (element RelativePath with path elements, a
    <Data> blob, no <Path>) — the shape of the user's real
    template donors."""
    root = ET.fromstring(_base_xml())
    donor = root.find('.//ClipSlotList//AudioClip')
    ref = donor.find('./SampleRef/FileRef')
    for child in list(ref):
        ref.remove(child)
    donor_abs = 'C:\\donor\\Donor Song.wav'
    ET.SubElement(ref, 'HasRelativePath').set('Value', 'true')
    ET.SubElement(ref, 'RelativePathType').set('Value', '1')
    rel = ET.SubElement(ref, 'RelativePath')
    ET.SubElement(rel, 'RelativePathElement').set('Dir', '')
    ET.SubElement(rel, 'RelativePathElement').set('Dir', 'donor')
    ET.SubElement(ref, 'Name').set('Value', 'Donor Song.wav')
    ET.SubElement(ref, 'Type').set('Value', '2')
    data = ET.SubElement(ref, 'Data')
    data.text = (donor_abs + '\0').encode('utf-16-le').hex().upper()
    return _write_base(
        tmp_path, xml=ET.tostring(root, encoding='unicode')), donor_abs


def test_file_ref_names_the_performed_file_on_every_clip(tmp_path):
    base, donor_abs = _real_form_base(tmp_path)
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    out = tmp_path / 'perf.als'
    perf_als.generate(
        base, [_ev(0, 'load', 0, str(wav_a)),
               _ev(F(1), 'play', 0, True),
               _ev(F(3), 'seek', 0, F(2) + F(5)),
               _ev(F(5), 'play', 0, False)],
        out, set_bpm=120.0, bpms={str(wav_a): 120.0})
    root = als_writer.load_als(out).getroot()
    tracks = als_writer._audio_tracks(root)
    clips = _clips(tracks[0])
    assert len(clips) == 2          # first clip included
    performed = str(wav_a.resolve())
    for clip in clips:
        ref = clip.find('./SampleRef/FileRef')
        assert als_writer._get_value(ref, 'Name') == 'Alpha.wav'
        assert als_writer._get_value(ref, 'Path') == performed
        blob = bytes.fromhex(ref.find('Data').text.strip())
        assert blob.decode('utf-16-le') == performed + '\0'
        assert als_writer._get_value(ref, 'HasRelativePath') \
            == 'false'
        assert list(ref.find('RelativePath')) == []
    # The session donor itself is untouched.
    donor = tracks[0].find('.//ClipSlotList//AudioClip')
    dref = donor.find('./SampleRef/FileRef')
    assert als_writer._get_value(dref, 'Name') == 'Donor Song.wav'
    dblob = bytes.fromhex(dref.find('Data').text.strip())
    assert dblob.decode('utf-16-le') == donor_abs + '\0'


# --- timeline rebase ---------------------------------------------------

def test_first_clip_starts_at_zero_despite_setup_gap(tmp_path):
    """Loads at frame 0, first play 90 s later (setup time):
    the export must open ON the music, not on 90 s of air."""
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    events = [_ev(0, 'load', 0, str(wav_a)),
              _ev(F(90), 'play', 0, True),
              _ev(F(94), 'play', 0, False)]
    report, tracks = _generate(tmp_path, events,
                               {str(wav_a): 120.0})
    clips = _clips(tracks[0])
    assert len(clips) == 1
    assert float(clips[0].get('Time')) == 0.0


# --- id discipline -----------------------------------------------------

def _events_two_decks(wav_a, wav_b):
    return [_ev(0, 'load', 0, str(wav_a)),
            _ev(0, 'load', 1, str(wav_b)),
            _ev(0, 'play', 0, True),
            _ev(F(1), 'cross', None, 1.0),
            _ev(F(1), 'play', 1, True),
            _ev(F(2), 'play', 0, False),
            _ev(F(2), 'play', 1, False)]


def test_id_space_and_lomid_match_genuine_saves(tmp_path):
    base = _write_base(tmp_path)
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    wav_b = _make_wav(tmp_path / 'Beta.wav')
    out = tmp_path / 'perf.als'
    perf_als.generate(base, _events_two_decks(wav_a, wav_b), out,
                      set_bpm=120.0,
                      bpms={str(wav_a): 120.0, str(wav_b): 120.0})
    root = als_writer.load_als(out).getroot()
    ids = [int(el.get('Id')) for el in root.iter()
           if el.get('Id') and el.get('Id').lstrip('-').isdigit()]
    # Ids the writer allocated (above the fixture base's 900
    # pointee ceiling) never repeat; NextPointeeId sits exactly
    # one past the maximum, as in a genuine save.
    allocated = [i for i in ids if i >= 900]
    assert len(allocated) == len(set(allocated))
    nxt = root.find('./LiveSet/NextPointeeId')
    assert int(nxt.get('Value')) == max(ids) + 1
    # LomId is left as the donor's — genuine Live saves carry
    # LomId 0 on their own session clips (both donors of the
    # user's real template do), so clones matching that are
    # isomorphic with a real save, not a defect.
    tracks = als_writer._audio_tracks(root)
    for track in tracks:
        for clip in _clips(track):
            assert als_writer._get_value(clip, 'LomId') == '0'


# --- the container Live actually reads --------------------------------

def test_clips_land_in_the_sample_container_not_cliptimeable(tmp_path):
    """THE pass-27 root cause, from the user's Live screenshots:
    the exported set showed his template's session clips but a
    completely blank arrangement. His file's genuine parts
    prove why — a real Live 10 audio track keeps its
    arrangement in MainSequencer/Sample/ArrangerAutomation
    (empty in his base), the master in FreezeSequencer/
    AudioSequencer/Sample, and ClipTimeable (the MIDI timeable)
    appears NOWHERE in the genuine structure. The old writer
    created ClipTimeable and put everything there; Live
    ignored the whole subtree. Pin the genuine locations."""
    base = _write_base(tmp_path)
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    wav_b = _make_wav(tmp_path / 'Beta.wav')
    out = tmp_path / 'perf.als'
    perf_als.generate(base, _events_two_decks(wav_a, wav_b), out,
                      set_bpm=120.0,
                      bpms={str(wav_a): 120.0, str(wav_b): 120.0})
    root = als_writer.load_als(out).getroot()
    assert list(root.iter('ClipTimeable')) == []
    tracks = als_writer._audio_tracks(root)
    assert len(_clips(tracks[0])) == 1
    assert len(_clips(tracks[1])) == 1
    # The tempo envelope reaches the master's genuine
    # container (the old code raised on the master, which has
    # no MainSequencer, and silently dropped it).
    master = root.find('./LiveSet/MasterTrack')
    menv = _arrangement(master).find('Envelopes')
    pointees = [e.find('./EnvelopeTarget/PointeeId').get('Value')
                for e in menv]
    assert '301' in pointees


# --- engine: mutual sync never phase-locks -----------------------------

def _make_track(bpm, offset, conf, seconds=30, name='t.wav'):
    frames = int(seconds * RATE)
    return Track(name, np.zeros((frames, 2), dtype='float32'),
                 np.ones(16, dtype='float32'), bpm, 1.0,
                 beat_offset=offset, beat_confidence=conf)


def _drain(engine):
    out = []
    while not engine.events.empty():
        out.append(engine.events.get())
    return out


def test_mutual_sync_never_phase_locks():
    """Both decks SYNC'd (the real session's state): the lock
    must stand down entirely — pass 26 locked each deck against
    the other's just-moved position, minting the sync_align
    storm that fragmented the export."""
    e = Engine()
    e.command('load', 0, _make_track(120.0, 0, 0.9, name='a.wav'))
    e.command('load', 1, _make_track(124.0, 9000, 0.9,
                                     name='b.wav'))
    e.apply_pending()
    e.command('seek', 0, 200000)
    e.command('play', 0, True)
    e.command('seek', 1, 100000)
    e.command('play', 1, True)
    e.command('sync', 0, True)
    e.command('sync', 1, True)
    e.apply_pending()
    _drain(e)
    beat_b = RATE * 60 / 124.0
    e.decks[1]['position'] += 0.1 * beat_b     # visible drift
    for _ in range(8):
        e.render(512)
    assert not [ev for ev in _drain(e)
                if ev['action'] == 'sync_align']
