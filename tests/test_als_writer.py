"""als_writer: clip-plan injection into a base .als (stdlib only).

The base fixture is a minimal synthetic .als shaped like a real Live 10
set (gzip XML; AudioTrack/DeviceChain/MainSequencer/ClipSlotList with the
nested ClipSlot/Value structure, a donor AudioClip written in the shape
Live itself writes, MasterTrack tempo, Scenes). The writer must clone the
donor clip rather than invent XML, preserve everything else in the base,
and fall back to its minimal skeleton (with a warning) only when the base
has no clip at all. Nothing here proves real Live opens the output; that
stays a real-machine check (see SETLIST_TO_LIVE.md).
"""
import gzip
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import als_writer
import live_plan

EMPTY_SLOT = (
    '<ClipSlot Id="0"><LomId Value="0"/><ClipSlot><LomId Value="0"/>'
    '<Value/><HasStopButton Value="true"/><NeedRefreeze Value="false"/>'
    '<NeedRecordingOnStart Value="false"/></ClipSlot></ClipSlot>')

DONOR_CLIP = (
    '<AudioClip Id="42" Time="0">'
    '<LomId Value="0"/><LomIdView Value="0"/>'
    '<CurrentStart Value="0"/><CurrentEnd Value="16"/>'
    '<Loop><LoopStart Value="0"/><LoopEnd Value="16"/>'
    '<StartRelative Value="0"/><LoopOn Value="false"/>'
    '<OutMarker Value="16"/><HiddenLoopStart Value="0"/>'
    '<HiddenLoopEnd Value="16"/></Loop>'
    '<Name Value="Donor Clip"/><Annotation Value=""/><ColorIndex Value="3"/>'
    '<LaunchMode Value="0"/><LaunchQuantisation Value="0"/>'
    '<Legato Value="false"/><Ram Value="false"/><Disabled Value="false"/>'
    '<VelocityAmount Value="0"/><FreezeStart Value="0"/><FreezeEnd Value="0"/>'
    '<IsWarped Value="false"/>'
    '<SampleRef><FileRef>'
    '<RelativePathType Value="3"/><RelativePath Value="Samples/donor.wav"/>'
    '<Path Value="C:\\donor.wav"/><LivePackName Value=""/>'
    '<LivePackId Value=""/><OriginalFileSize Value="111"/>'
    '<OriginalCrc Value="222"/></FileRef>'
    '<LastModDate Value="333"/>'
    '<SourceContext><SourceContext>'
    '<OriginalFilePath Value="C:\\donor.wav"/></SourceContext></SourceContext>'
    '<SampleUsageHint Value="0"/><DefaultDuration Value="705600"/>'
    '<DefaultSampleRate Value="44100"/></SampleRef>'
    '<Onsets/><WarpMode Value="0"/>'
    '<WarpMarkers><WarpMarker Id="7" SecTime="0" BeatTime="0"/>'
    '<WarpMarker Id="8" SecTime="16" BeatTime="16"/></WarpMarkers>'
    '<SavedPlayingOffset Value="0"/><Transposition Value="0"/><Envelopes/>'
    '</AudioClip>')

SCENE = (
    '<Scene Id="60"><Name><EffectiveName Value="Scene"/>'
    '<UserName Value=""/><Annotation Value=""/>'
    '<MemorizedFirstClipName Value=""/></Name>'
    '<ColorIndex Value="-1"/><LomId Value="0"/><ClipSlotList>'
    + '<SceneClipSlot Id="0"><LomId Value="0"/><SceneClipSlot>'
      '<LomId Value="0"/><Value/></SceneClipSlot></SceneClipSlot>' * 3
    + '</ClipSlotList></Scene>')


def _track(track_id, name, slots):
    return (
        f'<AudioTrack Id="{track_id}"><Name>'
        f'<EffectiveName Value="{name}"/><UserName Value="{name}"/>'
        f'<Annotation Value="KEEP-{name}"/></Name>'
        '<DeviceChain><MainSequencer><ClipSlotList>'
        + "".join(slots) +
        '</ClipSlotList><Sample><ArrangerAutomation><Events/>'
        '</ArrangerAutomation></Sample></MainSequencer>'
        '<DeviceChain><Devices/></DeviceChain></DeviceChain></AudioTrack>')


def _base_xml(with_donor=True, audio_tracks=2):
    donor_slot = EMPTY_SLOT.replace("<Value/>", f"<Value>{DONOR_CLIP}</Value>")
    deck_a = _track(10, "Deck A",
                    [EMPTY_SLOT, donor_slot] if with_donor
                    else [EMPTY_SLOT, EMPTY_SLOT])
    deck_b = _track(11, "Deck B", [EMPTY_SLOT, EMPTY_SLOT])
    tracks = deck_a + deck_b if audio_tracks == 2 else deck_a
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
        'SchemaChangeCount="3" Creator="Ableton Live 10.1.43" Revision="">'
        '<LiveSet><NextPointeeId Value="900"/><Tracks>'
        + tracks +
        '<MidiTrack Id="12"><Name><EffectiveName Value="KEEP-MIDI"/>'
        '</Name></MidiTrack></Tracks>'
        '<MasterTrack><DeviceChain><DeviceChain><Mixer>'
        '<Tempo><Manual Value="120"/></Tempo></Mixer></DeviceChain>'
        '</DeviceChain></MasterTrack>'
        '<Scenes>' + SCENE * 2 + '</Scenes>'
        '<GroovePool><Grooves/></GroovePool>'
        '</LiveSet></Ableton>')


