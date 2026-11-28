"""Pass 28 (user verdict 2026-11-28: 「オートメーションは反映
されてない」): the automation audit.

Stage isolation, from the user's REAL pass-27 export
(mixcraft-performance-75ef3791.als): the file DOES contain the
performance's envelopes — <Envelope> elements under
Sample/ArrangerAutomation/Envelopes, PointeeIds resolving to
the template's real AutomationTarget ids, with his EQ sweeps,
filter sweep, crossfader and tempo moves all present. The
journal -> replay -> write pipeline carried every knob event.
What Live could not use was the VALUES: they were written in
the wrong domains.

  * Utility Gain / EQ Three bands store AMPLITUDE (linear),
    but the writer converted through a dB helper: neutral 1.0
    became 0.0 (amplitude zero = the band/deck silenced) and a
    x0.5 EQ cut became -6.02 (an invalid negative amplitude).
  * AutoFilter Cutoff stores Hz (20..20000), but the writer
    wrote the raw 0..1 knob — clamped to the bottom of the
    range. FilterType was never addressed at all.
  * The EQ values in his screenshot (GainLow -inf etc.) were
    his template's own saved Manuals, not export output.

These tests pin the fixed domains against a fixture in the
REAL template shape (devices nested under
DeviceChain/DeviceChain/Devices, Manuals copied from his
template), plus three pass-28 regressions: a same-start false
start replaced by the real take, pre-play knob settings
surviving to beat 0, and the journal copy saved beside the
export.
"""
import gzip
import json
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


# Manuals copied from the user's real template (the values his
# 2026-11-26 hardware-test session left saved): EQ3 in
# amplitude — 0.000316… displays as -inf, 0.00211… as -53.5 dB,
# 0.0706… as -23 dB. Cutoff in Hz, FilterType 1 = HP.
_MANUALS = dict(volume='1.99526238', gain='1.17191911',
                lo='0.0003162277571', mid='0.002119144192',
                hi='0.0706750825', cutoff='50.0445747', ftype='1')


def _track(track_id, name, base, donor=False):
    slot = (f'<ClipSlot Id="{track_id + 40}"><ClipSlot><Value>'
            + (DONOR_CLIP if donor else '')
            + '</Value></ClipSlot></ClipSlot>')
    devices = (
        '<DeviceChain><Devices>'
        '<StereoGain>' + _param('Gain', _MANUALS['gain'], base + 1)
        + '</StereoGain>'
        '<FilterEQ3>' + _param('GainLo', _MANUALS['lo'], base + 2)
        + _param('GainMid', _MANUALS['mid'], base + 3)
        + _param('GainHi', _MANUALS['hi'], base + 4) + '</FilterEQ3>'
        '<AutoFilter>' + _param('Cutoff', _MANUALS['cutoff'], base + 5)
        + _param('FilterType', _MANUALS['ftype'], base + 6)
        + '</AutoFilter></Devices></DeviceChain>')
    arranger = ('<Sample><ArrangerAutomation><Events/><Envelopes/>'
                '</ArrangerAutomation></Sample>')
    return (f'<AudioTrack Id="{track_id}"><Name>'
            f'<EffectiveName Value="{name}"/></Name><DeviceChain>'
            '<Mixer>' + _param('Volume', _MANUALS['volume'], base)
            + '</Mixer>'
            '<MainSequencer><ClipSlotList>' + slot
            + '</ClipSlotList>' + arranger + '</MainSequencer>'
            + devices + '</DeviceChain></AudioTrack>')


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


def _base_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
        'Creator="Ableton Live 10.1.43">'
        '<LiveSet><NextPointeeId Value="9000"/><Tracks>'
        + _track(10, 'Deck A', 100, donor=True)
        + _track(11, 'Deck B', 200)
        + '</Tracks>'
        '<MasterTrack><DeviceChain><Mixer>'
        + _param('Tempo', '121.9716', 301) +
        '</Mixer><MainSequencer><AudioSequencer><Sample>'
        '<ArrangerAutomation><Events/><Envelopes/></ArrangerAutomation>'
        '</Sample></AudioSequencer></MainSequencer>'
        '</DeviceChain></MasterTrack>'
        '</LiveSet></Ableton>')


