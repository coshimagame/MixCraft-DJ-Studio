"""mixlist: performed .als -> pasted-ready tracklist (stdlib only).

The fixtures are synthetic performed sets (gzip XML) with clips at
known beat positions at a known tempo (120 BPM, so 1 beat = 0.5 s).
Most use the legacy ClipTimeable/ArrangerAutomation/Events placement
(the container pre-pass-27 MixAble exports wrote); the container
section at the bottom rebuilds the same sets in the real Live 10
placement (MainSequencer/Sample/ArrangerAutomation/Events, audited
2026-11-28 against a real-format generated set and the user's real
exported file) and pins the Sample-over-ClipTimeable preference and
the FreezeSequencer exclusion. Nothing here proves a set recorded
and saved by Live 10 itself parses; that stays a real-machine check
(MIXLIST.md).
"""
import gzip

import pytest

import mixlist

EMPTY_SLOT = (
    '<ClipSlot Id="0"><LomId Value="0"/><ClipSlot><LomId Value="0"/>'
    '<Value/><HasStopButton Value="true"/><NeedRefreeze Value="false"/>'
    '<NeedRecordingOnStart Value="false"/>'
    '</ClipSlot></ClipSlot>')


def _clip_xml(clip_id, time, name, file_path, cur_start=0, cur_end=480):
    """An AudioClip in the shape Live itself writes (cf. test_als_writer)."""
    return (
        f'<AudioClip Id="{clip_id}" Time="{time}">'
        '<LomId Value="0"/><LomIdView Value="0"/>'
        f'<CurrentStart Value="{cur_start}"/><CurrentEnd Value="{cur_end}"/>'
        f'<Loop><LoopStart Value="{cur_start}"/><LoopEnd Value="{cur_end}"/>'
        '<StartRelative Value="0"/><LoopOn Value="false"/>'
        f'<OutMarker Value="{cur_end}"/>'
        f'<HiddenLoopStart Value="{cur_start}"/>'
        f'<HiddenLoopEnd Value="{cur_end}"/></Loop>'
        f'<Name Value="{name}"/><Annotation Value=""/><ColorIndex Value="3"/>'
        '<LaunchMode Value="0"/><LaunchQuantisation Value="0"/>'
        '<Legato Value="false"/><Ram Value="false"/><Disabled Value="false"/>'
        '<VelocityAmount Value="0"/><FreezeStart Value="0"/>'
        '<FreezeEnd Value="0"/><IsWarped Value="true"/>'
        '<SampleRef><FileRef>'
        '<RelativePathType Value="3"/><RelativePath Value=""/>'
        f'<Path Value="{file_path}"/><LivePackName Value=""/>'
        '<LivePackId Value=""/><OriginalFileSize Value="111"/>'
        '<OriginalCrc Value="0"/></FileRef>'
        '<LastModDate Value="333"/>'
        '<SourceContext><SourceContext/></SourceContext>'
        '<SampleUsageHint Value="0"/><DefaultDuration Value="705600"/>'
        '<DefaultSampleRate Value="44100"/></SampleRef>'
        '<Onsets/><WarpMode Value="0"/>'
        '<WarpMarkers><WarpMarker Id="7" SecTime="0" BeatTime="0"/></WarpMarkers>'
        '<SavedPlayingOffset Value="0"/><Transposition Value="0"/><Envelopes/>'
        '</AudioClip>')


def _slot(clip_xml):
    if not clip_xml:
        return EMPTY_SLOT
    return EMPTY_SLOT.replace("<Value/>", f"<Value>{clip_xml}</Value>")


def _track(track_id, name, session_clips, arrangement_clips):
    return (
        f'<AudioTrack Id="{track_id}"><Name>'
        f'<EffectiveName Value="{name}"/><UserName Value="{name}"/>'
        f'<Annotation Value=""/></Name>'
        '<DeviceChain><MainSequencer><ClipSlotList>'
        + "".join(_slot(c) for c in session_clips) +
        '</ClipSlotList><ClipTimeable><ArrangerAutomation><Events>'
        + "".join(arrangement_clips) +
        '</Events></ArrangerAutomation></ClipTimeable></MainSequencer>'
        '<DeviceChain><Devices/></DeviceChain></DeviceChain></AudioTrack>')


