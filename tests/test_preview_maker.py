"""preview_maker pure logic (stdlib only; ffmpeg itself is never invoked).

Covers the preview window calculation (start = 25% of the track, clamped
to fit, short tracks from the top), output-name collision avoidance, and
manifest add / update / de-duplication. process_file is exercised with
probe_duration and make_preview monkeypatched out, so no ffmpeg needed.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import preview_maker as pm

NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-11-25T12:00:00+00:00"


# ---------------------------------------------------------------------------
# compute_window: start position / length
# ---------------------------------------------------------------------------

def test_window_long_track_starts_at_25_percent():
    assert pm.compute_window(400.0) == (100.0, 90.0)


def test_window_clamped_so_preview_fits_the_tail():
    # 25% of 100s = 25s, but 25 + 90 > 100, so start is pulled back to 10s.
    assert pm.compute_window(100.0) == (10.0, 90.0)


def test_window_short_track_plays_from_the_top():
    assert pm.compute_window(60.0) == (0.0, 60.0)


def test_window_exactly_preview_length_is_a_boundary():
    assert pm.compute_window(90.0) == (0.0, 90.0)


def test_window_just_above_preview_length():
    start, length = pm.compute_window(91.0)
    assert length == 90.0
    assert start == 1.0  # min(91 * 0.25, 91 - 90)


def test_window_unknown_duration_starts_at_top():
    assert pm.compute_window(None) == (0.0, 90.0)
    assert pm.compute_window(0) == (0.0, 90.0)


def test_window_custom_preview_length():
    assert pm.compute_window(200.0, preview_seconds=30.0) == (50.0, 30.0)


# ---------------------------------------------------------------------------
# is_audio_file / title / output names
# ---------------------------------------------------------------------------

def test_is_audio_file():
    assert pm.is_audio_file("a.wav")
    assert pm.is_audio_file("a.WAV")
    assert pm.is_audio_file("a.aif")
    assert not pm.is_audio_file("a.txt")
    assert not pm.is_audio_file("a.mp4")


def test_title_from_filename():
    assert pm.title_from_filename("My_Song.wav") == "My Song"
    assert pm.title_from_filename("Artist - Title.flac") == "Artist - Title"


def test_preview_name_no_collision():
    assert pm.preview_name_for("Song.wav", set()) == "Song_preview.mp3"


def test_preview_name_collision_avoidance():
    taken = {"Song_preview.mp3"}
    assert pm.preview_name_for("Song.wav", taken) == "Song_preview_2.mp3"
    taken.add("Song_preview_2.mp3")
    assert pm.preview_name_for("Song.wav", taken) == "Song_preview_3.mp3"


def test_preview_name_sanitizes_and_keeps_japanese():
    assert pm.preview_name_for("My Song!.flac", set()) == "My_Song_preview.mp3"
    assert pm.preview_name_for("曲 タイトル.wav", set()) == "曲_タイトル_preview.mp3"


# ---------------------------------------------------------------------------
# manifest: add / update / de-duplication
# ---------------------------------------------------------------------------

def _entry(source="Song.wav", preview="Song_preview.mp3", duration=400.0):
    start, length = pm.compute_window(duration)
    return pm.make_entry(source, duration, preview, start, length, now=NOW)


def test_make_entry_fields():
    entry = _entry()
    assert entry["source"] == "Song.wav"
    assert entry["title"] == "Song"
    assert entry["duration_seconds"] == 400.0
    assert entry["preview"] == "Song_preview.mp3"
    assert entry["preview_start_seconds"] == 100.0
    assert entry["preview_seconds"] == 90.0
    assert entry["generated_at"] == NOW_ISO


def test_make_entry_unknown_duration_is_null():
    entry = pm.make_entry("Song.wav", None, "Song_preview.mp3", 0.0, 90.0, now=NOW)
    assert entry["duration_seconds"] is None


def test_upsert_adds_and_sorts():
    manifest = pm.empty_manifest()
    pm.upsert_entry(manifest, _entry("B.wav", "B_preview.mp3"), now=NOW)
    pm.upsert_entry(manifest, _entry("A.wav", "A_preview.mp3"), now=NOW)
    assert [e["source"] for e in manifest["previews"]] == ["A.wav", "B.wav"]
    assert manifest["updated_at"] == NOW_ISO


def test_upsert_same_source_replaces_without_duplicating():
    manifest = pm.empty_manifest()
    pm.upsert_entry(manifest, _entry(preview="Song_preview.mp3"), now=NOW)
    pm.upsert_entry(manifest, _entry(preview="Song_preview_2.mp3"), now=NOW)
    assert len(manifest["previews"]) == 1
    assert manifest["previews"][0]["preview"] == "Song_preview_2.mp3"


def test_manifest_save_load_roundtrip(tmp_path):
    manifest = pm.empty_manifest()
    pm.upsert_entry(manifest, _entry(), now=NOW)
    pm.save_manifest(tmp_path, manifest)
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert raw["previews"][0]["source"] == "Song.wav"
    loaded = pm.load_manifest(tmp_path)
    assert loaded["previews"] == manifest["previews"]


def test_load_manifest_missing_or_corrupt_is_empty(tmp_path):
    assert pm.load_manifest(tmp_path)["previews"] == []
    (tmp_path / "manifest.json").write_text("{broken", encoding="utf-8")
    assert pm.load_manifest(tmp_path)["previews"] == []


# ---------------------------------------------------------------------------
# needs_processing / taken names / stability (tmp dirs)
# ---------------------------------------------------------------------------

def test_needs_processing(tmp_path):
    manifest = pm.empty_manifest()
    assert pm.needs_processing("Song.wav", tmp_path, manifest) is True
    pm.upsert_entry(manifest, _entry(), now=NOW)
    assert pm.needs_processing("Song.wav", tmp_path, manifest) is True  # file gone
    (tmp_path / "Song_preview.mp3").write_bytes(b"mp3")
    assert pm.needs_processing("Song.wav", tmp_path, manifest) is False


def test_taken_names_include_disk_and_exclude_own(tmp_path):
    manifest = pm.empty_manifest()
    pm.upsert_entry(manifest, _entry(), now=NOW)
    pm.upsert_entry(manifest, _entry("Other.wav", "Other_preview.mp3"), now=NOW)
    (tmp_path / "Stray.mp3").write_bytes(b"x")
    taken = pm.taken_preview_names(manifest, tmp_path, exclude_source="Song.wav")
    assert "Other_preview.mp3" in taken
    assert "Stray.mp3" in taken
    assert "Song_preview.mp3" not in taken  # own name may be reused


def test_stable_sources_requires_same_size_twice():
    current = {"a.wav": 10, "b.wav": 20, "c.wav": 5}
    previous = {"a.wav": 10, "b.wav": 21}
    assert pm.stable_sources(current, previous) == {"a.wav"}


# ---------------------------------------------------------------------------
# ffmpeg argument building (pure) and graceful no-ffmpeg behaviour
# ---------------------------------------------------------------------------

def test_build_ffmpeg_args_shape():
    args = pm.build_ffmpeg_args("in.wav", "out.mp3", 100.0, 90.0)
    assert args[0] == "ffmpeg"
    assert args[args.index("-ss") + 1] == "100.000"
    assert args[args.index("-t") + 1] == "90.000"
    af = args[args.index("-af") + 1]
    assert "afade=t=in:st=0:d=1.000" in af
    assert "afade=t=out:st=89.000:d=1.000" in af
    assert args[args.index("-b:a") + 1] == "128k"
    assert args[-1] == "out.mp3"


def test_process_pending_without_ffmpeg_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "ffmpeg_available", lambda: False)
    (tmp_path / "Song.wav").write_bytes(b"wav")
    out = tmp_path / "out"
    manifest = pm.empty_manifest()
    assert pm.process_pending(tmp_path, out, manifest) == 0
    assert manifest["previews"] == []


def test_process_file_updates_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "probe_duration", lambda p: 400.0)

    def fake_make(source, dest, start, length):
        Path(dest).write_bytes(b"mp3")
        return True

    monkeypatch.setattr(pm, "make_preview", fake_make)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "Song.wav").write_bytes(b"wav")
    out = tmp_path / "out"
    manifest = pm.empty_manifest()

    entry = pm.process_file(inbox / "Song.wav", out, manifest, now=NOW)
    assert entry["preview"] == "Song_preview.mp3"
    assert entry["preview_start_seconds"] == 100.0
    assert (out / "Song_preview.mp3").is_file()
    saved = pm.load_manifest(out)
    assert [e["source"] for e in saved["previews"]] == ["Song.wav"]
    assert pm.needs_processing("Song.wav", out, saved) is False
