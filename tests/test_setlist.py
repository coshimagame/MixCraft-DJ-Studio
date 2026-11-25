"""setlist JSON read/write: phone <-> PC shared format (stdlib only).

Pins the canonical mixcraft-setlist format: tolerant loading (missing
fields, alias field names, "mm:ss" durations), strict format/version
checks, save/load round-trip, manifest -> tracks conversion for the
preview_maker output (online / local_cut mixed), and clip-track export
for library.cues_to_clip_plan.
"""
import json

import pytest

from library import cues_to_clip_plan
from setlist import (FORMAT, VERSION, SetlistFormatError, format_duration,
                     load_setlist, normalize_setlist, save_setlist,
                     setlist_from_manifest, setlist_to_clip_tracks,
                     total_seconds, tracks_from_manifest)

CANONICAL = {
    "format": "mixcraft-setlist",
    "version": 1,
    "exported_at": "2026-11-25T12:00:00+09:00",
    "set_bpm": 122,
    "started": True,
    "current_index": 1,
    "tracks": [
        {"title": "Song A", "artist": "Artist A", "bpm": 120.0, "key": "8A",
         "duration_seconds": 390, "preview_url": "https://example.com/a.mp3",
         "preview_source": "itunes", "source_url": "https://example.com/a",
         "local_preview": None},
        {"title": "Song B", "artist": None, "bpm": 123.5, "key": "5A",
         "duration_seconds": 405, "preview_url": None, "preview_source": "local",
         "source_url": None, "local_preview": "song_b.mp3"},
    ],
}


def test_load_canonical_dict():
    data = load_setlist(CANONICAL)
    assert data["format"] == FORMAT and data["version"] == VERSION
    assert data["set_bpm"] == 122 and data["started"] is True
    assert data["current_index"] == 1
    assert [t["title"] for t in data["tracks"]] == ["Song A", "Song B"]
    assert data["tracks"][0]["preview_source"] == "itunes"
    assert data["tracks"][1]["local_preview"] == "song_b.mp3"


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "setlist.json"
    saved = save_setlist(CANONICAL, path)
    assert saved["exported_at"] == "2026-11-25T12:00:00+09:00"  # preserved
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == saved
    loaded = load_setlist(path)
    assert loaded["set_bpm"] == 122
    assert [t["title"] for t in loaded["tracks"]] == ["Song A", "Song B"]
    assert loaded["tracks"][0]["duration_seconds"] == 390
    # internal helper fields do not leak into the saved file
    assert "path" not in on_disk["tracks"][0]
    assert "cues" not in on_disk["tracks"][0]


def test_save_stamps_exported_at_when_missing(tmp_path):
    path = tmp_path / "s.json"
    src = {"format": FORMAT, "version": 1, "set_bpm": 120, "tracks": []}
    saved = save_setlist(src, path)
    assert saved["exported_at"]  # stamped
    assert saved["started"] is False and saved["current_index"] == -1


def test_missing_track_fields_are_filled_with_none():
    data = load_setlist({"format": FORMAT, "version": 1,
                         "tracks": [{"title": "Only Title"}]})
    t = data["tracks"][0]
    assert t["title"] == "Only Title"
    assert t["artist"] is None and t["bpm"] is None and t["key"] is None
    assert t["duration_seconds"] is None and t["preview_url"] is None
    assert t["preview_source"] is None and t["source_url"] is None
    assert t["local_preview"] is None


def test_alias_fields_are_accepted():
    data = load_setlist({
        "format": FORMAT, "version": 1,
        "tracks": [
            {"name": "Alias Song", "duration": "6:30", "camelot": "8A",
             "tempo": "121"},
            {"title": "Second", "length_seconds": 300, "tonality": "Am"},
            {"title": "Third", "duration": "1:02:03"},
        ],
    })
    a, b, c = data["tracks"]
    assert a["title"] == "Alias Song"
    assert a["duration_seconds"] == 390 and a["key"] == "8A" and a["bpm"] == 121.0
    assert b["duration_seconds"] == 300 and b["key"] == "Am"
    assert c["duration_seconds"] == 3723


def test_format_mismatch_raises():
    with pytest.raises(SetlistFormatError):
        load_setlist({"format": "something-else", "version": 1, "tracks": []})
    with pytest.raises(SetlistFormatError):
        load_setlist({"version": 1, "tracks": []})  # format missing