def _performed_xml(deck_a, deck_b, tempo=120):
    """deck_a / deck_b: dict(session=[...], arrangement=[...])."""
    tracks = (_track(10, "Deck A", deck_a["session"], deck_a["arrangement"])
              + _track(11, "Deck B", deck_b["session"], deck_b["arrangement"]))
    master = ""
    if tempo is not None:
        master = ('<MasterTrack><DeviceChain><DeviceChain><Mixer>'
                  f'<Tempo><Manual Value="{tempo}"/></Tempo>'
                  '</Mixer></DeviceChain></DeviceChain></MasterTrack>')
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
            'SchemaChangeCount="3" Creator="Ableton Live 10.1.43" Revision="">'
            '<LiveSet><NextPointeeId Value="900"/><Tracks>'
            + tracks + '</Tracks>' + master +
            '</LiveSet></Ableton>')


def _write_set(tmp_path, deck_a, deck_b, tempo=120, name="performed.als"):
    path = tmp_path / name
    path.write_bytes(gzip.compress(
        _performed_xml(deck_a, deck_b, tempo).encode("utf-8")))
    return path


def _empty_decks():
    return {"session": [], "arrangement": []}


# ---------------------------------------------------------------------------
# Main scenario: cross-deck order, hot-cue re-fire merge, later return
# (120 BPM -> 1 beat = 0.5 s)
# ---------------------------------------------------------------------------

def _main_set(tmp_path):
    alpha = "C:\\music\\Alpha - First.wav"
    beta = "C:\\music\\Beta - Second.wav"
    gamma = "C:\\music\\Gamma - Third.wav"
    deck_a = {
        "session": [_clip_xml(90, 0, "Session Only - Nobody",
                              "C:\\music\\Nobody - Session.wav")],
        "arrangement": [
            _clip_xml(1, 0, "First - Alpha", alpha, 0, 480),
            _clip_xml(2, 800, "Third - Gamma", gamma, 0, 400),
        ],
    }
    deck_b = {
        "session": [],
        "arrangement": [
            _clip_xml(3, 400, "Second - Beta", beta, 0, 400),
            # Hot-cue re-fire of the song that is already the latest entry:
            # same file, consecutive -> merges into entry 2.
            _clip_xml(4, 440, "Second – Hot Cue B", beta, 32, 400),
            # The first song genuinely returns after Third intervened:
            # same file, but a new entry.
            _clip_xml(5, 1200, "First - Alpha", alpha, 0, 480),
        ],
    }
    return _write_set(tmp_path, deck_a, deck_b)


def test_tracklist_from_performed_set(tmp_path):
    path = _main_set(tmp_path)
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    assert text == (
        "# Mixlist — 2026-11-27 / Total 14:00 / 120 BPM\n"
        "1. 00:00 First — Alpha\n"
        "2. 03:20 Second — Beta\n"
        "3. 06:40 Third — Gamma\n"
        "4. 10:00 First — Alpha\n")
    assert "Session Only" not in text  # Session clips never appear


def test_arrangement_clips_extraction(tmp_path):
    path = _main_set(tmp_path)
    import als_writer
    root = als_writer.load_als(path).getroot()
    clips = mixlist.arrangement_clips(root)
    assert [(c["deck"], c["start_beats"]) for c in clips] == [
        ("A", 0.0), ("B", 400.0), ("B", 440.0), ("A", 800.0), ("B", 1200.0)]
    assert clips[0]["end_beats"] == 480.0
    assert mixlist.set_tempo(root) == 120.0


# ---------------------------------------------------------------------------
# Merge rules
# ---------------------------------------------------------------------------

def test_hot_cue_merge_by_name_without_file(tmp_path):
    deck_a = {"session": [], "arrangement": [
        _clip_xml(1, 0, "Fifth - Eps", "", 0, 400),
        _clip_xml(2, 100, "Fifth – Hot Cue A", "", 16, 400),
    ]}
    path = _write_set(tmp_path, deck_a, _empty_decks())
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    lines = text.splitlines()
    assert lines[1:] == ["1. 00:00 Fifth — Eps"]


def test_same_title_different_songs_do_not_merge(tmp_path):
    deck_a = {"session": [], "arrangement": [
        _clip_xml(1, 0, "Same - One", "C:\\music\\One - Same.wav", 0, 200),
        _clip_xml(2, 100, "Same - Two", "C:\\music\\Two - Same.wav", 0, 200),
    ]}
    path = _write_set(tmp_path, deck_a, _empty_decks())
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    lines = text.splitlines()
    assert lines[1:] == ["1. 00:00 Same — One", "2. 00:50 Same — Two"]


