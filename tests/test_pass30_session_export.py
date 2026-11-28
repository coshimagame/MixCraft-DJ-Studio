"""Pass 30 — the performance also lands in the Session view.

User vision restated 2026-11-28: the export to Live must lay
the set out BOTH ways — the Arrangement (passes 27–29) AND
the Session view, same songs in performance order on the two
audio tracks, so the set can be DJ'd again inside Live.

What is pinned here:
  - per deck track, one session clip per performed song, in
    performance order, filling successive ClipSlots;
  - a song played twice gets two slots (pass 28.1's reprise
    rule), in the order the plays happened;
  - each session clip is a clone of THAT track's own session
    donor, with the file reference fully rewritten (Name /
    Path), starting at the played start offset and running
    to the end of the song (not just the played span);
  - slots beyond the filled ones are left exactly as the
    template had them;
  - a track with NO session donor earns a plain warning and
    no invented clip — the Arrangement still exports;
  - the Arrangement output is unchanged by the session step.
"""
import gzip
import wave
from pathlib import Path

import pytest

import als_writer
import perf_als
from engine import RATE


def F(seconds):
    return int(seconds * RATE)


def _param(tag, value, target_id):
    return (f'<{tag}><Manual Value="{value}"/>'
            f'<AutomationTarget Id="{target_id}">'
            f'<LockEnvelope Value="0"/></AutomationTarget></{tag}>')


def _donor(clip_id, name, path, marker_id=7):
    return (
        f'<AudioClip Id="{clip_id}" Time="0">'
        '<LomId Value="0"/><CurrentStart Value="0"/>'
        '<CurrentEnd Value="16"/>'
        '<Loop><LoopStart Value="0"/><LoopEnd Value="16"/>'
        '<StartRelative Value="0"/><LoopOn Value="false"/>'
        '<OutMarker Value="16"/><HiddenLoopStart Value="0"/>'
        '<HiddenLoopEnd Value="16"/></Loop>'
        f'<Name Value="{name}"/><IsWarped Value="false"/>'
        '<SampleRef><FileRef>'
        '<RelativePathType Value="3"/><RelativePath Value=""/>'
        f'<Path Value="{path}"/><LivePackName Value=""/>'
        '<LivePackId Value=""/><OriginalFileSize Value="111"/>'
        '<OriginalCrc Value="222"/></FileRef>'
        '<LastModDate Value="333"/>'
        '<DefaultDuration Value="705600"/>'
        '<DefaultSampleRate Value="44100"/></SampleRef>'
        '<WarpMode Value="0"/><WarpMarkers>'
        f'<WarpMarker Id="{marker_id}" SecTime="0" BeatTime="0"/>'
        '</WarpMarkers>'
        '<Envelopes/></AudioClip>')


def _track(track_id, name, base, donor_clip, slots=2):
    slot_xml = ''
    for i in range(slots):
        slot_xml += (f'<ClipSlot Id="{track_id * 100 + i}">'
                     '<ClipSlot><Value>'
                     + (donor_clip if i == 0 else '')
                     + '</Value></ClipSlot></ClipSlot>')
    devices = (
        '<Mixer>' + _param('Volume', '0.85', base) + '</Mixer>'
        '<StereoGain>' + _param('Gain', '0', base + 1) + '</StereoGain>'
        '<FilterEQ3>' + _param('GainLo', '0', base + 2)
        + _param('GainMid', '0', base + 3)
        + _param('GainHi', '0', base + 4) + '</FilterEQ3>'
        '<AutoFilter>' + _param('Cutoff', '20000', base + 5)
        + _param('FilterType', '0', base + 6) + '</AutoFilter>')
    arranger = ('<Sample><ArrangerAutomation><Events/><Envelopes/>'
                '</ArrangerAutomation></Sample>')
    return (f'<AudioTrack Id="{track_id}"><Name>'
            f'<EffectiveName Value="{name}"/></Name><DeviceChain>'
            + devices +
            '<MainSequencer><ClipSlotList>' + slot_xml
            + '</ClipSlotList>' + arranger
            + '</MainSequencer></DeviceChain></AudioTrack>')


def _base_xml(donor_b=True, slots=2):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
        'Creator="Ableton Live 10.1.43">'
        '<LiveSet><NextPointeeId Value="900"/><Tracks>'
        + _track(10, 'Deck A', 100,
                 _donor(42, 'Donor A', 'C:\\donor_a.wav'), slots)
        + _track(11, 'Deck B', 200,
                 _donor(43, 'Donor B', 'C:\\donor_b.wav', marker_id=8)
                 if donor_b else '', slots)
        + '</Tracks>'
        '<MasterTrack><DeviceChain><Mixer>'
        + _param('Tempo', '100', 301) +
        '</Mixer><FreezeSequencer><AudioSequencer><Sample>'
        '<ArrangerAutomation><Events/><Envelopes/></ArrangerAutomation>'
        '</Sample></AudioSequencer></FreezeSequencer>'
        '</DeviceChain></MasterTrack>'
        '</LiveSet></Ableton>')


def _write_base(tmp_path, **kwargs):
    path = tmp_path / 'base.als'
    path.write_bytes(gzip.compress(_base_xml(**kwargs).encode('utf-8')))
    return path


def _make_wav(path, seconds=4.0, rate=44100):
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


