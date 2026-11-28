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
受け付ける。スマホ側ページの実出力は camelCase (setBpm / durationSec /
previewLink / status など) なので、その別名にも対応する (対応表は
SETLIST_FORMAT.md)。format は "mixcraft" で始まる文字列 (大小無視) で
version が 1 なら受け付け、それ以外は SetlistFormatError で明確に弾く。

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
from urllib.parse import urlparse

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


_AUDIO_SUFFIXES = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav", ".flac")


def _looks_like_audio_url(url) -> bool:
    """URL が音源ファイルそのもの (直接再生できる) らしいかどうか。

    パス部分 (クエリ除く) の拡張子で判定する。iTunes / Deezer の試聴
    ファイル (.m4a / .mp3) はこちら、YouTube / Spotify / Beatport /
    Bandcamp の曲ページは拡張子を持たないので対象外になる。
    """
    if not url or not isinstance(url, str):
        return False
    path = urlparse(url).path.lower()
    return path.endswith(_AUDIO_SUFFIXES)


_STATUS_PLAYING = {"playing", "now", "now_playing", "nowplaying",
                   "current", "on_air", "onair"}
_STATUS_PLAYED = {"played", "done", "finished", "complete", "completed"}


def _normalize_status(value):
    """スマホ側の status 文字列を played / playing / queued にそろえる。

    値が無い場合は None (状態不明)。未知の文字列は「まだかかっていない」
    として queued に寄せる。
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if not text:
        return None
    if text in _STATUS_PLAYING:
        return "playing"
    if text in _STATUS_PLAYED:
        return "played"
    return "queued"


# ---------------------------------------------------------------------------
# 正規化
# ---------------------------------------------------------------------------

def normalize_track(raw) -> dict:
    """1 曲分を正本の形にそろえる。欠けたフィールドは None で補う。"""
    if not isinstance(raw, dict):
        raw = {}
    duration = _parse_duration(_first(raw, "duration_seconds", "durationSec",
                                       "duration_sec", "length_seconds",
                                       "duration", "seconds", "length"))
    if duration is None:
        duration = _parse_duration(raw.get("duration_text"))
    # スマホ側の previewLink は「貼られた曲ページ / 試聴リンク」。
    # 音源ファイルそのものらしい URL (.mp3/.m4a 等) は preview_url に、
    # それ以外 (YouTube 等の曲ページ) は source_url に振り分ける。
    preview_url = raw.get("preview_url")
    preview_source = raw.get("preview_source")
    source_url = raw.get("source_url")
    link = _first(raw, "previewLink", "preview_link")
    if link:
        if _looks_like_audio_url(link):
            if preview_url is None:
                preview_url = link
            if preview_source is None:
                preview_source = "link"
        elif source_url is None:
            source_url = link
    return {
        "title": _first(raw, "title", "name") or "",
        "artist": _first(raw, "artist", "artist_name", "matched_artist"),
        "bpm": _as_float(_first(raw, "bpm", "tempo", "average_bpm")),
        "key": _first(raw, "key", "camelot", "tonality", "key_name"),
        "duration_seconds": duration,
        "preview_url": preview_url,
        "preview_source": preview_source,
        "source_url": source_url,
        "local_preview": _first(raw, "local_preview", "preview_file"),
        # スマホ側の付帯情報 (あれば保持する。並びは配列順が正で、
        # position は照合用にそのまま残すだけ)
        "note": _first(raw, "note", "cueNote", "cue_note"),
        "status": _normalize_status(_first(raw, "status", "state")),
        "position": _as_int(raw.get("position")),
        # PC 側に実ファイルがある場合の任意フィールド (正本では未使用でも保持)
        "path": raw.get("path"),
        "cues": raw.get("cues") or [],
    }


def canonical_track(track) -> dict:
    """保存用の正本フィールドだけに絞った 1 曲分 (path/cues は落とす)。

    スマホ側の付帯情報 (note / status / position) は、値がある場合だけ
    一緒に残す (load → save → load で情報が落ちないように)。
    """
    t = normalize_track(track)
    out = {k: t[k] for k in ("title", "artist", "bpm", "key", "duration_seconds",
                             "preview_url", "preview_source", "source_url",
                             "local_preview")}
    for k in ("note", "status", "position"):
        if t.get(k) is not None:
            out[k] = t[k]
    return out


def normalize_setlist(data) -> dict:
    """セットリスト全体を正本の形にそろえる。format/version 不一致はエラー。

    format 判定は緩めてある: "mixcraft" で始まる文字列 (大文字小文字は
    無視) で version が 1 なら受け付ける。スマホ側ページの実出力は
    format の正確な綴りが版によって揺れる可能性があるためで、それ以外
    (欠落・無関係な文字列・version 不一致) は従来通り弾く。
    """
    if not isinstance(data, dict):
        raise SetlistFormatError("セットリストは JSON オブジェクトである必要があります")
    fmt = data.get("format")
    if not (isinstance(fmt, str) and fmt.strip().lower().startswith("mixcraft")):
        raise SetlistFormatError(
            f"format が違います: {fmt!r} (期待値: {FORMAT!r} 系)。"
            "MixCraft のセットリスト JSON ではない可能性があります")
    version = data.get("version", VERSION)
    if _as_int(version) != VERSION:
        raise SetlistFormatError(
            f"version が違います: {version!r} (対応 version: {VERSION})")
    tracks = data.get("tracks") or []
    if not isinstance(tracks, list):
        raise SetlistFormatError("tracks はリストである必要があります")
    norm_tracks = [normalize_track(t) for t in tracks]
    # 再生状態の復元: 明示の current_index があればそれを正とし、無ければ
    # status="playing" の曲から復元する。started はトップレベルが正で、
    # 無ければ played / playing の存在から推定する。
    current = _as_int(_first(data, "current_index", "currentIndex"))
    if current is None or current < 0:
        current = next((i for i, t in enumerate(norm_tracks)
                        if t["status"] == "playing"), -1)
    if data.get("started") is not None:
        started = bool(data.get("started"))
    else:
        started = any(t["status"] in ("played", "playing") for t in norm_tracks)
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": _first(data, "exported_at", "exportedAt"),
        "set_bpm": _as_float(_first(data, "set_bpm", "setBpm")),
        "target_minutes": _as_float(_first(data, "target_minutes",
                                           "targetMinutes")),
        "started": started,
        "current_index": current,
        "tracks": norm_tracks,
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
    if data.get("target_minutes") is not None:
        out["target_minutes"] = data["target_minutes"]
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
# 表示用整形 (PC アプリのセットリストパネル用。Qt 非依存の純粋ロジック)
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    "played": "PLAYED / 済",
    "playing": "NOW / 再生中",
    "next": "NEXT / 次",
    "queued": "QUEUED / 待機",
}


def live_statuses(tracks, playing=(), played=()) -> list:
    """ライブ状態 (デッキの実再生) から各行の status を決める純関数。

    playing: 今かかっている行の index 集合。played: 既に鳴った行の
    index 集合。next は「今かかっている行 (無ければ最後に鳴った行)
    より後ろで最初の未再生の行」。ファイル側の started /
    current_index / 曲ごとの status スナップショットは一切見ない —
    PC アプリのパネルではデッキの実状態が正 (pass 27: 読み込んだ
    JSON の再生位置スナップショットと実際の演奏が食い違って、行の
    表示とデッキの ON AIR が矛盾した)。
    """
    playing = set(playing)
    played = set(played)
    statuses = []
    for i in range(len(tracks)):
        if i in playing:
            statuses.append("playing")
        elif i in played:
            statuses.append("played")
        else:
            statuses.append("queued")
    sounded = playing | played
    if sounded:
        anchor = max(sounded)
        for i in range(anchor + 1, len(statuses)):
            if statuses[i] == "queued":
                statuses[i] = "next"
                break
    return statuses


def display_rows(setlist, statuses=None) -> list:
    """セットリストを表示用の行 dict のリストに整形する。

    各行: number (1 始まり), title, artist, bpm, key, duration_seconds,
    duration_text ("--:--" は長さ不明), status, status_label, link_url
    (source_url 優先、無ければ preview_url、どちらも無ければ None),
    preview_url, source_url, local_preview, note。

    status は played / playing / next / queued の 4 値。曲ごとの status が
    あればそれを正とし、無ければ started / current_index から導出する。
    next (再生中の次に控える曲) は明示の status には無いので常に導出する:
    再生中より後ろで最初の queued が next になる。

    statuses を渡すと、その status 列をそのまま使う (導出しない)。
    PC アプリがライブ状態 (live_statuses) を渡すための口で、行の
    並び・長さが tracks と一致しない場合は無視して従来導出に落とす。
    """
    data = normalize_setlist(setlist)
    tracks = data["tracks"]
    if statuses is not None and len(statuses) != len(tracks):
        statuses = None
    if statuses is None:
        current = data["current_index"]
        started = data["started"]
        statuses = []
        for i, t in enumerate(tracks):
            s = t.get("status")
            if s not in ("played", "playing", "queued"):
                s = None
            if s is None:
                if not started:
                    s = "queued"
                elif i < current:
                    s = "played"
                elif i == current:
                    s = "playing"
                else:
                    s = "queued"
            statuses.append(s)
        if started and current >= 0:
            for i in range(current + 1, len(tracks)):
                if statuses[i] == "queued":
                    statuses[i] = "next"
                    break
    rows = []
    for i, t in enumerate(tracks):
        rows.append({
            "index": i,
            "number": i + 1,
            "title": t["title"],
            "artist": t["artist"],
            "bpm": t["bpm"],
            "key": t["key"],
            "duration_seconds": t["duration_seconds"],
            "duration_text": (format_duration(t["duration_seconds"])
                              if t["duration_seconds"] else "--:--"),
            "status": statuses[i],
            "status_label": STATUS_LABELS[statuses[i]],
            "link_url": t["source_url"] or t["preview_url"],
            "preview_url": t["preview_url"],
            "source_url": t["source_url"],
            "local_preview": t["local_preview"],
            "note": t["note"],
        })
    return rows


def display_summary(setlist) -> dict:
    """セット情報の要約 (セット BPM / 曲数 / 合計時間 / 再生状態)。"""
    data = normalize_setlist(setlist)
    total = sum(t.get("duration_seconds") or 0 for t in data["tracks"])
    current = data["current_index"]
    if not data["started"]:
        state_text = "未開始"
    elif 0 <= current < len(data["tracks"]):
        state_text = f"再生中 {current + 1} 曲目"
    else:
        state_text = "開始済み"
    return {
        "set_bpm": data["set_bpm"],
        "track_count": len(data["tracks"]),
        "total_seconds": total,
        "total_text": format_duration(total),
        "started": data["started"],
        "current_index": current,
        "state_text": state_text,
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
    target = data.get("target_minutes")
    print(f"  set_bpm: {bpm if bpm is not None else '(未設定)'}"
          f" / started: {data['started']} / current_index: {data['current_index']}"
          + (f" / target: {target:g} 分" if target is not None else ""))
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
