#!/usr/bin/env python3
"""セットリスト → Ableton Live クリッププラン変換 (MixCraft DJ Studio).

スマホの下ごしらえページが書き出す mixcraft-setlist v1 JSON を、Live の
Session View にそのまま並べられる「クリッププラン」JSON に変換する。
プランは als_writer.py が .als に注入するための入力で、人間が読んで
確認できる形 (JSON) にもなっている。

設計判断 (2026-11-25/26 確定分):
  - セット BPM は開始時に固定し、演奏中は動かさない → プランの set_bpm。
  - 曲はセットリスト順にクリップとして事前に並べる。
  - デッキは交互: 1,3,5... 曲目 → Deck A、2,4,6... 曲目 → Deck B
    (Remote Script の前提: Track 1 = Deck A / Track 2 = Deck B)。
  - rekordbox キューの変換は library.cues_to_clip_plan と同じ規則:
    Hot Cue → クリップ開始オフセット、ループマーク → クリップのループ範囲、
    メモリーキュー → ロケーター。

ファイル解決 (実ファイルが要る。推測では埋めない):
  1. セットリストの track に path があればそれを使う (PC 側で付けた実パス)。
  2. preview_maker の manifest.json と照合する。エントリの source
     (元ファイル名) が正で、曲フォルダ内の実ファイルを指す。マッチ判定の
     (artist, title) の作り方は setlist.tracks_from_manifest と同じ規則
     (matched_* があれば優先、無ければファイル名からの推定)。
  3. 曲フォルダ内の音源ファイル名と照合する
     (setlist._guess_artist_title と同じ "Artist - Title" 規則)。
  どれにも当たらなかった曲は unmatched に理由付きで報告し、
  クリップにはしない。

使い方 (CLI):
    python live_plan.py SETLIST_JSON [--tracks FOLDER]
                        [--manifest MANIFEST_JSON] [--out PLAN_JSON]

純粋ロジック (stdlib + setlist / library) で、tests/test_live_plan.py で検証。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import library
import setlist as setlist_mod

PLAN_FORMAT = "mixcraft-live-plan"
PLAN_VERSION = 1

_AUDIO_SUFFIXES = (".mp3", ".m4a", ".aac", ".ogg", ".oga",
                   ".wav", ".flac", ".aif", ".aiff")


# ---------------------------------------------------------------------------
# 照合ヘルパー
# ---------------------------------------------------------------------------

def _norm(text) -> str:
    """照合用に文字列をそろえる (小文字化 + 空白圧縮)。"""
    if not text:
        return ""
    return " ".join(str(text).split()).lower()


def _pair(artist, title):
    return (_norm(artist), _norm(title))


def _manifest_candidates(manifest) -> list:
    """manifest の各エントリを (照合キー, source ファイル名) にする。

    (artist, title) の作り方は setlist.tracks_from_manifest と同じ:
    matched_artist / matched_title があればそれを、無ければ source の
    ファイル名から推定する。
    """
    out = []
    for entry in (manifest or {}).get("previews", []):
        source = entry.get("source")
        if not source:
            continue
        artist, title = setlist_mod._guess_artist_title(source)
        if entry.get("matched_artist"):
            artist = entry["matched_artist"]
        if entry.get("matched_title"):
            title = entry["matched_title"]
        elif not title and entry.get("title"):
            title = entry["title"]
        out.append({"key": _pair(artist, title), "title_key": _norm(title),
                    "source": source})
    return out


def _folder_candidates(tracks_folder) -> list:
    """曲フォルダ内の音源ファイルを (照合キー, Path) にする。"""
    out = []
    if not tracks_folder:
        return out
    folder = Path(tracks_folder)
    if not folder.is_dir():
        return out
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in _AUDIO_SUFFIXES:
            artist, title = setlist_mod._guess_artist_title(path.name)
            out.append({"key": _pair(artist, title),
                        "title_key": _norm(title), "path": path})
    return out


def _match(candidates, artist, title):
    """候補から (artist, title) 一致 → タイトル単独一致 (一意な時だけ) を探す。

    戻り値: (候補 or None, 理由文字列 or None)。
    """
    key = _pair(artist, title)
    for cand in candidates:
        if cand["key"] == key and key[1]:
            return cand, None
    title_key = _norm(title)
    if title_key:
        by_title = [c for c in candidates if c["title_key"] == title_key]
        if len(by_title) == 1:
            return by_title[0], None
        if len(by_title) > 1:
            return None, f"タイトルが複数候補と一致します: {title!r}"
    return None, None


def _resolve_file(track, tracks_folder, manifest_cands, folder_cands):
    """1 曲の実ファイルを探す。戻り値: (パス文字列 or None, 経由, 理由)。

    経由は "path" / "manifest" / "folder" のいずれか。
    """
    if track.get("path"):
        return str(track["path"]), "path", None
    artist, title = track.get("artist"), track.get("title")
    if manifest_cands:
        cand, reason = _match(manifest_cands, artist, title)
        if cand is not None:
            if tracks_folder:
                return str(Path(tracks_folder) / cand["source"]), "manifest", None
            return cand["source"], "manifest", None
        if reason:
            return None, None, reason
    if folder_cands:
        cand, reason = _match(folder_cands, artist, title)
        if cand is not None:
            return str(cand["path"]), "folder", None
        if reason:
            return None, None, reason
    return None, None, "曲フォルダ / manifest のどちらにも見つかりませんでした"


# ---------------------------------------------------------------------------
# キュー変換 (library.cues_to_clip_plan の規則を 1 クリップ分に畳む)
# ---------------------------------------------------------------------------

def _seconds_to_beats(seconds, bpm):
    if seconds is None or not bpm:
        return None
    return round(seconds * bpm / 60.0, 6)


def _cue_fields(track, clip_track) -> dict:
    """Hot Cue / ループ / メモリーキューをプランの項目に変換する。

    1 曲 = 1 クリップが前提 (デッキに並べる本編クリップ)。開始オフセットは
    最初の Hot Cue (最も早い位置)、ループ範囲はループマークがあればその範囲、
    メモリーキューはロケーター (秒 + BPM があれば拍) になる。
    """
    plan = library.cues_to_clip_plan(clip_track)
    bpm = track.get("bpm")
    hot = plan["clips"]
    start_seconds = min((c["start_seconds"] for c in hot), default=0.0)
    loop = None
    for clip in hot:
        if clip.get("loop_end_seconds") is not None:
            loop = {
                "start_seconds": clip["start_seconds"],
                "end_seconds": clip["loop_end_seconds"],
                "start_beats": _seconds_to_beats(clip["start_seconds"], bpm),
                "end_beats": _seconds_to_beats(clip["loop_end_seconds"], bpm),
            }
            break
    locators = [{
        "name": loc["name"],
        "seconds": loc["start_seconds"],
        "beats": _seconds_to_beats(loc["start_seconds"], bpm),
    } for loc in plan["locators"]]
    return {
        "start_offset_seconds": start_seconds,
        "start_offset_beats": _seconds_to_beats(start_seconds, bpm),
        "loop": loop,
        "locators": locators,
    }


# ---------------------------------------------------------------------------
# プラン組み立て
# ---------------------------------------------------------------------------

def build_plan(setlist, tracks_folder=None, manifest=None) -> dict:
    """正規化済み or 生のセットリストから Live クリッププランを作る。

    setlist: dict / path (setlist.load_setlist で読む)。
    manifest: dict / path / None (preview_maker の manifest.json)。
    tracks_folder: 曲フォルダ (str / Path / None)。
    """
    data = setlist_mod.load_setlist(setlist)
    if isinstance(manifest, (str, Path)):
        manifest = json.loads(Path(manifest).read_text(encoding="utf-8"))
    manifest_cands = _manifest_candidates(manifest) if manifest else []
    folder_cands = _folder_candidates(tracks_folder)
    clip_tracks = setlist_mod.setlist_to_clip_tracks(data)

    clips, unmatched, warnings = [], [], []
    slot_counters = {"A": 0, "B": 0}
    if data.get("set_bpm") is None:
        warnings.append("set_bpm が未設定です (セットの固定 BPM が決まっていません)")
    for index, track in enumerate(data["tracks"]):
        position = index + 1
        file_path, via, reason = _resolve_file(
            track, tracks_folder, manifest_cands, folder_cands)
        if file_path is None:
            unmatched.append({
                "position": position,
                "title": track.get("title") or "",
                "artist": track.get("artist"),
                "reason": reason or "ファイルが見つかりませんでした",
            })
            continue
        deck = "A" if index % 2 == 0 else "B"
        slot = slot_counters[deck]
        slot_counters[deck] += 1
        title = track.get("title") or ""
        artist = track.get("artist")
        entry = {
            "position": position,
            "deck": deck,
            "slot": slot,
            "title": title,
            "artist": artist,
            "name": f"{title} - {artist}" if artist else title,
            "file": file_path,
            "file_via": via,
            "bpm": track.get("bpm"),
            "key": track.get("key"),
            "duration_seconds": track.get("duration_seconds"),
        }
        entry.update(_cue_fields(track, clip_tracks[index]))
        if track.get("bpm") is None:
            warnings.append(
                f"{position} 曲目 ({title}) は BPM 未設定のため Warp 拍換算ができません")
        clips.append(entry)
    return {
        "format": PLAN_FORMAT,
        "version": PLAN_VERSION,
        "set_bpm": data.get("set_bpm"),
        "clips": clips,
        "unmatched": unmatched,
        "warnings": warnings,
    }


def load_plan(source) -> dict:
    """プラン JSON (dict / path) を読み、形式だけ軽く検証して返す。"""
    if isinstance(source, dict):
        plan = source
    else:
        plan = json.loads(Path(source).read_text(encoding="utf-8"))
    if plan.get("format") != PLAN_FORMAT:
        raise ValueError(f"プランの format が違います: {plan.get('format')!r}")
    return plan


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="セットリスト JSON を Live クリッププランに変換します")
    parser.add_argument("setlist", help="mixcraft-setlist v1 の JSON")
    parser.add_argument("--tracks", help="曲フォルダ (実ファイルの置き場)")
    parser.add_argument("--manifest", help="preview_maker の manifest.json")
    parser.add_argument("--out", help="プラン JSON の出力先 (省略時は標準出力)")
    args = parser.parse_args(argv)

    try:
        plan = build_plan(args.setlist, tracks_folder=args.tracks,
                          manifest=args.manifest)
    except (setlist_mod.SetlistFormatError, OSError, json.JSONDecodeError) as exc:
        print(f"NG: セットリストが読めません: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    bpm = plan["set_bpm"]
    print(f"プラン: {len(plan['clips'])} クリップ / set_bpm: "
          f"{bpm if bpm is not None else '(未設定)'}", file=sys.stderr)
    for item in plan["unmatched"]:
        print(f"  未解決 {item['position']} 曲目: {item['title']} "
              f"({item['reason']})", file=sys.stderr)
    for warning in plan["warnings"]:
        print(f"  注意: {warning}", file=sys.stderr)
    if args.out:
        print(f"書き出し: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