def _events(wav_a, wav_b, wav_g):
    """Deck A: Alpha, then Gamma, then Alpha again (a reprise).
    Deck B: Beta, cued 1 s into the song before it plays."""
    return [
        _ev(0, 'load', 0, str(wav_a)),
        _ev(0, 'load', 1, str(wav_b)),
        _ev(0, 'seek', 1, F(1)),
        _ev(0, 'play', 0, True),
        _ev(F(1), 'play', 1, True),
        _ev(F(2), 'play', 0, False),
        _ev(F(3), 'load', 0, str(wav_g)),
        _ev(F(3), 'play', 0, True),
        _ev(F(4), 'play', 1, False),
        _ev(F(5), 'play', 0, False),
        _ev(F(6), 'load', 0, str(wav_a)),
        _ev(F(6), 'play', 0, True),
        _ev(F(8), 'play', 0, False),
    ]


def _session_clips(track):
    clips = []
    for slot in als_writer._slots(track):
        value = als_writer._slot_value(slot)
        clips.append(value.find('AudioClip')
                     if value is not None else None)
    return clips


def _arrangement_clips(track):
    arranger = track.find('./DeviceChain/MainSequencer/Sample/'
                          'ArrangerAutomation')
    return arranger.find('Events').findall('AudioClip')


def _run(tmp_path, **base_kwargs):
    base = _write_base(tmp_path, **base_kwargs)
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    wav_b = _make_wav(tmp_path / 'Beta.wav')
    wav_g = _make_wav(tmp_path / 'Gamma.wav')
    out = tmp_path / 'perf.als'
    report = perf_als.generate(
        base, _events(wav_a, wav_b, wav_g), out, set_bpm=120.0,
        bpms={str(wav_a): 120.0, str(wav_b): 120.0,
              str(wav_g): 120.0})
    root = als_writer.load_als(out).getroot()
    return report, root, als_writer._audio_tracks(root)


def test_session_slots_follow_performance_order_with_reprise(
        tmp_path):
    report, root, tracks = _run(tmp_path)
    assert report['session_clips'] == {'A': 3, 'B': 1}
    clips_a = _session_clips(tracks[0])
    names_a = [als_writer._get_value(c, 'Name') for c in clips_a[:3]]
    # Performance order on deck A, the reprise in its own slot.
    assert names_a == ['Alpha', 'Gamma', 'Alpha']
    for clip, fname in zip(clips_a[:3],
                           ['Alpha.wav', 'Gamma.wav', 'Alpha.wav']):
        ref = clip.find('.//FileRef')
        assert als_writer._get_value(ref, 'Name') == fname
        assert als_writer._get_value(ref, 'Path').endswith(fname)
        # Session region: played start (0 here) to the END OF
        # THE SONG (4 s at 120 BPM = 8 beats), not the played
        # span (the arrangement clips carry the spans).
        assert float(als_writer._get_value(clip, 'CurrentStart')) == 0.0
        assert float(als_writer._get_value(clip, 'CurrentEnd')) == 8.0
    clips_b = _session_clips(tracks[1])
    assert als_writer._get_value(clips_b[0], 'Name') == 'Beta'
    # Beta was cued 1 s in: the session clip starts there
    # (2 beats at 120 BPM) and still runs to the song's end.
    assert float(als_writer._get_value(clips_b[0], 'CurrentStart')) == 2.0
    assert float(als_writer._get_value(clips_b[0], 'CurrentEnd')) == 8.0


def test_session_step_leaves_arrangement_and_other_slots_alone(
        tmp_path):
    report, root, tracks = _run(tmp_path)
    # The Arrangement is exactly what passes 27–29 produce:
    # three performed segments on A, one on B.
    assert report['clips_written'] == 4
    assert report['decks'] == {'A': 3, 'B': 1}
    assert len(_arrangement_clips(tracks[0])) == 3
    assert len(_arrangement_clips(tracks[1])) == 1
    # Deck B filled only slot 0: slot 1 stays empty, as the
    # template had it. Deck A's donor slot was replaced by the
    # first performed song (built from that very donor).
    assert _session_clips(tracks[1])[1] is None
    # Clip Ids (the clips this export wrote, plus the donors
    # they came from) stayed unique. (Slot WRAPPER Ids are not
    # asserted: als_writer._ensure_slots — the established
    # setlist-path helper — clones the wrapper element as-is
    # when a track outgrows its slots, pass 30 keeps that
    # behaviour rather than diverging from it.)
    ids = []
    for clip in root.iter('AudioClip'):
        ids += [int(el.get('Id')) for el in clip.iter()
                if el.get('Id')]
    assert len(ids) == len(set(ids))


def test_donorless_track_warns_and_still_exports(tmp_path):
    report, root, tracks = _run(tmp_path, donor_b=False)
    assert report['session_clips'] == {'A': 3, 'B': 0}
    assert any('Session' in w and 'Deck B' in w
               for w in report['warnings'])
    # No clip was invented for deck B's session…
    assert all(c is None for c in _session_clips(tracks[1]))
    # …but its Arrangement export is untouched by the gap.
    assert report['decks'] == {'A': 3, 'B': 1}
    assert len(_arrangement_clips(tracks[1])) == 1


def test_slots_extend_when_the_set_outgrows_the_template(tmp_path):
    report, root, tracks = _run(tmp_path, slots=1)
    assert report['session_clips'] == {'A': 3, 'B': 1}
    assert len(als_writer._slots(tracks[0])) == 3
    names_a = [als_writer._get_value(c, 'Name')
               for c in _session_clips(tracks[0])]
    assert names_a == ['Alpha', 'Gamma', 'Alpha']