# ---------------------------------------------------------------------------
# Display fallbacks and skips
# ---------------------------------------------------------------------------

def test_display_falls_back_to_file_name(tmp_path):
    deck_b = {"session": [], "arrangement": [
        _clip_xml(1, 0, "", "C:\\music\\Delta - Fourth.wav", 0, 240),
    ]}
    path = _write_set(tmp_path, _empty_decks(), deck_b)
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    assert text.splitlines()[1] == "1. 00:00 Fourth — Delta"


def test_clip_without_name_and_file_is_skipped(tmp_path):
    deck_a = {"session": [], "arrangement": [
        _clip_xml(1, 0, "First - Alpha", "C:\\music\\Alpha - First.wav", 0, 200),
        _clip_xml(2, 100, "", "", 0, 200),
        _clip_xml(3, 200, "Third - Gamma", "C:\\music\\Gamma - Third.wav", 0, 200),
    ]}
    path = _write_set(tmp_path, deck_a, _empty_decks())
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    assert text.splitlines()[1:] == [
        "1. 00:00 First — Alpha", "2. 01:40 Third — Gamma"]


def test_hot_cue_only_song_displays_its_title(tmp_path):
    deck_a = {"session": [], "arrangement": [
        _clip_xml(1, 0, "Sixth – Hot Cue C", "C:\\music\\Zeta - Sixth.wav",
                  64, 400),
    ]}
    path = _write_set(tmp_path, deck_a, _empty_decks())
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    assert text.splitlines()[1] == "1. 00:00 Sixth"


# ---------------------------------------------------------------------------
# Errors and CLI
# ---------------------------------------------------------------------------

def test_missing_tempo_is_an_error(tmp_path):
    deck_a = {"session": [], "arrangement": [
        _clip_xml(1, 0, "First - Alpha", "C:\\music\\Alpha - First.wav")]}
    path = _write_set(tmp_path, deck_a, _empty_decks(), tempo=None)
    with pytest.raises(mixlist.MixlistError):
        mixlist.mixlist_from_als(path, date_str="2026-11-27")


def test_cli_missing_file_returns_2(tmp_path):
    assert mixlist.main([str(tmp_path / "nope.als")]) == 2


def test_cli_stdout_and_out_file(tmp_path, capsys):
    path = _main_set(tmp_path)
    assert mixlist.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "1. 00:00 First — Alpha" in out
    assert "4. 10:00 First — Alpha" in out

    target = tmp_path / "tracklist.txt"
    assert mixlist.main([str(path), "--out", str(target)]) == 0
    written = target.read_text(encoding="utf-8")
    assert written.splitlines()[1] == "1. 00:00 First — Alpha"
    assert written.splitlines()[0].startswith("# Mixlist — ")


# ---------------------------------------------------------------------------
# Container audit (2026-11-28): the real Live 10 placement is
# MainSequencer/Sample/ArrangerAutomation/Events. ClipTimeable is the
# MIDI container that pre-pass-27 MixAble exports used by mistake
# (Live ignores it there); the reader prefers Sample per track and
# falls back to ClipTimeable, and never reads the FreezeSequencer
# mirror.
# ---------------------------------------------------------------------------

def _track_real(track_id, name, session_clips, arrangement_clips,
                legacy_clips=(), freeze_clips=()):
    """A track in the real Live 10 shape: Arrangement under Sample,
    optional legacy clips under ClipTimeable, optional freeze mirror
    under DeviceChain/FreezeSequencer/Sample."""
    xml = (
        f'<AudioTrack Id="{track_id}"><Name>'
        f'<EffectiveName Value="{name}"/><UserName Value="{name}"/>'
        f'<Annotation Value=""/></Name>'
        '<DeviceChain><MainSequencer><ClipSlotList>'
        + "".join(_slot(c) for c in session_clips) +
        '</ClipSlotList><Sample><ArrangerAutomation><Events>'
        + "".join(arrangement_clips) +
        '</Events></ArrangerAutomation></Sample>')
    if legacy_clips:
        xml += ('<ClipTimeable><ArrangerAutomation><Events>'
                + "".join(legacy_clips) +
                '</Events></ArrangerAutomation></ClipTimeable>')
    xml += '</MainSequencer>'
    if freeze_clips:
        xml += ('<FreezeSequencer><Sample><ArrangerAutomation><Events>'
                + "".join(freeze_clips) +
                '</Events></ArrangerAutomation></Sample></FreezeSequencer>')
    xml += '<DeviceChain><Devices/></DeviceChain></DeviceChain></AudioTrack>'
    return xml


