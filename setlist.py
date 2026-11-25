#!/usr/bin/env python3
"""セットリスト JSON の読み書き (MixCraft DJ Studio, スマホ ⇄ PC).

スマホの下ごしらえページで組んだセットリストを JSON で書き出し、
NAS / Google Drive の共有フォルダ経由で PC に渡し、PC 側 (このモジュール)
で読み込んで Ableton Live 相棒型のクリップ準備につなげるための共通形式。

正本形式 (SETLIST_FORMAT.md に仕様):

    {
      "format": "mixcraft-setlist",
      "version": 1,
      "exported_at": "ISO8601",
      "set_bpm": 122,
      "started": false,
      "current_index": -1,
      "tracks": [
        {"title": "曲名", "artist": "アーティスト or null", "bpm": 120.0,
         "key": "8A", "duration_seconds": 390,
         "preview_url": "試聴URL or null",
         "preview_source": "itunes/deezer/local/file or null",
         "source_url": "曲ページURL or null",
         "local_preview": "切り出しプレビューのファイル名 or null"}
      ]
    }

読み取りは寛容にする: 欠けたフィールドは None / デフォルトで補い、
duration ("6:30" のような mm:ss 文字列) や length_seconds などの別名も
受け付ける。ただし format / version が違うデータは別物なので、
SetlistFormatError で明確に弾く。

使い方 (CLI):
    python setlist.py check <setlist.json>          # 形式検証 + 件数/合計時間
    python setlist.py convert <manifest.json> <out.json>
                                                    # preview_maker の
                                                    # manifest → setlist 変換

純粋ロジックのみ (stdlib) で、tests/test_setlist.py で検証する。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

FORMAT = "mixcraft-setlist"
VERSION = 1


class SetlistFormatError(ValueError):
    """format / version が正本と違う、または JSON として読めない。"""


# ---------------------------------------------------------------------------
# 寛容な型変換ヘルパー
# ---------------------------------------------------------------------------

def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value):
    f = _as_float(value)
    return int(f) if f is not None else None


def _parse_duration(value):
    """秒数への変換。数値 / 数値文字列 / "mm:ss" / "h:mm:ss" を受け付ける。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        total = 0.0
        for n in nums:
            total = total * 60 + n
        return int(total)
    f = _as_float(text)
    return int(f) if f is not None else None


def _first(raw, *names):
    for name in names:
        if raw.get(name) is not None:
            return raw.get(name)
    return None


# ---------------------------------------------------------------------------
# 正規化
# ---------------------------------------------------------------------------

def normalize_track(raw) -> dict:
    """1 曲分を正本の形にそろえる。欠けたフィールドは None で補う。"""
    if not isinstance(raw, dict):
        raw = {}
    duration = _parse_duration(_first(raw, "duration_seconds", "length_seconds",
                                       "duration", "seconds", "length"))
    if duration is None:
        duration = _parse_duration(raw.get("duration_text"))
    return {
        "title": _first(raw, "title", "name") or "",
        "artist": _first(raw, "artist", "artist_name", "matched_artist"),
        "bpm": _as_float(_first(raw, "bpm", "tempo", "average_bpm")),
        "key": _first(raw, "key", "camelot", "tonality", "key_name"),
        "duration_seconds": duration,
        "preview_url": raw.get("preview_url"),
        "preview_source": raw.get("preview_source"),
        "source_url": raw.get("source_url"),
        "local_preview": _first(raw, "local_preview", "preview_file"),
        # PC 側に実ファイルがある場合の任意フィールド (正本では未使用でも保持)
        "path": raw.get("path"),
        "cues": raw.get("cues") or [],
    }


def canonical_track(track) -> dict:
    """保存用の正本フィールドだけに絞った 1 曲分 (path/cues は落とす)。"""
    t = normalize_track(track)
    return {k: t[k] for k in ("title", "artist", "bpm", "key", "duration_seconds",
                              "preview_url", "preview_source", "source_url",
                              "local_preview")}


def normalize_setlist(data) -> dict:
    """セットリスト全体を正本の形にそろえる。format/version 不一致はエラー。"""
    if not isinstance(data, dict):
        raise SetlistFormatError("セットリストは JSON オブジェクトである必要があります")
    fmt = data.get("format")
    if fmt != FORMAT:
        raise SetlistFormatError(
            f"format が違います: {fmt!r} (期待値: {FORMAT!r})。"
            "MixCraft のセットリスト JSON ではない可能性があります")
    version = data.get("version", VERSION)
    if _as_int(version) != VERSION:
        raise SetlistFormatError(
            f"version が違います: {version!r} (対応 version: {VERSION})")
    tracks = data.get("tracks") or []
    if not isinstance(tracks, list):
        raise SetlistFormatError("tracks はリストである必要があります")
    current = _as_int(data.get("current_index"))
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": data.get("exported_at"),
        "set_bpm": _as_float(data.get("set_bpm")),
        "started": bool(data.get("started", False)),
        "current_index": current if current is not None else -1,
        "tracks": [normalize_track(t) for t in tracks],
    }


def total_seconds(setlist) -> int:
    """合計時間 (秒)。duration_seconds が無い曲は 0 として数える。"""
    return sum(t.get("duration_seconds") or 0
               for t in normalize_setlist(setlist)["tracks"])


def format_duration(seconds) -> str:
    """表示用: 3600 未満は m:ss、以上は h:mm:ss。"""
    seconds = int(seconds or 0)
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------

