"""pass 34: the export neutralises the base template's leftover
static (Manual) device values on the deck tracks.

The user's real template was saved mid-test (2026-11-26 Remote
Script session) with deck A's EQ Three statics at GainLo
0.000316 (-inf) / GainMid 0.00212 (-53.5 dB) / GainHi 0.0707
(-23.0 dB) and its Mixer Volume static at 1.995 (+6 dB). The
performance envelopes only cover the played regions, so the
Session clips (no envelopes at all) played through that corpse
EQ and deck A sounded dead in Live's Session view while deck B
was fine. The export now resets the deck tracks' statics to
neutral (amplitude 1.0, filter wide open at 20000 Hz LP) and
must not move a single envelope event or clip while doing so.
"""
import gzip
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import als_writer
import perf_als
from engine import RATE


def F(seconds):
    return int(seconds * RATE)


def _param(tag, value, target_id):
    return (f'<{tag}><Manual Value="{value}"/>'
            f'<AutomationTarget Id="{target_id}">'
            f'<LockEnvelope Value="0"/></AutomationTarget></{tag}>')


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


def _track(track_id, name, base, statics, eq=True):
    slot = (f'<ClipSlot Id="{track_id + 40}"><ClipSlot><Value>'
            + DONOR_CLIP + '</Value></ClipSlot></ClipSlot>')
    devices = (
        '<Mixer>' + _param('Volume', statics['volume'], base) + '</Mixer>'
        '<StereoGain>' + _param('Gain', statics['gain'], base + 1)
        + '</StereoGain>'
        + ('<FilterEQ3>' + _param('GainLo', statics['lo'], base + 2)
           + _param('GainMid', statics['mid'], base + 3)
           + _param('GainHi', statics['hi'], base + 4)
           + '</FilterEQ3>' if eq else '')
        + '<AutoFilter>' + _param('Cutoff', statics['cutoff'], base + 5)
        + _param('FilterType', statics['ftype'], base + 6)
        + '</AutoFilter>')
    arranger = ('<Sample><ArrangerAutomation><Events/><Envelopes/>'
                '</ArrangerAutomation></Sample>')
    return (f'<AudioTrack Id="{track_id}"><Name>'
            f'<EffectiveName Value="{name}"/></Name><DeviceChain>'
            + devices
            + '<MainSequencer><ClipSlotList>' + slot
            + '</ClipSlotList>' + arranger
            + '</MainSequencer></DeviceChain></AudioTrack>')


# The user's real deck-A statics (inspected from his exported
# .als) and a different, merely non-neutral set for deck B.
EXTREME_A = {'volume': '1.995', 'gain': '0.5', 'lo': '0.000316',
             'mid': '0.00212', 'hi': '0.0707', 'cutoff': '147',
             'ftype': '1'}
EXTREME_B = {'volume': '0.85', 'gain': '0.25', 'lo': '0.5',
             'mid': '0.6', 'hi': '0.7', 'cutoff': '800',
             'ftype': '1'}
NEUTRAL = {'volume': '1', 'gain': '1', 'lo': '1', 'mid': '1',
           'hi': '1', 'cutoff': '20000', 'ftype': '0'}


def _base_xml(statics_a, statics_b, eq_b=True):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
        'Creator="Ableton Live 10.1.43">'
        '<LiveSet><NextPointeeId Value="900"/><Tracks>'
        + _track(10, 'Deck A', 100, statics_a)
        + _track(11, 'Deck B', 200, statics_b, eq=eq_b)
        + '</Tracks>'
        '<MasterTrack><DeviceChain><Mixer>'
        + _param('Tempo', '100', 301)
        + '</Mixer><FreezeSequencer><AudioSequencer><Sample>'
        '<ArrangerAutomation><Events/><Envelopes/></ArrangerAutomation>'
        '</Sample></AudioSequencer></FreezeSequencer>'
        '</DeviceChain></MasterTrack>'
        '</LiveSet></Ableton>')


