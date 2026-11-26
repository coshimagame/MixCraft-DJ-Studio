#!/usr/bin/env python3
"""演奏後の .als からミックスのトラックリストを自動作成 (MixCraft DJ Studio).

セット BPM は固定でキューポイントも事前に決まっているため、本番中に
DDJ-RB の BROWSE ノブで曲順を変えても成立する (ユーザー判断 2026-11-26)。
その代わり、計画したセットリストと実際に掛かった順番は食い違いうる。
そこで録音が終わったら、演奏後の Live セット (.als) の Arrangement に
記録されたクリップから「実際に掛かった順番」のトラックリストを作る。

読み取り方 (als_writer.py とその fixture が持つ .als 知識を再利用する):
  - パースは als_writer.load_als (gzip / 非圧縮 XML 両対応)。
  - デッキは als_writer と同じ前提: 最初の 2 本の AudioTrack が
    Deck A / Deck B。
  - Arrangement のクリップは MainSequencer 内の ArrangerAutomation 配下の
    AudioClip (公開資料 fileformats.org の ALS 形式ドキュメントでも
    Arrangement クリップは ClipTimeable/ArrangerAutomation/Events と
    されている)。Session のクリップは ClipSlotList の中にあるので、
    ArrangerAutomation の外の AudioClip は数えない。
  - クリップの開始位置は AudioClip の Time 属性 (拍)、長さは
    CurrentEnd - CurrentStart (拍)。テンポは als_writer._set_tempo が
    書く場所 (MasterTrack / MainTrack の Tempo/Manual) から読む。
    セット BPM 固定が前提なので、テンポオートメーションは追わない。

マージ規則:
  時間順に並べたとき連続する同じ曲のクリップは 1 エントリにまとめる
  (Hot Cue の打ち直しやループの再トリガで同じ曲が続けて記録されるため)。
  同じ曲の判定は、強い順に (1) 同じソースファイル (FileRef の Path、
  無ければ RelativePath)、(2) 同じクリップ名、(3) Hot Cue クリップ
  (名前が "Title – Hot Cue X" 形式 = library.cues_to_clip_plan の命名)
  が隣のエントリとタイトル部分一致。別の曲が間に入った後に同じ曲が
  戻ってきた場合は新しいエントリにする。

タイトルは推測しない。表示名はクリップ名 (本編クリップは live_plan の
命名 "Title - Artist" → "Title — Artist" に整形) → 無ければファイル名
(setlist._guess_artist_title の "Artist - Title" 規則) の順で決める。
名前もファイルも無いクリップは曲ではないとみなして飛ばす。

使い方 (CLI):
    python mixlist.py SET.als [--out tracklist.txt]

検証範囲: tests/test_mixlist.py で、als_writer のテストと同じ流儀の
合成 fixture (Arrangement に既知の拍位置でクリップを置いたもの) に対して
抽出・マージ・時刻換算・整形を検証する。本物の Live 10 が録音した
.als で読めるかは実機確認が別途必要 (MIXLIST.md 参照)。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import als_writer
import setlist as setlist_mod


class MixlistError(ValueError):
    """演奏後の .als からトラックリストを作れない。"""


_HOT_CUE_SUFFIX = re.compile(r"\s+–\s+Hot Cue [A-H]$")


# ---------------------------------------------------------------------------
# .als からの抽出
# ---------------------------------------------------------------------------

def _float(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _clip_file(clip):
    """クリップのソースファイル参照 (Path 優先、無ければ RelativePath)。"""
    file_ref = clip.find(".//FileRef")
    if file_ref is None:
        return ""
    path = als_writer._get_value(file_ref, "Path", "") or ""
    if path:
        return path
    return als_writer._get_value(file_ref, "RelativePath", "") or ""


def _file_key(file_ref_text):
    """同一ファイル判定用にパスを正規化する (区切りと大文字小文字を揃える)。"""
    if not file_ref_text:
        return None
    return file_ref_text.replace("\\", "/").strip().lower() or None


def _file_display_name(file_ref_text):
    """パスからファイル名部分だけを取り出す (Windows / POSIX 両対応)。"""
    if not file_ref_text:
        return ""
    return re.split(r"[\\/]", file_ref_text.strip())[-1]


def arrangement_clips(root) -> list:
    """Deck A / B の Arrangement クリップを時間順に返す。

    各要素は dict(deck, start_beats, end_beats, name, file)。
    Session クリップ (ClipSlotList 内) は含まない。
    """
    tracks = als_writer._audio_tracks(root)
    if len(tracks) < 2:
        raise MixlistError(
            f"AudioTrack が {len(tracks)} 本しかありません (Deck A / B の 2 本が必要)")
    clips = []
    for deck, track in zip(("A", "B"), tracks[:2]):
        for automation in track.iter("ArrangerAutomation"):
            for clip in automation.iter("AudioClip"):
                start = _float(clip.get("Time"), 0.0)
                cur_start = _float(als_writer._get_value(clip, "CurrentStart"), 0.0)
                cur_end = _float(als_writer._get_value(clip, "CurrentEnd"), 0.0)
                clips.append({
                    "deck": deck,
                    "start_beats": start,
                    "end_beats": start + max(cur_end - cur_start, 0.0),
                    "name": als_writer._get_value(clip, "Name", "") or "",
                    "file": _clip_file(clip),
                })
    clips.sort(key=lambda c: (c["start_beats"], c["deck"]))
    return clips


def set_tempo(root):
    """セットのテンポ (MasterTrack / MainTrack の Tempo/Manual) を読む。"""
    live_set = root.find("LiveSet")
    master = None
    if live_set is not None:
        master = live_set.find("MasterTrack")
        if master is None:
            master = live_set.find("MainTrack")
    if master is not None:
        for tempo in master.iter("Tempo"):
            manual = tempo.find("Manual")
            if manual is not None and manual.get("Value"):
                return _float(manual.get("Value"), 0.0) or None
    return None


# ---------------------------------------------------------------------------
# 曲の同一判定と表示名
# ---------------------------------------------------------------------------

def _song_base_name(name):
    """Hot Cue サフィックスを外した曲名部分 ("Title – Hot Cue A" → "Title")。"""
    return _HOT_CUE_SUFFIX.sub("", name or "").strip()


def _is_hot_cue_clip(name):
    return bool(_HOT_CUE_SUFFIX.search(name or ""))


def _title_core(name):
    """タイトル部分だけの照合キー ("Title - Artist" / "Title – ..." → "title")。"""
    base = _song_base_name(name)
    if " - " in base:
        base = base.split(" - ", 1)[0]
    return " ".join(base.split()).lower() or None


def _display_from_clip_name(name):
    """本編クリップ名 "Title - Artist" を "Title — Artist" に整形する。"""
    base = _song_base_name(name)
    if " - " in base:
        title, artist = base.split(" - ", 1)
        if title.strip() and artist.strip():
            return f"{title.strip()} — {artist.strip()}"
    return base


def _display_from_file(file_ref_text):
    """ファイル名から表示名を作る ("Artist - Title.wav" → "Title — Artist")。"""
    artist, title = setlist_mod._guess_artist_title(_file_display_name(file_ref_text))
    if artist and title:
        return f"{title} — {artist}"
    return title or ""


def _clip_display(clip):
    """1 クリップ分の表示名 (クリップ名 → ファイル名の順で決める)。"""
    if clip["name"]:
        return _display_from_clip_name(clip["name"])
    if clip["file"]:
        return _display_from_file(clip["file"])
    return None


def merge_entries(clips) -> list:
    """時間順クリップ列をトラックリストのエントリにマージする。

    戻り値: [dict(start_beats, display)]。連続する同じ曲は 1 件にまとめ、
    最初の開始位置を採る。表示名はグループ内で最初に得られた
    「本編クリップ名由来」のものを優先する。
    """
    entries = []
    for clip in clips:
        file_key = _file_key(clip["file"])
        name_key = " ".join((clip["name"] or "").split()).lower() or None
        core = _title_core(clip["name"]) if clip["name"] else None
        hot = _is_hot_cue_clip(clip["name"])
        if not file_key and not name_key:
            continue  # 名前もファイルも無いものは曲として扱わない
        prev = entries[-1] if entries else None
        same_song = False
        if prev is not None:
            if file_key and file_key in prev["file_keys"]:
                same_song = True
            elif name_key and name_key == prev["name_key"]:
                same_song = True
            elif core and core == prev["core"] and (hot or prev["last_hot"]):
                same_song = True
        if same_song:
            if file_key:
                prev["file_keys"].add(file_key)
            prev["last_hot"] = hot
            if not prev["display_from_main"] and clip["name"] and not hot:
                display = _clip_display(clip)
                if display:
                    prev["display"] = display
                    prev["display_from_main"] = True
            continue
        display = _clip_display(clip) or ""
        entries.append({
            "start_beats": clip["start_beats"],
            "display": display,
            "file_keys": {file_key} if file_key else set(),
            "name_key": name_key,
            "core": core,
            "last_hot": hot,
            "display_from_main": bool(clip["name"] and not hot),
        })
    return [{"start_beats": e["start_beats"], "display": e["display"]}
            for e in entries]


# ---------------------------------------------------------------------------
# 整形
# ---------------------------------------------------------------------------

def _mmss(seconds) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def build_mixlist(root, date_str=None) -> str:
    """パース済み .als の root からトラックリスト文字列を作る。"""
    bpm = set_tempo(root)
    if not bpm:
        raise MixlistError(
            "セットのテンポ (MasterTrack の Tempo) が読めないため、"
            "拍を時刻に換算できません")
    clips = arrangement_clips(root)
    entries = merge_entries(clips)
    total_beats = max((c["end_beats"] for c in clips), default=0.0)
    total_seconds = total_beats * 60.0 / bpm

    header_bits = []
    if date_str:
        header_bits.append(date_str)
    header_bits.append(f"Total {_mmss(total_seconds)}")
    header_bits.append(f"{'%g' % bpm} BPM")
    lines = ["# Mixlist — " + " / ".join(header_bits)]
    for index, entry in enumerate(entries, 1):
        seconds = entry["start_beats"] * 60.0 / bpm
        lines.append(f"{index}. {_mmss(seconds)} {entry['display']}")
    return "\n".join(lines) + "\n"


def mixlist_from_als(path, date_str=None) -> str:
    """演奏後の .als ファイルからトラックリスト文字列を作る。

    date_str が無ければファイルの更新日時 (保存した日) を日付にする。
    """
    als_path = Path(path)
    if date_str is None:
        try:
            mtime = als_path.stat().st_mtime
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except OSError:
            date_str = None
    tree = als_writer.load_als(als_path)
    return build_mixlist(tree.getroot(), date_str=date_str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="演奏後の .als からミックスのトラックリストを自動作成します")
    parser.add_argument("als", help="演奏後に保存した Live セット (.als)")
    parser.add_argument("--out", help="トラックリストの出力先 (省略時は標準出力)")
    args = parser.parse_args(argv)

    als_path = Path(args.als)
    if not als_path.is_file():
        print(f"NG: .als が見つかりません: {als_path}\n"
              "  演奏後に Live で保存したセットファイルを指定してください "
              "(MIXLIST.md 参照)。", file=sys.stderr)
        return 2
    try:
        text = mixlist_from_als(als_path)
    except (als_writer.AlsError, MixlistError, OSError) as exc:
        print(f"NG: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"書き出し: {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    count = sum(1 for line in text.splitlines() if line and not line.startswith("#"))
    print(f"トラックリスト: {count} 曲", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