def load_setlist(source) -> dict:
    """path (str/Path) または dict から読み、正規化した dict を返す。"""
    if isinstance(source, dict):
        data = source
    else:
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SetlistFormatError(f"ファイルが読めません: {path} ({exc})") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SetlistFormatError(f"JSON として読めません: {path} ({exc})") from exc
    return normalize_setlist(data)


def save_setlist(setlist, path, now=None) -> dict:
    """正本形式で書き出す。exported_at が無ければ現在時刻を打つ。

    書き出した内容 (正規化済み dict) を返す。PC → スマホの往復用。
    """
    if isinstance(setlist, dict):
        setlist = dict(setlist)
        setlist.setdefault("format", FORMAT)
        setlist.setdefault("version", VERSION)
    data = normalize_setlist(setlist)
    out = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": data["exported_at"] or _now_iso(now),
        "set_bpm": data["set_bpm"],
        "started": data["started"],
        "current_index": data["current_index"],
        "tracks": [canonical_track(t) for t in data["tracks"]],
    }
    Path(path).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return out


def _now_iso(now=None) -> str:
    dt = now if now is not None else datetime.now().astimezone()
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 他モジュールへの橋渡し
# ---------------------------------------------------------------------------

def setlist_to_clip_tracks(setlist) -> list:
    """library.py の track dict (path/title/bpm/key/cues) のリストへ変換する。

    cues_to_clip_plan にそのまま渡せる形。ファイル実体が無い曲は
    path=None とし、タイトル等は保持する。
    """
    data = normalize_setlist(setlist)
    return [{
        "path": t.get("path"),
        "title": t["title"],
        "bpm": t["bpm"],
        "key": t["key"],
        "cues": t.get("cues") or [],
    } for t in data["tracks"]]


def _guess_artist_title(source_name):
    """ファイル名から (artist, title) を推定する (preview_maker と同じ規則)。

    "Artist - Title.wav" のような区切りがあれば分け、無ければ全体を
    title とし artist は None。
    """
    stem = Path(source_name or "").stem
    text = " ".join(stem.replace("_", " ").split())
    if " - " in text:
        artist, title = text.split(" - ", 1)
        return (artist.strip() or None), title.strip()
    return None, text


def tracks_from_manifest(manifest) -> list:
    """preview_maker の manifest から setlist の tracks を組み立てる。

    manifest は dict でも path でもよい。preview_type="online" のエントリは
    preview_url / preview_source / source_url を、"local_cut" のエントリは
    local_preview (切り出しファイル名) を持つ曲になる。混在してよい。
    """
    if isinstance(manifest, (str, Path)):
        manifest = json.loads(Path(manifest).read_text(encoding="utf-8"))
    tracks = []
    for entry in (manifest or {}).get("previews", []):
        artist, title = _guess_artist_title(entry.get("source"))
        if entry.get("matched_artist"):
            artist = entry["matched_artist"]
        if entry.get("matched_title"):
            title = entry["matched_title"]
        elif not title and entry.get("title"):
            title = entry["title"]
        base = {
            "title": title,
            "artist": artist,
            "bpm": None,
            "key": None,
            "duration_seconds": _parse_duration(entry.get("duration_seconds")),
        }
        if entry.get("preview_type") == "online":
            base.update(preview_url=entry.get("preview_url"),
                        preview_source=entry.get("preview_source"),
                        source_url=entry.get("source_url"),
                        local_preview=None)
        else:  # local_cut (および旧形式)
            base.update(preview_url=None,
                        preview_source="local" if entry.get("preview") else None,
                        source_url=None,
                        local_preview=entry.get("preview"))
        tracks.append(normalize_track(base))
    return tracks


def setlist_from_manifest(manifest, set_bpm=None) -> dict:
    """manifest 全体を setlist dict にする (convert CLI 用)。"""
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": None,
        "set_bpm": set_bpm,
        "started": False,
        "current_index": -1,
        "tracks": [canonical_track(t) for t in tracks_from_manifest(manifest)],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_check(path) -> int:
    try:
        data = load_setlist(path)
    except SetlistFormatError as exc:
        print(f"NG: {exc}", file=sys.stderr)
        return 1
    total = total_seconds(data)
    bpm = data["set_bpm"]
    print(f"OK: format={data['format']} version={data['version']}")
    print(f"  set_bpm: {bpm if bpm is not None else '(未設定)'}"
          f" / started: {data['started']} / current_index: {data['current_index']}")
    print(f"  tracks: {len(data['tracks'])} 曲 / 合計時間: {format_duration(total)}"
          f" ({total} 秒)")
    for i, t in enumerate(data["tracks"], 1):
        dur = format_duration(t["duration_seconds"]) if t["duration_seconds"] else "--:--"
        print(f"  {i:2d}. {t['title']} ({dur})")
    return 0


def _cmd_convert(manifest_path, out_path) -> int:
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"NG: manifest が読めません: {exc}", file=sys.stderr)
        return 1
    saved = save_setlist(setlist_from_manifest(manifest), out_path)
    print(f"OK: {len(saved['tracks'])} 曲を {out_path} に書き出しました")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "check":
        return _cmd_check(argv[1])
    if len(argv) == 3 and argv[0] == "convert":
        return _cmd_convert(argv[1], argv[2])
    print(__doc__.split("使い方 (CLI):")[1].split("純粋ロジック")[0].strip()
          if "使い方 (CLI):" in __doc__ else
          "usage: setlist.py check <file> | convert <manifest.json> <out.json>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