def _write_base(tmp_path, name, statics_a, statics_b, **kwargs):
    path = tmp_path / name
    path.write_bytes(gzip.compress(
        _base_xml(statics_a, statics_b, **kwargs).encode('utf-8')))
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


def _manual(track, *path):
    el = track.find('./DeviceChain/' + '/'.join(path) + '/Manual')
    assert el is not None, path
    return float(el.get('Value'))


def _statics(track):
    return {
        'volume': _manual(track, 'Mixer', 'Volume'),
        'gain': _manual(track, 'StereoGain', 'Gain'),
        'lo': _manual(track, 'FilterEQ3', 'GainLo'),
        'mid': _manual(track, 'FilterEQ3', 'GainMid'),
        'hi': _manual(track, 'FilterEQ3', 'GainHi'),
        'cutoff': _manual(track, 'AutoFilter', 'Cutoff'),
        'ftype': _manual(track, 'AutoFilter', 'FilterType'),
    }


def _generate(tmp_path, base, tag):
    wav_a = _make_wav(tmp_path / f'Alpha{tag}.wav')
    wav_b = _make_wav(tmp_path / f'Beta{tag}.wav')
    out = tmp_path / f'perf{tag}.als'
    report = perf_als.generate(base, _events(wav_a, wav_b), out,
                               set_bpm=120.0,
                               bpms={str(wav_a): 120.0,
                                     str(wav_b): 120.0})
    return out, report


def test_extreme_statics_are_neutralised_on_both_decks(tmp_path):
    base = _write_base(tmp_path, 'base.als', EXTREME_A, EXTREME_B)
    out, report = _generate(tmp_path, base, 'x')
    assert report['statics_neutralised'] == {'A': 7, 'B': 7}
    root = als_writer.load_als(out).getroot()
    tracks = als_writer._audio_tracks(root)
    for track in tracks:
        statics = _statics(track)
        assert statics == {'volume': 1.0, 'gain': 1.0, 'lo': 1.0,
                           'mid': 1.0, 'hi': 1.0, 'cutoff': 20000.0,
                           'ftype': 0.0}


def test_neutralising_does_not_move_envelopes_or_clips(tmp_path):
    extreme = _write_base(tmp_path, 'extreme.als', EXTREME_A, EXTREME_B)
    neutral = _write_base(tmp_path, 'neutral.als', NEUTRAL, NEUTRAL)
    out_x, _ = _generate(tmp_path, extreme, 'x')
    out_n, _ = _generate(tmp_path, neutral, 'n')

    def events_of(path):
        root = als_writer.load_als(path).getroot()
        floats = [(e.get('Time'), e.get('Value'))
                  for e in root.iter('FloatEvent')]
        clips = [(c.get('Time'),
                  als_writer._get_value(c, 'CurrentStart'),
                  als_writer._get_value(c, 'CurrentEnd'))
                 for c in root.iter('AudioClip')]
        return floats, clips

    floats_x, clips_x = events_of(out_x)
    floats_n, clips_n = events_of(out_n)
    assert floats_x and floats_x == floats_n
    assert clips_x and clips_x == clips_n


def test_missing_device_is_skipped_silently(tmp_path):
    base = _write_base(tmp_path, 'base.als', EXTREME_A, EXTREME_B,
                       eq_b=False)
    out, report = _generate(tmp_path, base, 'x')
    # Deck B has no FilterEQ3: its 3 EQ params are skipped, the
    # remaining 4 are still reset, and nothing is invented.
    assert report['statics_neutralised'] == {'A': 7, 'B': 4}
    root = als_writer.load_als(out).getroot()
    tracks = als_writer._audio_tracks(root)
    assert tracks[1].find('./DeviceChain/FilterEQ3') is None
    assert _manual(tracks[1], 'Mixer', 'Volume') == 1.0
    assert _manual(tracks[1], 'AutoFilter', 'Cutoff') == 20000.0