def test_version_mismatch_raises():
    with pytest.raises(SetlistFormatError):
        load_setlist({"format": FORMAT, "version": 2, "tracks": []})


def test_load_bad_json_file_raises(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SetlistFormatError):
        load_setlist(path)


def test_total_seconds_and_format_duration():
    assert total_seconds(CANONICAL) == 795
    sparse = {"format": FORMAT, "version": 1,
              "tracks": [{"title": "x", "duration_seconds": 60}, {"title": "y"}]}
    assert total_seconds(sparse) == 60
    assert format_duration(795) == "13:15"
    assert format_duration(3723) == "1:02:03"
    assert format_duration(0) == "0:00"


MANIFEST = {
    "version": 1,
    "updated_at": "2026-11-25T12:00:00+09:00",
    "previews": [
        {"source": "Artist A - Song A.wav", "title": "Artist A - Song A",
         "duration_seconds": 390.0, "preview_type": "online", "preview": None,
         "preview_url": "https://example.com/a.mp3", "preview_source": "itunes",
         "matched_title": "Song A", "matched_artist": "Artist A",
         "matched_album": "Album A", "source_url": "https://example.com/a",
         "match_score": 0.95},
        {"source": "Song B.flac", "title": "Song B",
         "duration_seconds": 405.0, "preview_type": "local_cut",
         "preview": "Song B.mp3", "preview_start_seconds": 101.25,
         "preview_seconds": 90.0},
        {"source": "Artist C - Song C.mp3", "title": "Artist C - Song C",
         "duration_seconds": None, "preview_type": "local_cut",
         "preview": "Artist C - Song C.mp3", "preview_start_seconds": 0.0,
         "preview_seconds": 60.0},
    ],
}


def test_tracks_from_manifest_mixed():
    tracks = tracks_from_manifest(MANIFEST)
    assert len(tracks) == 3
    online, local_b, local_c = tracks
    # online: matched title/artist win, URL fields carried over
    assert online["title"] == "Song A" and online["artist"] == "Artist A"
    assert online["preview_url"] == "https://example.com/a.mp3"
    assert online["preview_source"] == "itunes"
    assert online["source_url"] == "https://example.com/a"
    assert online["local_preview"] is None
    assert online["duration_seconds"] == 390
    # local_cut: preview filename becomes local_preview
    assert local_b["title"] == "Song B" and local_b["artist"] is None
    assert local_b["local_preview"] == "Song B.mp3"
    assert local_b["preview_url"] is None and local_b["preview_source"] == "local"
    # local_cut with "Artist - Title" filename: split into artist/title
    assert local_c["artist"] == "Artist C" and local_c["title"] == "Song C"
    assert local_c["duration_seconds"] is None


def test_setlist_from_manifest_round_trip(tmp_path):
    path = tmp_path / "from_manifest.json"
    saved = save_setlist(setlist_from_manifest(MANIFEST, set_bpm=122), path)
    assert saved["set_bpm"] == 122 and len(saved["tracks"]) == 3
    loaded = load_setlist(path)
    assert [t["title"] for t in loaded["tracks"]] == ["Song A", "Song B", "Song C"]
    assert total_seconds(loaded) == 795


# ---------------------------------------------------------------------------
# Phone page's actual export (camelCase, verified against the real builder
# output): keys differ from the canonical snake_case, so load_setlist must
# map them. Pinned here so the two sides cannot drift apart silently.
# ---------------------------------------------------------------------------

PHONE_EXPORT = {
    "format": "mixcraft-setlist",
    "version": 1,
    "exportedAt": "2026-11-25T13:00:00+09:00",
    "setBpm": 122,
    "targetMinutes": 60,
    "started": True,
    "totalSeconds": 1515,
    "tracks": [
        {"position": 1, "title": "Song A", "artist": "Artist A", "bpm": 120,
         "key": "8A", "durationSec": 390, "cueNote": "イントロ32小節",
         "previewLink": "https://www.youtube.com/watch?v=exampleA",
         "status": "played"},
        {"position": 2, "title": "Song B", "artist": "Artist B", "bpm": 122,
         "key": "5A", "durationSec": 405, "cueNote": None,
         "previewLink": "https://audio-ssl.itunes.apple.com/itunes-assets"
                        "/previewB.m4a",
         "status": "playing"},
        {"position": 3, "title": "Song C", "artist": None, "bpm": 123,
         "key": "10A", "durationSec": 720, "cueNote": "",
         "previewLink": None, "status": "queued"},
    ],
}