def _performed_xml_real(deck_a, deck_b, tempo=120):
    """Deck dicts: session / arrangement (Sample) plus optional
    legacy (ClipTimeable) and freeze (FreezeSequencer mirror)."""
    tracks = (
        _track_real(10, "Deck A", deck_a["session"], deck_a["arrangement"],
                    deck_a.get("legacy", ()), deck_a.get("freeze", ()))
        + _track_real(11, "Deck B", deck_b["session"], deck_b["arrangement"],
                      deck_b.get("legacy", ()), deck_b.get("freeze", ())))
    master = ""
    if tempo is not None:
        master = ('<MasterTrack><DeviceChain><DeviceChain><Mixer>'
                  f'<Tempo><Manual Value="{tempo}"/></Tempo>'
                  '</Mixer></DeviceChain></DeviceChain></MasterTrack>')
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
            'SchemaChangeCount="3" Creator="Ableton Live 10.1.43" Revision="">'
            '<LiveSet><NextPointeeId Value="900"/><Tracks>'
            + tracks + '</Tracks>' + master +
            '</LiveSet></Ableton>')


def _write_set_real(tmp_path, deck_a, deck_b, tempo=120,
                    name="performed_real.als"):
    path = tmp_path / name
    path.write_bytes(gzip.compress(
        _performed_xml_real(deck_a, deck_b, tempo).encode("utf-8")))
    return path


def _main_set_real(tmp_path):
    """The main scenario rebuilt in the real (Sample) container."""
    alpha = "C:\\music\\Alpha - First.wav"
    beta = "C:\\music\\Beta - Second.wav"
    gamma = "C:\\music\\Gamma - Third.wav"
    deck_a = {
        "session": [_clip_xml(90, 0, "Session Only - Nobody",
                              "C:\\music\\Nobody - Session.wav")],
        "arrangement": [
            _clip_xml(1, 0, "First - Alpha", alpha, 0, 480),
            _clip_xml(2, 800, "Third - Gamma", gamma, 0, 400),
        ],
    }
    deck_b = {
        "session": [],
        "arrangement": [
            _clip_xml(3, 400, "Second - Beta", beta, 0, 400),
            _clip_xml(4, 440, "Second – Hot Cue B", beta, 32, 400),
            _clip_xml(5, 1200, "First - Alpha", alpha, 0, 480),
        ],
    }
    return _write_set_real(tmp_path, deck_a, deck_b)


def test_real_sample_container_gives_same_tracklist(tmp_path):
    path = _main_set_real(tmp_path)
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    assert text == (
        "# Mixlist — 2026-11-27 / Total 14:00 / 120 BPM\n"
        "1. 00:00 First — Alpha\n"
        "2. 03:20 Second — Beta\n"
        "3. 06:40 Third — Gamma\n"
        "4. 10:00 First — Alpha\n")
    assert "Session Only" not in text


def test_sample_preferred_over_legacy_cliptimeable(tmp_path):
    deck_a = {
        "session": [],
        "arrangement": [
            _clip_xml(1, 0, "First - Alpha", "C:\\music\\Alpha - First.wav",
                      0, 480)],
        "legacy": [
            _clip_xml(2, 0, "Ghost - Legacy", "C:\\music\\Legacy - Ghost.wav",
                      0, 480)],
    }
    path = _write_set_real(tmp_path, deck_a, _empty_decks())
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    assert text.splitlines()[1:] == ["1. 00:00 First — Alpha"]
    assert "Ghost" not in text


def test_freeze_sequencer_mirror_is_not_counted(tmp_path):
    clips = [_clip_xml(1, 0, "First - Alpha",
                       "C:\\music\\Alpha - First.wav", 0, 480)]
    deck_a = {"session": [], "arrangement": clips, "freeze": clips}
    path = _write_set_real(tmp_path, deck_a, _empty_decks())
    import als_writer
    root = als_writer.load_als(path).getroot()
    found = mixlist.arrangement_clips(root)
    assert len(found) == 1
    text = mixlist.mixlist_from_als(path, date_str="2026-11-27")
    assert text.splitlines()[1:] == ["1. 00:00 First — Alpha"]