def _write_base(tmp_path):
    path = tmp_path / 'base.als'
    path.write_bytes(gzip.compress(_base_xml().encode('utf-8')))
    return path


def _make_wav(path, seconds=60.0, rate=44100):
    frames = int(seconds * rate)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b'\x00\x00\x00\x00' * frames)
    return str(path)


def ev(frame, action, deck=None, value=None):
    return dict(frame=frame, sample_rate=RATE, action=action,
                deck=deck, value=value)


def _journal(wav_a, wav_b):
    """A performance with moves in every domain — including
    knob settings made BEFORE the first play, and deck B's
    false start from its cue (1.302 beats in) followed by the
    real take from the same cue."""
    cue = int(1.302 * RATE * 60 / 120)   # frames at 120 BPM
    return [
        ev(0, 'load', 0, wav_a),
        ev(100, 'load', 1, wav_b),
        ev(200, 'gain', 0, 1.5),          # before any play
        ev(300, 'eq_low', 0, 0.25),       # UI 0.25 -> band x0.5
        ev(F(1), 'play', 0, True),
        ev(F(5), 'filter', 0, 0.2),       # LP side
        ev(F(9), 'filter', 0, 0.8),       # HP side (crossing)
        ev(F(10), 'cross', None, 0.5),
        ev(F(12), 'set_bpm', None, 126.0),
        ev(F(13), 'seek', 1, cue),
        ev(F(14), 'play', 1, True),       # false start…
        ev(F(14.6), 'play', 1, False),    # …stopped after 0.6 s
        ev(F(20), 'seek', 1, cue),
        ev(F(20.1), 'play', 1, True),     # the real take
        ev(F(30), 'play', 0, False),
        ev(F(40), 'play', 1, False),
    ]


def _envelopes_by_id(track):
    arr = track.find('./DeviceChain/MainSequencer/Sample/'
                     'ArrangerAutomation')
    out = {}
    for env in arr.find('Envelopes'):
        pid = int(env.find('./EnvelopeTarget/PointeeId')
                  .get('Value'))
        out[pid] = [(float(e.get('Time')), float(e.get('Value')))
                    for e in env.find('./Automation/Events')]
    return out


def _base_target_ids(base_path):
    root = als_writer.load_als(str(base_path)).getroot()
    ids = set()
    for el in root.iter():
        tgt = el.find('AutomationTarget')
        if tgt is not None:
            ids.add(int(tgt.get('Id')))
    return ids


def _generate(tmp_path):
    base = _write_base(tmp_path)
    wav_a = _make_wav(tmp_path / 'a.wav')
    wav_b = _make_wav(tmp_path / 'b.wav')
    events = _journal(wav_a, wav_b)
    out = tmp_path / 'set.als'
    report = perf_als.generate(str(base), events, str(out),
                               set_bpm=120.0,
                               bpms={wav_a: 120.0, wav_b: 120.0})
    root = als_writer.load_als(str(out)).getroot()
    tracks = als_writer._audio_tracks(root)
    return base, out, report, tracks


def test_every_envelope_target_resolves_against_the_real_shape(
        tmp_path):
    base, out, report, tracks = _generate(tmp_path)
    ids = _base_target_ids(base)
    for track in tracks:
        for pid in _envelopes_by_id(track):
            assert pid in ids
    master = root_master = None
    ls = als_writer.load_als(str(out)).getroot().find('LiveSet')
    master = ls.find('MasterTrack')
    arr = master.find('./DeviceChain/MainSequencer/'
                      'AudioSequencer/Sample/ArrangerAutomation')
    for env in arr.find('Envelopes'):
        pid = int(env.find('./EnvelopeTarget/PointeeId')
                  .get('Value'))
        assert pid in ids


