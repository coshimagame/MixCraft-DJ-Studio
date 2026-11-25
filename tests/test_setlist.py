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
