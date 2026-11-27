"""perf_als: performance-log injection into a base .als.

The base fixture is a synthetic .als in the documented shape:
two AudioTracks whose DeviceChain carries a Mixer (Volume), a
StereoGain (Utility), a FilterEQ3 (EQ Three) and an AutoFilter —
each parameter with its AutomationTarget Id — plus a donor
Session clip to clone and a MasterTrack Tempo with an
AutomationTarget. The writer must place the performed segments
as Arrangement clips and write the envelopes against the RIGHT
PointeeIds; a parameter missing from the template is skipped
with a warning, never invented. Nothing here proves real Live
opens the output (real-machine check, see MANUAL.md).
"""
import gzip
import json
import wave
import xml.etree.ElementTree as ET
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
    arranger = ('<ClipTimeable><ArrangerAutomation><Events/><Envelopes/>'
                '</ArrangerAutomation></ClipTimeable>' if donor else
                '<ClipTimeable/>')
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
    '<Path Value="C:\\donor.wav"/><LivePackName Value=""/>'
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
        '</Mixer><MainSequencer><ClipTimeable/></MainSequencer>'
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


def _events(wav_a, wav_b):
    def ev(frame, action, deck=None, value=None):
        return dict(frame=frame, sample_rate=RATE, action=action,
                    deck=deck, value=value)
    return [ev(0, 'load', 0, str(wav_a)), ev(0, 'load', 1, str(wav_b)),
            ev(0, 'play', 0, True),
            ev(F(1), 'cross', None, 1.0),
            ev(F(1), 'play', 1, True),
            ev(F(2), 'play', 0, False), ev(F(2), 'play', 1, False)]


def _arrangement(track):
    return track.find('./DeviceChain/MainSequencer/ClipTimeable/'
                      'ArrangerAutomation')


def _envelope_for(track, pointee_id):
    arranger = _arrangement(track)
    for envelope in arranger.find('Envelopes'):
        pointee = envelope.find('./EnvelopeTarget/PointeeId')
        if pointee is not None and pointee.get('Value') == str(pointee_id):
            return [(float(e.get('Time')), float(e.get('Value')))
                    for e in envelope.find('./Automation/Events')]
    return None


def test_generate_places_clips_and_envelopes(tmp_path):
    base = _write_base(tmp_path)
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    wav_b = _make_wav(tmp_path / 'Beta.wav')
    out = tmp_path / 'perf.als'
    report = perf_als.generate(base, _events(wav_a, wav_b), out,
                               set_bpm=120.0,
                               bpms={str(wav_a): 120.0, str(wav_b): 120.0})
    assert report['clips_written'] == 2
    assert report['donor'] == 'clone'
    assert report['tempo_set'] is True

    root = als_writer.load_als(out).getroot()
    tracks = als_writer._audio_tracks(root)
    clips_a = _arrangement(tracks[0]).find('Events').findall('AudioClip')
    clips_b = _arrangement(tracks[1]).find('Events').findall('AudioClip')
    assert len(clips_a) == 1 and len(clips_b) == 1
    # Deck A played 0-2 s from the top; Deck B 1-2 s from the top.
    assert float(clips_a[0].get('Time')) == 0.0
    assert float(als_writer._get_value(clips_a[0], 'CurrentStart')) == 0.0
    assert float(als_writer._get_value(clips_a[0], 'CurrentEnd')) == 4.0
    assert float(clips_b[0].get('Time')) == 2.0
    assert float(als_writer._get_value(clips_b[0], 'CurrentEnd')) == 2.0
    assert als_writer._get_value(clips_a[0], 'Name') == 'Alpha'

    # Volume A (PointeeId 100): the crossfader is baked in —
    # centre at 0 s (0.8 x cos(pi/4)), ~0 once cross hits 1.0.
    volume_a = _envelope_for(tracks[0], 100)
    assert volume_a[0] == (0.0, pytest.approx(0.8 * 0.70710678))
    assert volume_a[1][0] == 2.0
    assert volume_a[1][1] == pytest.approx(0.0, abs=1e-9)
    # Neutral EQ / GAIN are flat 0 dB lines on their own targets.
    assert all(v == 0.0 for _, v in _envelope_for(tracks[0], 103))
    assert all(v == 0.0 for _, v in _envelope_for(tracks[1], 201))
    # Tempo envelope on the master track, and Manual updated.
    master = root.find('./LiveSet/MasterTrack')
    tempo_env = _envelope_for(master, 301)
    assert tempo_env == [(0.0, 120.0), (4.0, 120.0)]
    tempo = master.find('.//Tempo/Manual')
    assert tempo.get('Value') == '120'
    # Ids stayed unique and NextPointeeId advanced past them.
    ids = [int(el.get('Id')) for el in root.iter() if el.get('Id')]
    assert len(ids) == len(set(ids))


def test_missing_device_skips_its_envelopes_with_warnings(tmp_path):
    base = _write_base(tmp_path, eq_b=False)
    wav_a = _make_wav(tmp_path / 'Alpha.wav')
    wav_b = _make_wav(tmp_path / 'Beta.wav')
    out = tmp_path / 'perf.als'
    report = perf_als.generate(base, _events(wav_a, wav_b), out,
                               set_bpm=120.0,
                               bpms={str(wav_a): 120.0, str(wav_b): 120.0})
    assert 'eq_low_B' not in report['envelopes_written']
    assert 'volume_B' in report['envelopes_written']
    assert any('eq_low' in w and 'B' in w for w in report['warnings'])


def test_cli_refuses_to_overwrite_base(tmp_path):
    base = _write_base(tmp_path)
    events_path = tmp_path / 'events.json'
    events_path.write_text(json.dumps({'schema': 1, 'events': []}),
                           encoding='utf-8')
    assert perf_als.main([str(base), str(events_path), str(base)]) == 2
