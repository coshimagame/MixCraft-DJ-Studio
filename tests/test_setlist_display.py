"""setlist display formatting: load -> view-model rows / summary (stdlib only).

Pins the pure formatting used by the PC app's set-list panel: per-track
status derivation (played grey / playing green / next red / queued amber
on the panel), explicit phone statuses winning over derivation, link
selection (source_url first, preview_url fallback), and summary totals.
"""
from setlist import display_rows, display_summary


def _setlist(tracks, **kw):
    base = {"format": "mixcraft-setlist", "version": 1, "set_bpm": 122,
            "started": False, "current_index": -1, "tracks": tracks}
    base.update(kw)
    return base


def _track(title, **kw):
    t = {"title": title, "artist": None, "bpm": None, "key": None,
         "duration_seconds": None, "preview_url": None,
         "preview_source": None, "source_url": None, "local_preview": None}
    t.update(kw)
    return t


def test_statuses_derived_from_current_index():
    data = _setlist([_track("A"), _track("B"), _track("C"), _track("D")],
                    started=True, current_index=1)
    rows = display_rows(data)
    assert [r["status"] for r in rows] == ["played", "playing", "next", "queued"]
    assert [r["number"] for r in rows] == [1, 2, 3, 4]
    assert rows[1]["status_label"].startswith("NOW")
    assert rows[2]["status_label"].startswith("NEXT")


def test_not_started_all_queued_no_next():
    data = _setlist([_track("A"), _track("B")], started=False, current_index=-1)
    rows = display_rows(data)
    assert [r["status"] for r in rows] == ["queued", "queued"]


def test_explicit_statuses_win_and_next_is_derived():
    data = _setlist([_track("A", status="played"),
                     _track("B", status="playing"),
                     _track("C", status="queued"),
                     _track("D", status="queued")],
                    started=True, current_index=1)
    rows = display_rows(data)
    assert [r["status"] for r in rows] == ["played", "playing", "next", "queued"]


def test_last_track_playing_has_no_next():
    data = _setlist([_track("A"), _track("B")], started=True, current_index=1)
    rows = display_rows(data)
    assert [r["status"] for r in rows] == ["played", "playing"]


def test_link_selection_source_first_then_preview():
    data = _setlist([
        _track("A", source_url="https://example.com/page",
               preview_url="https://example.com/a.m4a"),
        _track("B", preview_url="https://example.com/b.m4a"),
        _track("C"),
    ])
    rows = display_rows(data)
    assert rows[0]["link_url"] == "https://example.com/page"
    assert rows[1]["link_url"] == "https://example.com/b.m4a"
    assert rows[2]["link_url"] is None


def test_duration_text_and_fields_pass_through():
    data = _setlist([_track("A", artist="Artist", bpm=120.0, key="8A",
                            duration_seconds=390),
                     _track("B")])
    rows = display_rows(data)
    assert rows[0]["duration_text"] == "6:30"
    assert rows[0]["artist"] == "Artist"
    assert rows[0]["bpm"] == 120.0
    assert rows[0]["key"] == "8A"
    assert rows[1]["duration_text"] == "--:--"


def test_summary_totals_and_state():
    data = _setlist([_track("A", duration_seconds=390),
                     _track("B", duration_seconds=405),
                     _track("C")],
                    started=True, current_index=0)
    s = display_summary(data)
    assert s["set_bpm"] == 122
    assert s["track_count"] == 3
    assert s["total_seconds"] == 795
    assert s["total_text"] == "13:15"
    assert s["state_text"] == "再生中 1 曲目"


def test_summary_not_started():
    s = display_summary(_setlist([_track("A")], started=False))
    assert s["state_text"] == "未開始"
    assert s["current_index"] == -1