def _write_base(tmp_path, **kwargs):
    path = tmp_path / "base.als"
    path.write_bytes(gzip.compress(_base_xml(**kwargs).encode("utf-8")))
    return path


def _make_wav(path, seconds=2.0, rate=44100):
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00\x00\x00" * frames)
    return path


def _plan(clips, set_bpm=122.0):
    return {"format": "mixcraft-live-plan", "version": 1,
            "set_bpm": set_bpm, "clips": clips,
            "unmatched": [], "warnings": []}


def _clip(position, deck, slot, file, name, bpm=120.0, duration=2.0,
          start_s=0.0, loop=None):
    return {"position": position, "deck": deck, "slot": slot,
            "title": name, "artist": "Tester", "name": name,
            "file": str(file), "file_via": "folder", "bpm": bpm,
            "key": "8A", "duration_seconds": duration,
            "start_offset_seconds": start_s,
            "start_offset_beats": (start_s * bpm / 60.0) if bpm else None,
            "loop": loop, "locators": []}


def _deck_clips(root, deck_index):
    tracks = root.find("./LiveSet/Tracks")
    audio = [t for t in tracks if t.tag == "AudioTrack"]
    slot_list = audio[deck_index].find("./DeviceChain/MainSequencer/ClipSlotList")
    out = []
    for slot in slot_list:
        value = slot.find("./ClipSlot/Value")
        out.append(value.find("AudioClip") if value is not None else None)
    return out


def _value(el, tag):
    child = el.find(tag)
    return child.get("Value") if child is not None else None


# ---------------------------------------------------------------------------
# wav_info
# ---------------------------------------------------------------------------

def test_wav_info(tmp_path):
    wav = _make_wav(tmp_path / "a.wav", seconds=2.0)
    assert als_writer.wav_info(wav) == (88200, 44100)
    not_wav = tmp_path / "b.mp3"
    not_wav.write_bytes(b"ID3" + b"\x00" * 64)
    assert als_writer.wav_info(not_wav) is None


# ---------------------------------------------------------------------------
# Round trip: inject -> reopen -> parse
# ---------------------------------------------------------------------------

def test_round_trip_injects_clips_in_order(tmp_path):
    base = _write_base(tmp_path)
    wav1 = _make_wav(tmp_path / "one.wav")
    wav2 = _make_wav(tmp_path / "two.wav")
    loop = {"start_seconds": 0.5, "end_seconds": 1.5,
            "start_beats": 1.0, "end_beats": 3.0}
    plan = _plan([
        _clip(1, "A", 0, wav1, "One - Tester", start_s=0.5, loop=loop),
        _clip(2, "B", 0, wav2, "Two - Tester"),
        _clip(3, "A", 1, wav2, "Three - Tester"),
        _clip(4, "B", 1, wav1, "Four - Tester"),
        _clip(5, "A", 2, wav1, "Five - Tester"),  # needs a 3rd slot + scene
    ])
    out = tmp_path / "out.als"
    report = als_writer.generate(base, plan, out)
    assert report["clips_written"] == 5
    assert report["decks"] == {"A": 3, "B": 2}
    assert report["donor"] == "clone" and report["tempo_set"] is True
    assert report["slots_extended"].get("A") == 1
    assert report["scenes_extended"] == 1

    root = als_writer.load_als(out).getroot()
    deck_a = _deck_clips(root, 0)
    deck_b = _deck_clips(root, 1)
    assert [_value(c, "Name") for c in deck_a] == [
        "One - Tester", "Three - Tester", "Five - Tester"]
    assert [_value(c, "Name") for c in deck_b] == ["Two - Tester", "Four - Tester"]

    first = deck_a[0]
    file_ref = first.find(".//FileRef")
    assert _value(file_ref, "Path") == str(wav1.resolve())
    assert _value(file_ref, "OriginalFileSize") == str(wav1.stat().st_size)
    assert _value(file_ref, "OriginalCrc") == "0"
    sample_ref = first.find("SampleRef")
    assert _value(sample_ref, "DefaultDuration") == "88200"
    assert _value(sample_ref, "DefaultSampleRate") == "44100"
    assert _value(first, "IsWarped") == "true"
    markers = first.find("WarpMarkers")
    pairs = [(m.get("SecTime"), m.get("BeatTime")) for m in markers]
    assert pairs == [("0", "0"), ("2", "4")]
    assert _value(first, "CurrentStart") == "1"
    assert _value(first, "CurrentEnd") == "4"
    loop_el = first.find("Loop")
    assert _value(loop_el, "LoopOn") == "true"
    assert _value(loop_el, "LoopStart") == "1"
    assert _value(loop_el, "LoopEnd") == "3"

    plain = deck_b[0]
    assert _value(plain.find("Loop"), "LoopOn") == "false"
    assert _value(plain, "CurrentStart") == "0"

    # Set tempo written from the plan.
    tempo = root.find(".//MasterTrack//Tempo/Manual")
    assert tempo.get("Value") == "122"

    # Non-clip content of the base survives untouched.
    names = [e.get("Value") for e in root.iter("EffectiveName")]
    assert "KEEP-MIDI" in names and "Deck A" in names
    assert root.find(".//GroovePool") is not None
    assert root.find(".//MidiTrack") is not None

    # Ids: clips got fresh unique ids, NextPointeeId exceeds them all.
    clip_ids = [int(c.get("Id")) for c in root.iter("AudioClip")]
    assert len(clip_ids) == len(set(clip_ids)) == 5
    assert 42 not in clip_ids
    pointee = int(root.find(".//NextPointeeId").get("Value"))
    assert pointee > max(clip_ids)
    scenes = root.find(".//Scenes")
    assert len([s for s in scenes if s.tag == "Scene"]) == 3


