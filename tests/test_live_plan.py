"""live_plan: setlist -> Live clip plan conversion (stdlib only).

Covers deck alternation and per-deck slot numbering, file resolution via
the track's own path / preview_maker manifest / tracks-folder scan,
explicit unmatched reporting, rekordbox cue conversion (first Hot Cue ->
start offset, loop mark -> loop range, memory cue -> locator with beat
positions) and the set BPM carried through to the plan.
"""
import json

import pytest

import live_plan

CUES = [
    {"name": "Intro", "type": "0", "start": 32.5, "end": None, "num": 0},
    {"name": "Break", "type": "0", "start": 96.0, "end": None, "num": -1},
    {"name": "Loop8", "type": "4", "start": 128.0, "end": 143.5, "num": 1},
]


def _setlist(tracks, set_bpm=122.0):
    return {"format": "mixcraft-setlist", "version": 1, "set_bpm": set_bpm,
            "tracks": tracks}


def _setup(tmp_path):
    """Tracks folder + manifest shared by the resolution tests."""
    folder = tmp_path / "tracks"
    folder.mkdir()
    (folder / "Romain Garcia - Heya.wav").write_bytes(b"a")
    (folder / "Qrion - Proud (Extended Mix).wav").write_bytes(b"b")
    (folder / "Five - Fifth.wav").write_bytes(b"c")
    manifest = {"version": 1, "previews": [
        {"source": "Qrion - Proud (Extended Mix).wav",
         "preview_type": "online", "preview": None,
         "matched_title": "Proud", "matched_artist": "Qrion"},
    ]}
    return folder, manifest


def _tracks(tmp_path):
    return [
        {"title": "Heya", "artist": "Romain Garcia", "bpm": 122.0,
         "key": "8A", "duration_seconds": 390, "cues": CUES},
        {"title": "Proud", "artist": "Qrion", "bpm": 123.0,
         "key": "8A", "duration_seconds": 360},
        {"title": "Path Song", "artist": "Someone", "bpm": 120.0,
         "duration_seconds": 300, "path": str(tmp_path / "elsewhere.wav")},
        {"title": "Ghost", "artist": "Nobody", "bpm": 121.0,
         "duration_seconds": 300},
        {"title": "Fifth", "artist": "Five", "bpm": None,
         "duration_seconds": 280},
    ]


def test_deck_alternation_and_slots(tmp_path):
    folder, manifest = _setup(tmp_path)
    plan = live_plan.build_plan(_setlist(_tracks(tmp_path)),
                                tracks_folder=folder, manifest=manifest)
    by_pos = {c["position"]: c for c in plan["clips"]}
    assert (by_pos[1]["deck"], by_pos[1]["slot"]) == ("A", 0)
    assert (by_pos[2]["deck"], by_pos[2]["slot"]) == ("B", 0)
    assert (by_pos[3]["deck"], by_pos[3]["slot"]) == ("A", 1)
    # Deck is decided by setlist position, so the unmatched track at
    # position 4 still takes the Deck B turn: position 5 lands on Deck A.
    assert (by_pos[5]["deck"], by_pos[5]["slot"]) == ("A", 2)


def test_resolution_routes(tmp_path):
    folder, manifest = _setup(tmp_path)
    plan = live_plan.build_plan(_setlist(_tracks(tmp_path)),
                                tracks_folder=folder, manifest=manifest)
    by_pos = {c["position"]: c for c in plan["clips"]}
    assert by_pos[1]["file_via"] == "folder"
    assert by_pos[1]["file"].endswith("Romain Garcia - Heya.wav")
    # The folder file is "...(Extended Mix).wav", so only the manifest's
    # matched_title can resolve the plain title "Proud".
    assert by_pos[2]["file_via"] == "manifest"
    assert by_pos[2]["file"].endswith("Qrion - Proud (Extended Mix).wav")
    assert by_pos[3]["file_via"] == "path"
    assert by_pos[1]["name"] == "Heya - Romain Garcia"


def test_unmatched_reported_not_guessed(tmp_path):
    folder, manifest = _setup(tmp_path)
    plan = live_plan.build_plan(_setlist(_tracks(tmp_path)),
                                tracks_folder=folder, manifest=manifest)
    assert [c["position"] for c in plan["clips"]] == [1, 2, 3, 5]
    assert len(plan["unmatched"]) == 1
    ghost = plan["unmatched"][0]
    assert ghost["position"] == 4 and ghost["title"] == "Ghost"
    assert ghost["reason"]


def test_cue_conversion(tmp_path):
    folder, manifest = _setup(tmp_path)
    plan = live_plan.build_plan(_setlist(_tracks(tmp_path)),
                                tracks_folder=folder, manifest=manifest)
    heya = next(c for c in plan["clips"] if c["position"] == 1)
    assert heya["start_offset_seconds"] == 32.5
    assert heya["start_offset_beats"] == pytest.approx(32.5 * 122.0 / 60.0)
    assert heya["loop"]["start_seconds"] == 128.0
    assert heya["loop"]["end_seconds"] == 143.5
    assert heya["loop"]["end_beats"] == pytest.approx(143.5 * 122.0 / 60.0)
    assert len(heya["locators"]) == 1
    loc = heya["locators"][0]
    assert loc["name"].endswith("Break")
    assert loc["seconds"] == 96.0
    assert loc["beats"] == pytest.approx(96.0 * 122.0 / 60.0)
    proud = next(c for c in plan["clips"] if c["position"] == 2)
    assert proud["start_offset_seconds"] == 0.0 and proud["loop"] is None
    assert proud["locators"] == []


def test_set_bpm_carried_and_warned_when_missing(tmp_path):
    folder, manifest = _setup(tmp_path)
    plan = live_plan.build_plan(_setlist(_tracks(tmp_path)),
                                tracks_folder=folder, manifest=manifest)
    assert plan["format"] == "mixcraft-live-plan" and plan["set_bpm"] == 122.0
    plan2 = live_plan.build_plan(_setlist(_tracks(tmp_path), set_bpm=None),
                                 tracks_folder=folder, manifest=manifest)
    assert plan2["set_bpm"] is None
    assert any("set_bpm" in w for w in plan2["warnings"])
    # Track 5 has no BPM: warned, beats are None but seconds survive.
    assert any("Fifth" in w or "5 曲目" in w for w in plan2["warnings"])
    fifth = next(c for c in plan2["clips"] if c["position"] == 5)
    assert fifth["start_offset_beats"] is None


def test_cli_round_trip(tmp_path):
    folder, manifest = _setup(tmp_path)
    setlist_path = tmp_path / "setlist.json"
    setlist_path.write_text(json.dumps(_setlist(_tracks(tmp_path))),
                            encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out_path = tmp_path / "plan.json"
    rc = live_plan.main([str(setlist_path), "--tracks", str(folder),
                         "--manifest", str(manifest_path),
                         "--out", str(out_path)])
    assert rc == 0
    plan = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(plan["clips"]) == 4 and len(plan["unmatched"]) == 1
    # The written plan loads back through load_plan (als_writer's entry).
    assert live_plan.load_plan(out_path)["set_bpm"] == 122.0
