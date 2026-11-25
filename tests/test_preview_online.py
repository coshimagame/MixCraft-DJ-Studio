"""preview_maker online preview search (stdlib only, NO real network).

Covers filename -> track info guessing, ffprobe tag JSON parsing, title
normalization / version-token splitting, match scoring (exact, featured
artists, version-mismatch penalty), the iTunes / Deezer response parsers,
find_online_preview with a mock fetcher (incl. network-failure fallback),
and the manifest shape for online entries via process_file integration.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import preview_maker as pm

NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-11-25T12:00:00+00:00"

ITUNES_PAYLOAD = {
    "resultCount": 2,
    "results": [
        {  # version mismatch: must score below the clean version
            "artistName": "Daft Punk",
            "trackName": "Get Lucky (Remix)",
            "collectionName": "Get Lucky Remixes",
            "previewUrl": "https://example.com/remix.m4a",
            "trackViewUrl": "https://music.apple.com/remix",
        },
        {
            "artistName": "Daft Punk, Pharrell Williams & Nile Rodgers",
            "trackName": "Get Lucky",
            "collectionName": "Random Access Memories",
            "previewUrl": "https://example.com/getlucky.m4a",
            "trackViewUrl": "https://music.apple.com/getlucky",
        },
        {  # no previewUrl -> must be skipped by the parser
            "artistName": "Daft Punk",
            "trackName": "Get Lucky",
            "collectionName": "Random Access Memories",
        },
    ],
}

DEEZER_PAYLOAD = {
    "total": 1,
    "data": [
        {
            "title": "Get Lucky (feat. Pharrell Williams and Nile Rodgers)",
            "preview": "https://example.com/deezer.mp3",
            "link": "https://www.deezer.com/track/1",
            "artist": {"name": "Daft Punk"},
            "album": {"title": "Random Access Memories"},
        }
    ],
}


def router_fetcher(itunes=None, deezer=None, calls=None):
    """Fake fetcher: routes by URL host, returns canned payloads."""
    def fetch(url):
        if calls is not None:
            calls.append(url)
        if url.startswith(pm.ITUNES_SEARCH_URL):
            if isinstance(itunes, Exception):
                raise itunes
            return itunes if itunes is not None else {"results": []}
        if url.startswith(pm.DEEZER_SEARCH_URL):
            if isinstance(deezer, Exception):
                raise deezer
            return deezer if deezer is not None else {"data": []}
        raise AssertionError(f"unexpected URL: {url}")
    return fetch


# ---------------------------------------------------------------------------
# guess_track_info: filename -> (artist, title)
# ---------------------------------------------------------------------------

def test_guess_artist_dash_title():
    info = pm.guess_track_info("Daft Punk - Get Lucky.wav")
    assert info == {"artist": "Daft Punk", "title": "Get Lucky"}


def test_guess_strips_track_number_prefixes():
    assert pm.guess_track_info("01 - Daft Punk - Get Lucky.flac")["artist"] == "Daft Punk"
    assert pm.guess_track_info("01. Daft Punk - Get Lucky.mp3")["title"] == "Get Lucky"
    assert pm.guess_track_info("03_Daft Punk_-_Get Lucky.wav") == \
        {"artist": "Daft Punk", "title": "Get Lucky"}


def test_guess_title_only_has_no_artist():
    info = pm.guess_track_info("Get Lucky.mp3")
    assert info["artist"] is None
    assert info["title"] == "Get Lucky"


def test_guess_keeps_four_digit_year_prefix():
    # "1999" is not a track number (only 1-3 digits are stripped)
    info = pm.guess_track_info("1999 - Some Song.wav")
    assert info["artist"] == "1999"
    assert info["title"] == "Some Song"


# ---------------------------------------------------------------------------
# parse_probe_tags: ffprobe JSON (mocked strings)
# ---------------------------------------------------------------------------

def test_parse_probe_tags_lowercase():
    text = json.dumps({"format": {"duration": "372.5",
                                  "tags": {"artist": "Daft Punk",
                                           "title": "Get Lucky"}}})
    info = pm.parse_probe_tags(text)
    assert info == {"artist": "Daft Punk", "title": "Get Lucky",
                    "duration": 372.5}


def test_parse_probe_tags_uppercase_keys():
    text = json.dumps({"format": {"tags": {"ARTIST": "A", "TITLE": "T"}}})
    info = pm.parse_probe_tags(text)
    assert info["artist"] == "A"
    assert info["title"] == "T"
    assert info["duration"] is None


def test_parse_probe_tags_broken_input():
    assert pm.parse_probe_tags("{broken") == \
        {"artist": None, "title": None, "duration": None}
    assert pm.parse_probe_tags("{}")["artist"] is None


# ---------------------------------------------------------------------------
# normalization / parse_title / scoring
# ---------------------------------------------------------------------------

def test_normalize_tokens_accents_and_symbols():
    assert pm.normalize_tokens("Beyoncé") == {"beyonce"}
    assert pm.normalize_tokens("Rock & Roll!") == {"rock", "and", "roll"}


def test_parse_title_feat_is_not_a_version():
    core, version = pm.parse_title("Get Lucky (feat. Pharrell Williams)")
    assert core == {"get", "lucky"}
    assert version == set()


def test_parse_title_bracket_version():
    core, version = pm.parse_title("Get Lucky (Radio Edit)")
    assert core == {"get", "lucky"}
    assert version == {"radio", "edit"}


def test_parse_title_dash_version():
    core, version = pm.parse_title("Some Song - Extended Mix")
    assert core == {"some", "song"}
    assert version == {"extended", "mix"}


def test_parse_title_unclosed_bracket_after_feat_cut():
    # Deezer-style: "(Radio Edit - feat. ...)" leaves an unclosed bracket
    core, version = pm.parse_title(
        "Get Lucky (Radio Edit - feat. Pharrell Williams and Nile Rodgers)")
    assert core == {"get", "lucky"}
    assert version == {"radio", "edit"}


def test_score_exact_match_is_one():
    assert pm.score_match("Daft Punk", "Get Lucky",
                          "Daft Punk", "Get Lucky") == 1.0


def test_score_multi_artist_candidate_still_matches():
    score = pm.score_match(
        "Daft Punk", "Get Lucky",
        "Daft Punk, Pharrell Williams & Nile Rodgers", "Get Lucky")
    assert score == 1.0


def test_score_version_mismatch_is_penalized_below_threshold():
    score = pm.score_match("Daft Punk", "Get Lucky",
                           "Daft Punk", "Get Lucky (Remix)")
    assert abs(score - 0.65) < 1e-9
    assert score < pm.ONLINE_MATCH_THRESHOLD


def test_score_same_version_on_both_sides_is_not_penalized():
    assert pm.score_match("Daft Punk", "Get Lucky (Remix)",
                          "Daft Punk", "Get Lucky (Remix)") == 1.0


def test_score_different_song_is_zero():
    assert pm.score_match("Daft Punk", "Get Lucky",
                          "Daft Punk", "One More Time") == 0.0


def test_score_wrong_artist_is_below_threshold():
    score = pm.score_match("Daft Punk", "Get Lucky",
                           "Someone Else", "Get Lucky")
    assert score < pm.ONLINE_MATCH_THRESHOLD


# ---------------------------------------------------------------------------
# response parsers
# ---------------------------------------------------------------------------

def test_parse_itunes_results_skips_items_without_preview():
    cands = pm.parse_itunes_results(ITUNES_PAYLOAD)
    assert len(cands) == 2
    assert cands[1]["preview_source"] == "itunes"
    assert cands[1]["matched_album"] == "Random Access Memories"
    assert cands[1]["source_url"] == "https://music.apple.com/getlucky"


def test_parse_deezer_results():
    cands = pm.parse_deezer_results(DEEZER_PAYLOAD)
    assert len(cands) == 1
    assert cands[0]["preview_source"] == "deezer"
    assert cands[0]["matched_artist"] == "Daft Punk"
    assert cands[0]["matched_album"] == "Random Access Memories"


def test_parse_deezer_error_payload_is_empty():
    assert pm.parse_deezer_results({"error": {"code": 4}}) == []
    assert pm.parse_itunes_results(None) == []


# ---------------------------------------------------------------------------
# find_online_preview with a mock fetcher (no real network)
# ---------------------------------------------------------------------------

INFO = {"artist": "Daft Punk", "title": "Get Lucky"}


def test_find_picks_clean_version_from_itunes():
    calls = []
    match = pm.find_online_preview(
        INFO, fetcher=router_fetcher(itunes=ITUNES_PAYLOAD, calls=calls))
    assert match["preview_source"] == "itunes"
    assert match["matched_title"] == "Get Lucky"
    assert match["match_score"] == 1.0
    assert match["preview_url"] == "https://example.com/getlucky.m4a"
    # adopted from iTunes, so Deezer was never queried
    assert all("itunes" in url for url in calls)


def test_find_falls_back_to_deezer():
    match = pm.find_online_preview(
        INFO, fetcher=router_fetcher(itunes={"results": []},
                                     deezer=DEEZER_PAYLOAD))
    assert match["preview_source"] == "deezer"
    assert match["match_score"] >= pm.ONLINE_MATCH_THRESHOLD


def test_find_survives_network_failure():
    match = pm.find_online_preview(
        INFO, fetcher=router_fetcher(itunes=OSError("down"),
                                     deezer=TimeoutError("slow")))
    assert match is None


def test_find_itunes_failure_still_tries_deezer():
    match = pm.find_online_preview(
        INFO, fetcher=router_fetcher(itunes=OSError("down"),
                                     deezer=DEEZER_PAYLOAD))
    assert match["preview_source"] == "deezer"


def test_find_below_threshold_returns_none():
    wrong = {"results": [{"artistName": "Someone Else",
                          "trackName": "Get Lucky",
                          "previewUrl": "https://example.com/x.m4a"}]}
    match = pm.find_online_preview(
        INFO, fetcher=router_fetcher(itunes=wrong, deezer={"data": []}))
    assert match is None


def test_find_requires_artist_and_title():
    calls = []
    fetch = router_fetcher(itunes=ITUNES_PAYLOAD, calls=calls)
    assert pm.find_online_preview({"artist": None, "title": "Get Lucky"},
                                  fetcher=fetch) is None
    assert pm.find_online_preview({"artist": "Daft Punk", "title": None},
                                  fetcher=fetch) is None
    assert calls == []  # never searched


# ---------------------------------------------------------------------------
# manifest shape for online entries
# ---------------------------------------------------------------------------

def test_make_online_entry_fields():
    match = {"preview_url": "https://example.com/p.m4a",
             "preview_source": "itunes",
             "matched_title": "Get Lucky",
             "matched_artist": "Daft Punk",
             "matched_album": "Random Access Memories",
             "source_url": "https://music.apple.com/getlucky",
             "match_score": 1.0}
    entry = pm.make_online_entry("Daft Punk - Get Lucky.wav", 372.5, match,
                                 now=NOW)
    assert entry["preview_type"] == "online"
    assert entry["preview"] is None
    assert entry["preview_url"] == "https://example.com/p.m4a"
    assert entry["preview_source"] == "itunes"
    assert entry["matched_title"] == "Get Lucky"
    assert entry["matched_artist"] == "Daft Punk"
    assert entry["matched_album"] == "Random Access Memories"
    assert entry["source_url"] == "https://music.apple.com/getlucky"
    assert entry["match_score"] == 1.0
    assert entry["duration_seconds"] == 372.5
    assert entry["generated_at"] == NOW_ISO


def test_online_entry_needs_no_processing(tmp_path):
    manifest = pm.empty_manifest()
    match = {"preview_url": "https://example.com/p.m4a",
             "preview_source": "deezer", "match_score": 0.9}
    pm.upsert_entry(manifest,
                    pm.make_online_entry("A - B.wav", None, match, now=NOW),
                    now=NOW)
    # no local preview file exists, yet the cached online result is final
    assert pm.needs_processing("A - B.wav", tmp_path, manifest) is False


# ---------------------------------------------------------------------------
# process_file integration (probe / ffmpeg / fetcher all mocked)
# ---------------------------------------------------------------------------

def _setup_track(monkeypatch, tmp_path, name="Daft Punk - Get Lucky.wav",
                 tags=None):
    monkeypatch.setattr(pm, "probe_duration", lambda p: 400.0)
    monkeypatch.setattr(pm, "probe_tags",
                        lambda p: tags if tags is not None else
                        {"artist": "Daft Punk", "title": "Get Lucky"})
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / name).write_bytes(b"wav")
    return inbox / name, tmp_path / "out"


def test_process_file_adopts_online_preview(monkeypatch, tmp_path):
    source, out = _setup_track(monkeypatch, tmp_path)

    def no_cut(*args, **kwargs):
        raise AssertionError("local cut must not run when online matched")

    monkeypatch.setattr(pm, "make_preview", no_cut)
    manifest = pm.empty_manifest()
    entry = pm.process_file(source, out, manifest, now=NOW,
                            fetcher=router_fetcher(itunes=ITUNES_PAYLOAD))
    assert entry["preview_type"] == "online"
    assert entry["preview_url"] == "https://example.com/getlucky.m4a"
    assert entry["matched_artist"].startswith("Daft Punk")
    assert not list(out.glob("*.mp3"))  # no local preview file was made
    saved = pm.load_manifest(out)
    assert saved["previews"][0]["preview_type"] == "online"
    assert pm.needs_processing(source.name, out, saved) is False


def test_process_file_network_failure_falls_back_to_local_cut(
        monkeypatch, tmp_path):
    source, out = _setup_track(monkeypatch, tmp_path)

    def fake_make(source_, dest, start, length):
        Path(dest).write_bytes(b"mp3")
        return True

    monkeypatch.setattr(pm, "make_preview", fake_make)
    manifest = pm.empty_manifest()
    entry = pm.process_file(
        source, out, manifest, now=NOW,
        fetcher=router_fetcher(itunes=OSError("down"),
                               deezer=OSError("down")))
    assert entry["preview_type"] == "local_cut"
    assert entry["preview"] == "Daft_Punk_-_Get_Lucky_preview.mp3"
    assert entry["online_attempted_at"] == NOW_ISO  # searched once, cached
    assert (out / entry["preview"]).is_file()


def test_process_file_no_online_flag_never_searches(monkeypatch, tmp_path):
    source, out = _setup_track(monkeypatch, tmp_path)

    def fake_make(source_, dest, start, length):
        Path(dest).write_bytes(b"mp3")
        return True

    def forbidden_fetcher(url):
        raise AssertionError("fetcher must not be called with online=False")

    monkeypatch.setattr(pm, "make_preview", fake_make)
    manifest = pm.empty_manifest()
    entry = pm.process_file(source, out, manifest, now=NOW, online=False,
                            fetcher=forbidden_fetcher)
    assert entry["preview_type"] == "local_cut"
    assert entry["online_attempted_at"] is None


def test_process_file_without_artist_skips_search(monkeypatch, tmp_path):
    source, out = _setup_track(monkeypatch, tmp_path, name="Get Lucky.wav",
                               tags={})

    def fake_make(source_, dest, start, length):
        Path(dest).write_bytes(b"mp3")
        return True

    def forbidden_fetcher(url):
        raise AssertionError("fetcher must not be called without an artist")

    monkeypatch.setattr(pm, "make_preview", fake_make)
    manifest = pm.empty_manifest()
    entry = pm.process_file(source, out, manifest, now=NOW,
                            fetcher=forbidden_fetcher)
    assert entry["preview_type"] == "local_cut"
    assert entry["online_attempted_at"] is None