def test_gain_and_eq_values_are_amplitudes(tmp_path):
    base, out, report, tracks = _generate(tmp_path)
    env_a = _envelopes_by_id(tracks[0])
    env_b = _envelopes_by_id(tracks[1])
    # Gain A: set to 1.5 BEFORE the first play — the exported
    # envelope must open at 1.5 (amplitude), not 0.0 (the old
    # dB-helper value = silence).
    assert env_a[101][0][1] == 1.5
    # EQ low A: UI 0.25 -> band multiplier 0.5.
    assert env_a[102][0][1] == 0.5
    # Untouched deck B: gain and every EQ band sit at the
    # neutral amplitude 1.0 for the whole set.
    assert all(v == 1.0 for _, v in env_b[201])
    for pid in (202, 203, 204):
        assert all(v == 1.0 for _, v in env_b[pid])
    # No envelope anywhere carries a negative amplitude.
    for env in (env_a, env_b):
        for pts in env.values():
            assert all(v >= 0.0 for _, v in pts)


def test_filter_values_are_hz_and_type_follows_the_crossing(
        tmp_path):
    base, out, report, tracks = _generate(tmp_path)
    env_a = _envelopes_by_id(tracks[0])
    env_b = _envelopes_by_id(tracks[1])
    cutoff = env_a[105]
    values = [v for _, v in cutoff]
    assert all(20.0 <= v <= 20000.0 for v in values)
    assert any(abs(v - 832.55) < 1.0 for v in values)   # f=0.2 LP
    assert any(abs(v - 480.45) < 1.0 for v in values)   # f=0.8 HP
    type_env = env_a[106]
    assert {v for _, v in type_env} == {0.0, 1.0}
    # Deck B's filter was never touched: no Cutoff envelope at
    # all — the template's own filter state survives untouched.
    assert 205 not in env_b and 206 not in env_b


def test_single_sided_filter_sets_the_output_type(tmp_path):
    base = _write_base(tmp_path)
    wav_a = _make_wav(tmp_path / 'a.wav')
    wav_b = _make_wav(tmp_path / 'b.wav')
    events = [ev(0, 'load', 0, wav_a), ev(100, 'load', 1, wav_b),
              ev(F(1), 'play', 0, True), ev(F(3), 'filter', 0, 0.2),
              ev(F(8), 'play', 0, False)]
    out = tmp_path / 'lp.als'
    report = perf_als.generate(str(base), events, str(out),
                               set_bpm=120.0,
                               bpms={wav_a: 120.0, wav_b: 120.0})
    root = als_writer.load_als(str(out)).getroot()
    track = als_writer._audio_tracks(root)[0]
    filt = track.find('./DeviceChain/DeviceChain/Devices/'
                      'AutoFilter/FilterType/Manual')
    # The template ships HP (Manual 1); an LP-only performance
    # exports with the type set to LP and no type envelope.
    assert filt.get('Value') == '0'
    assert 106 not in _envelopes_by_id(track)
    assert any('LP' in w for w in report['warnings'])


def test_same_start_false_start_is_replaced_by_the_real_take(
        tmp_path):
    base, out, report, tracks = _generate(tmp_path)
    arr = tracks[1].find('./DeviceChain/MainSequencer/Sample/'
                         'ArrangerAutomation/Events')
    clips = [c for c in arr if c.tag == 'AudioClip']
    # The real file had TWO clips at offset 1.302 (a 1.235-beat
    # sliver + the long take). Same start: the later take
    # replaces the earlier fragment — exactly one clip remains.
    assert len(clips) == 1
    clip = clips[0]
    start = float(clip.find('CurrentStart').get('Value'))
    end = float(clip.find('CurrentEnd').get('Value'))
    assert abs(start - 1.302) < 0.01
    assert end > 40.0        # the ~19.9 s take, not the sliver


def test_export_saves_a_journal_copy_beside_the_als(tmp_path):
    base, out, report, tracks = _generate(tmp_path)
    copy_path = report['events_copy']
    assert copy_path is not None
    copy_file = Path(copy_path)
    assert copy_file.parent == out.parent
    assert copy_file.name == 'set-events.json'
    data = json.loads(copy_file.read_text(encoding='utf-8'))
    assert data['schema'] == 1
    assert len(data['events']) == len(_journal('a', 'b'))
    actions = [e['action'] for e in data['events']]
    assert 'filter' in actions and 'set_bpm' in actions