def test_skeleton_fallback_warns(tmp_path):
    base = _write_base(tmp_path, with_donor=False)
    wav = _make_wav(tmp_path / "one.wav")
    plan = _plan([_clip(1, "A", 0, wav, "One - Tester"),
                  _clip(2, "B", 0, wav, "Two - Tester")])
    out = tmp_path / "out.als"
    report = als_writer.generate(base, plan, out)
    assert report["donor"] == "skeleton"
    assert any("スケルトン" in w for w in report["warnings"])
    root = als_writer.load_als(out).getroot()
    assert _value(_deck_clips(root, 0)[0], "Name") == "One - Tester"
    assert _value(_deck_clips(root, 1)[0], "Name") == "Two - Tester"
    file_ref = _deck_clips(root, 0)[0].find(".//FileRef")
    assert _value(file_ref, "Path") == str(wav.resolve())


def test_missing_bpm_keeps_donor_timing_but_still_places_clip(tmp_path):
    base = _write_base(tmp_path)
    wav = _make_wav(tmp_path / "one.wav")
    plan = _plan([_clip(1, "A", 0, wav, "One - Tester", bpm=None)])
    plan["clips"][0]["start_offset_beats"] = None
    out = tmp_path / "out.als"
    report = als_writer.generate(base, plan, out)
    assert report["clips_written"] == 1
    assert any("Warp" in w for w in report["warnings"])
    root = als_writer.load_als(out).getroot()
    clip = _deck_clips(root, 0)[0]
    assert _value(clip, "Name") == "One - Tester"
    assert _value(clip, "CurrentEnd") == "16"  # donor value untouched


def test_single_audio_track_is_an_error(tmp_path):
    base = _write_base(tmp_path, audio_tracks=1)
    with pytest.raises(als_writer.AlsError):
        als_writer.generate(base, _plan([]), tmp_path / "out.als")


def test_cli_missing_base_is_a_clear_failure(tmp_path):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text('{"format": "mixcraft-live-plan", "clips": []}',
                         encoding="utf-8")
    rc = als_writer.main([str(tmp_path / "nope.als"), str(plan_path),
                          str(tmp_path / "out.als")])
    assert rc == 2


def test_cli_refuses_to_overwrite_base(tmp_path):
    base = _write_base(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text('{"format": "mixcraft-live-plan", "clips": []}',
                         encoding="utf-8")
    assert als_writer.main([str(base), str(plan_path), str(base)]) == 2


def test_end_to_end_from_setlist(tmp_path):
    folder = tmp_path / "tracks"
    folder.mkdir()
    wav_a = _make_wav(folder / "Alpha - First.wav")
    wav_b = _make_wav(folder / "Beta - Second.wav")
    setlist = {"format": "mixcraft-setlist", "version": 1, "set_bpm": 122.0,
               "tracks": [
                   {"title": "First", "artist": "Alpha", "bpm": 120.0,
                    "duration_seconds": 2},
                   {"title": "Second", "artist": "Beta", "bpm": 120.0,
                    "duration_seconds": 2}]}
    plan = live_plan.build_plan(setlist, tracks_folder=folder)
    assert len(plan["clips"]) == 2 and not plan["unmatched"]
    base = _write_base(tmp_path)
    out = tmp_path / "gig.als"
    report = als_writer.generate(base, plan, out)
    assert report["clips_written"] == 2
    root = als_writer.load_als(out).getroot()
    assert _value(_deck_clips(root, 0)[0], "Name") == "First - Alpha"
    assert _value(_deck_clips(root, 1)[0], "Name") == "Second - Beta"
    assert _value(_deck_clips(root, 0)[0].find(".//FileRef"), "Path") == \
        str(wav_a.resolve())
    assert _value(_deck_clips(root, 1)[0].find(".//FileRef"), "Path") == \
        str(wav_b.resolve())