def test_load_phone_export_camelcase():
    data = load_setlist(PHONE_EXPORT)
    # top-level aliases
    assert data["set_bpm"] == 122
    assert data["exported_at"] == "2026-11-25T13:00:00+09:00"
    assert data["target_minutes"] == 60
    assert data["started"] is True
    # state restored from per-track status (no current_index in the export)
    assert data["current_index"] == 1
    a, b, c = data["tracks"]
    assert a["status"] == "played" and b["status"] == "playing"
    assert c["status"] == "queued"
    # per-track aliases
    assert a["duration_seconds"] == 390 and b["duration_seconds"] == 405
    assert c["duration_seconds"] == 720
    assert a["note"] == "イントロ32小節"
    assert a["position"] == 1 and c["position"] == 3
    # a pasted page link (YouTube) becomes source_url, not a playable file
    assert a["source_url"] == "https://www.youtube.com/watch?v=exampleA"
    assert a["preview_url"] is None
    # a direct audio file link (.m4a) becomes preview_url
    assert b["preview_url"].endswith("/previewB.m4a")
    assert b["preview_source"] == "link"
    assert c["preview_url"] is None and c["source_url"] is None
    # totalSeconds in the export is ignored; totals come from durations
    assert total_seconds(data) == 390 + 405 + 720


def test_phone_export_round_trip_keeps_information(tmp_path):
    path = tmp_path / "phone.json"
    save_setlist(load_setlist(PHONE_EXPORT), path)
    again = load_setlist(path)
    assert again["set_bpm"] == 122 and again["target_minutes"] == 60
    assert again["started"] is True and again["current_index"] == 1
    a, b, _ = again["tracks"]
    assert a["duration_seconds"] == 390 and a["status"] == "played"
    assert a["note"] == "イントロ32小節"
    assert a["source_url"] == "https://www.youtube.com/watch?v=exampleA"
    assert b["status"] == "playing" and b["preview_url"].endswith(".m4a")


def test_format_prefix_is_accepted_case_insensitively():
    for fmt in ("mixcraft-setlist", "Mixcraft Setlist", "MIXCRAFT"):
        data = load_setlist({"format": fmt, "version": 1, "tracks": []})
        assert data["format"] == FORMAT  # normalized to the canonical name
    # ...but an unrelated or missing format still raises, as does version 2
    with pytest.raises(SetlistFormatError):
        load_setlist({"format": "mixsetlist", "version": 1, "tracks": []})
    with pytest.raises(SetlistFormatError):
        load_setlist({"format": "mixcraft-setlist", "version": 2, "tracks": []})


def test_started_and_current_inferred_from_status():
    data = load_setlist({
        "format": FORMAT, "version": 1,
        "tracks": [{"title": "A", "status": "played"},
                   {"title": "B", "status": "now playing"},
                   {"title": "C", "status": "queued"}],
    })
    assert data["started"] is True  # inferred: something has played
    assert data["current_index"] == 1  # "now playing" normalized to playing
    fresh = load_setlist({"format": FORMAT, "version": 1,
                          "tracks": [{"title": "A", "status": "queued"}]})
    assert fresh["started"] is False and fresh["current_index"] == -1


def test_top_level_started_wins_over_status():
    data = load_setlist({
        "format": FORMAT, "version": 1, "started": False,
        "tracks": [{"title": "A", "status": "played"}],
    })
    assert data["started"] is False  # explicit top-level value has priority


def test_setlist_to_clip_tracks():
    clip_tracks = setlist_to_clip_tracks(CANONICAL)
    assert len(clip_tracks) == 2
    first = clip_tracks[0]
    assert first["path"] is None  # no audio file on the PC side (yet)
    assert first["title"] == "Song A" and first["bpm"] == 120.0
    assert first["key"] == "8A" and first["cues"] == []
    # the result feeds straight into library.cues_to_clip_plan
    plan = cues_to_clip_plan(first)
    assert plan == {"clips": [], "locators": []}
