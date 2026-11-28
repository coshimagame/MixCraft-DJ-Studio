#!/usr/bin/env python3
"""Live セット (.als) → セットリスト取り込み (MixAble, pass 31).

ユーザーのビジョン (2026-11-28): MixAble ⇄ Live の相互やり取り。
pass 30 で MixAble → Live (Arrangement + Session) を作ったので、
こちらは戻りの道: Live で保存したセット (.als) を開くと、
そのセットの曲順が MixAble のセットリストとして戻ってくる。

読み方 (Live 10 の .als、gzip XML):
  - まず Session を見る。最初の 2 本の AudioTrack (Deck A /
    Deck B) の ClipSlotList を、スロット番号順に、スロット
    ごとに A → B と交互に拾う — pass 30 の書き出しが置いた
    DJ 順そのもの。デッキはトラックで決まる (1 本目 = A、
    2 本目 = B)。
  - 両トラックとも Session が空なら Arrangement に落ちる:
    MainSequencer / Sample / ArrangerAutomation / Events の
    クリップを Events の Time (タイムライン位置) 順に拾う
    (perf_als._timeable_sample と同じ本物の入れ物。ClipTimeable
    は MIDI 用で、オーディオの Arrangement ではない — pass 27
    の実機確定事項)。
  - 曲のファイルはクリップの FileRef から: 絶対 <Path> →
    <Data> blob (絶対パスの UTF-16LE hex、pass 27 で実形式を
    確認) → 相対参照 (RelativePathElement を .als のフォルダ
    から解決できて、実ファイルがある場合だけ)。どれでも取れ
    なければ「ファイルなし」の行として残す — 黙って落とさ
    ない (スマホ JSON のファイルなし行と同じ扱い)。
  - 曲名 / アーティストはクリップ名 ("Artist - Title" を
    setlist._guess_artist_title の規則で分ける)。長さは実
    ファイル → SampleRef の DefaultDuration → Warp マーカー
    の終端 → クリップ区間 (SET BPM 換算) の順に分かる範囲で。
    BPM は Warp マーカーの傾き (その曲を Live が解析した
    テンポ) から。キーは .als のクリップには無いので空。
  - SET BPM は MasterTrack (Live 12 は MainTrack) の Tempo /
    Manual。

返り値は setlist.load_setlist と同じ形の正規化済み dict に、
行ごとの deck (0=A / 1=B) を付けたもの。アプリ側は JSON と
同じ読み込み経路にそのまま流せる。

使い方 (CLI):
    python als_import.py check <set.als>   # 取り込める曲の一覧

純粋ロジックのみ (Qt 非依存) で、tests/test_pass31_als_import.py
で検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import als_writer
import setlist as setlist_mod

AlsError = als_writer.AlsError
_get_value = als_writer._get_value


class AlsImportError(AlsError):
    """取り込める曲が 1 つも無い .als (黙って空リストにはしない)。"""


# ---------------------------------------------------------------------------
# クリップ 1 つ分の取り出し
# ---------------------------------------------------------------------------

def _clip_file_ref(clip):
    """クリップの FileRef (SampleRef の中)。無ければ None。"""
    return clip.find(".//FileRef")


def _looks_absolute(text) -> bool:
    return text.startswith(("/", "\\")) or (
        len(text) > 2 and text[1] == ":")


def _path_from_data_blob(file_ref):
    """<Data> blob (絶対パスの UTF-16LE + 終端 NUL の hex) から
    パスを取り出す。pass 27 で実 Live 10 形式として確認した
    形で、Path が空でもファイルの同一性が残っている場合の受け
    皿。絶対パスらしくなければ採用しない。"""
    data = file_ref.find("Data")
    if data is None or not (data.text or "").strip():
        return None
    try:
        raw = bytes.fromhex(data.text.strip())
        text = raw.decode("utf-16-le").strip("\x00").strip()
    except (ValueError, UnicodeDecodeError):
        return None
    return text if _looks_absolute(text) else None


def _path_from_relative(file_ref, als_dir):
    """相対参照 (RelativePathElement の列) を .als のある
    フォルダから解決する。実ファイルに行き着いた場合だけ
    採用する (行き着かなければ推測でパスを作らない)。"""
    rel = file_ref.find("RelativePath")
    if rel is None:
        return None
    parts = [el.get("Value") for el in rel
             if el.tag == "RelativePathElement" and el.get("Value")]
    if not parts:
        return None
    candidate = Path(als_dir, *parts)
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    return str(resolved) if resolved.is_file() else None


def _resolve_path(clip, als_dir):
    """クリップの実ファイルパス。取れなければ None (行は残る)。"""
    file_ref = _clip_file_ref(clip)
    if file_ref is None:
        return None
    path = _get_value(file_ref, "Path")
    if path:
        return path
    return _path_from_data_blob(file_ref) \
        or _path_from_relative(file_ref, als_dir)


def _warp_markers(clip):
    """(BeatTime, SecTime) の列 (拍順)。"""
    markers = clip.find("WarpMarkers")
    if markers is None:
        return []
    pairs = []
    for marker in markers.findall("WarpMarker"):
        try:
            pairs.append((float(marker.get("BeatTime")),
                          float(marker.get("SecTime"))))
        except (TypeError, ValueError):
            continue
    return sorted(pairs)


def _song_bpm(clip):
    """Warp マーカーの傾きから、その曲のテンポ (Live の解析値)。

    マーカー 2 点以上の拍/秒の傾きがそのまま BPM。1 点しか
    無い・傾きが取れない場合は None (推測で埋めない)。"""
    pairs = _warp_markers(clip)
    if len(pairs) < 2:
        return None
    (b0, s0), (b1, s1) = pairs[0], pairs[-1]
    if b1 <= b0 or s1 <= s0:
        return None
    return round(60.0 * (b1 - b0) / (s1 - s0), 2)


def _song_duration(clip, path, set_bpm):
    """曲の長さ (秒)。分かる範囲で確度の高い順に:
    実ファイルのヘッダ → SampleRef の DefaultDuration →
    Warp マーカーの終端秒 → クリップ区間 (拍) の SET BPM
    換算 (Arrangement の演奏区間しか分からない場合の最終手段)。
    どれも無ければ None。"""
    if path:
        info = als_writer.wav_info(path)
        if info is not None:
            return int(round(info[0] / info[1]))
    sample_ref = clip.find("SampleRef")
    if sample_ref is not None:
        frames = setlist_mod._as_float(
            _get_value(sample_ref, "DefaultDuration"))
        rate = setlist_mod._as_float(
            _get_value(sample_ref, "DefaultSampleRate"))
        if frames and rate:
            return int(round(frames / rate))
    pairs = _warp_markers(clip)
    if pairs and pairs[-1][1] > 0:
        return int(round(pairs[-1][1]))
    start = setlist_mod._as_float(_get_value(clip, "CurrentStart"))
    end = setlist_mod._as_float(_get_value(clip, "CurrentEnd"))
    if start is not None and end is not None and end > start \
            and set_bpm:
        return int(round((end - start) * 60.0 / set_bpm))
    return None


def _song_from_clip(clip, deck, als_dir, set_bpm):
    """AudioClip 1 つ → setlist の生の track dict + deck。"""
    name = _get_value(clip, "Name") or ""
    path = _resolve_path(clip, als_dir)
    if name:
        # クリップ名がファイルパスごと入っている場合 (書き出し
        # 環境のパス区切りが違うと stem がパス全体になる) は、
        # 最後の要素だけを曲名として扱う。
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
        artist, title = setlist_mod._guess_artist_title(name)
    elif path:
        artist, title = setlist_mod._guess_artist_title(
            Path(path).name)
    else:
        artist, title = None, ""
    return {
        "title": title,
        "artist": artist,
        "bpm": _song_bpm(clip),
        "key": None,
        "duration_seconds": _song_duration(clip, path, set_bpm),
        "path": path,
        "deck": deck,
    }


# ---------------------------------------------------------------------------
# Session / Arrangement からの曲順の抽出
# ---------------------------------------------------------------------------

def _session_songs(tracks, als_dir, set_bpm):
    """Session の曲を DJ 順に: スロット番号順、スロットごとに
    Deck A → Deck B (pass 30 の書き出し順)。"""
    per_deck = []
    for track in tracks[:2]:
        clips = []
        try:
            slots = als_writer._slots(track)
        except AlsError:
            slots = []
        for slot in slots:
            value = als_writer._slot_value(slot)
            clips.append(value.find("AudioClip")
                         if value is not None else None)
        per_deck.append(clips)
    songs = []
    for index in range(max((len(c) for c in per_deck), default=0)):
        for deck in (0, 1):
            if deck < len(per_deck) \
                    and index < len(per_deck[deck]) \
                    and per_deck[deck][index] is not None:
                songs.append(_song_from_clip(
                    per_deck[deck][index], deck, als_dir, set_bpm))
    return songs


def _arrangement_songs(tracks, als_dir, set_bpm):
    """Arrangement の曲をタイムライン (Events の Time) 順に。
    デッキはトラックで決まる。入れ物は本物の Sample 配下
    (perf_als と同じ場所) だけを見る。"""
    found = []
    for deck, track in enumerate(tracks[:2]):
        sequencer = track.find("./DeviceChain/MainSequencer")
        sample = sequencer.find("Sample") \
            if sequencer is not None else None
        arranger = sample.find("ArrangerAutomation") \
            if sample is not None else None
        events = arranger.find("Events") \
            if arranger is not None else None
        if events is None:
            continue
        for clip in events.findall("AudioClip"):
            try:
                time = float(clip.get("Time") or 0)
            except ValueError:
                time = 0.0
            found.append((time, deck, clip))
    found.sort(key=lambda item: (item[0], item[1]))
    return [_song_from_clip(clip, deck, als_dir, set_bpm)
            for _, deck, clip in found]


def _master_bpm(root):
    """MasterTrack (Live 12 は MainTrack) の Tempo / Manual。"""
    live_set = root.find("LiveSet")
    if live_set is None:
        return None
    master = live_set.find("MasterTrack")
    if master is None:
        master = live_set.find("MainTrack")
    if master is None:
        return None
    for tempo in master.iter("Tempo"):
        bpm = setlist_mod._as_float(_get_value(tempo, "Manual"))
        if bpm:
            return bpm
    return None


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

def read_als_set(source) -> dict:
    """Live セット (.als) をセットリストとして読み取る。

    返り値: {"setlist": 正規化済み setlist dict (行に deck 付き),
             "count": 曲数, "no_file": ファイルを取り出せなかった
             曲数, "source": "session" | "arrangement",
             "set_bpm": セットのテンポ (無ければ None)}。

    曲が 1 つも取れない .als は AlsImportError で明確に弾く
    (空のリストを黙って開かせない)。"""
    path = Path(source)
    tree = als_writer.load_als(path)
    root = tree.getroot()
    tracks = als_writer._audio_tracks(root)
    if not tracks:
        raise AlsImportError(
            "AudioTrack がありません (Live のセットではない可能性)")
    set_bpm = _master_bpm(root)
    songs = _session_songs(tracks, path.parent, set_bpm)
    origin = "session"
    if not songs:
        songs = _arrangement_songs(tracks, path.parent, set_bpm)
        origin = "arrangement"
    if not songs:
        raise AlsImportError(
            "セットに曲が入っていません (Session にも Arrangement "
            "にもクリップがありません)")
    data = setlist_mod.normalize_setlist({
        "format": setlist_mod.FORMAT,
        "version": setlist_mod.VERSION,
        "set_bpm": set_bpm,
        "tracks": [{
            "title": s["title"], "artist": s["artist"],
            "bpm": s["bpm"], "key": None,
            "duration_seconds": s["duration_seconds"],
            "path": s["path"],
        } for s in songs],
    })
    # normalize は行の並びを変えないので、デッキは位置で戻す。
    for entry, song in zip(data["tracks"], songs):
        entry["deck"] = song["deck"]
    return {
        "setlist": data,
        "count": len(songs),
        "no_file": sum(1 for s in songs if not s["path"]),
        "source": origin,
        "set_bpm": set_bpm,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "check":
        try:
            result = read_als_set(argv[1])
        except AlsError as exc:
            print(f"NG: {exc}", file=sys.stderr)
            return 1
        data = result["setlist"]
        print(f"OK: {result['count']} 曲 (出所: {result['source']})"
              f" / SET BPM: {result['set_bpm']}"
              f" / ファイルなし: {result['no_file']} 曲")
        for i, t in enumerate(data["tracks"], 1):
            dur = setlist_mod.format_duration(t["duration_seconds"]) \
                if t["duration_seconds"] else "--:--"
            print(f"  {i:2d}. [{'AB'[t['deck']]}] "
                  f"{t['artist']} - {t['title']} ({dur}) "
                  f"{t['path'] or '(ファイルなし)'}")
        return 0
    print("usage: als_import.py check <set.als>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
