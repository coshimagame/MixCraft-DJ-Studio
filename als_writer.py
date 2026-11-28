#!/usr/bin/env python3
"""クリッププランを .als に注入する (MixCraft DJ Studio).

live_plan.py が作ったクリッププランを、ユーザーが Live で保存した
テンプレート .als (ベース) に注入し、新しい .als を書き出す。
.als は gzip 圧縮 XML。ベースの内容 (デバイチェーン、ミキサー、
ルーティング等) は一切触らず、最初の 2 本の AudioTrack
(Deck A / Deck B) の Session クリップスロットに AudioClip を入れるだけ。

XML の作り方の方針 (重要):
  .als のスキーマは Ableton が公開していないため、クリップ XML を
  ゼロから捏造しない。ベース .als の中に既存の AudioClip があれば、
  それを複製 (ディープコピー) して名前・ファイル参照・Warp 情報だけを
  差し替える。これなら要素構成は本物の Live が書いた形のままになる。
  ベースにクリップが 1 つも無い場合だけ、公開資料 (fileformats.org の
  ALS 形式ドキュメント等) で確認できる要素名だけで組んだ最小スケルトンを
  使い、その旨を警告として必ず報告する (実機未検証の経路)。

  差し替える項目と根拠:
    - FileRef/Path (絶対パス), RelativePath (空にする), OriginalFileSize
      (実ファイルのサイズ), OriginalCrc (0。Live が再計算する)
    - SampleRef/LastModDate (ファイルの mtime), DefaultDuration /
      DefaultSampleRate (WAV はヘッダから実測。それ以外はプランの長さと
      既存レートから換算)
    - WarpMarkers: (SecTime 0 → BeatTime 0) と (曲の長さ → 拍換算) の
      2 点で線形に張り直し、IsWarped=true にする (Warp BPM = 曲の BPM)
    - CurrentStart / LoopStart = 開始オフセット (拍)、CurrentEnd /
      LoopEnd / OutMarker = 曲の終わり (拍)。ループ指定があれば LoopOn=true
    - Id 属性はファイル内で一意になるよう振り直し、NextPointeeId を進める
    - セットのテンポ (MasterTrack の Tempo/Manual) にプランの set_bpm を書く

  スロットが足りない場合は、そのトラック自身のスロット XML を複製して
  増やす。Scenes が存在し数が足りない場合も、最後の Scene を複製して
  増やす (中身のクリップは除去する)。どちらもベース自身の XML の複製で、
  新しい要素形は発明していない。

使い方 (CLI):
    python als_writer.py BASE.als PLAN.json OUT.als

検証範囲: tests/test_als_writer.py で、合成した最小ベース fixture に対して
注入 → 再オープン → XML をパースして検証する所まで。生成した .als を
本物の Live 10 が開けるかは実機確認が別途必要 (SETLIST_TO_LIVE.md 参照)。
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import live_plan as live_plan_mod


class AlsError(ValueError):
    """ベース .als の構造が想定と違う、または書き出しできない。"""


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------

def load_als(path) -> ET.ElementTree:
    """gzip (通常形) / 非圧縮 XML の両方を受ける。"""
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AlsError(f".als の XML が解析できません: {exc}") from exc
    return ET.ElementTree(root)


def write_als(tree: ET.ElementTree, path) -> None:
    body = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=False)
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + body
    Path(path).write_bytes(gzip.compress(data))


# ---------------------------------------------------------------------------
# WAV ヘッダ (DefaultDuration / DefaultSampleRate の実測用)
# ---------------------------------------------------------------------------

def wav_info(path):
    """WAV の (総フレーム数, サンプルレート)。WAV でなければ None。

    RIFF チャンクを歩く (固定 44 バイト前提にしない)。
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    rate = block_align = None
    frames = None
    pos = 12
    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if chunk_id == b"fmt " and len(body) >= 16:
            rate = struct.unpack("<I", body[4:8])[0]
            block_align = struct.unpack("<H", body[12:14])[0]
        elif chunk_id == b"data" and block_align:
            frames = size // block_align
        pos += 8 + size + (size % 2)
    if rate and frames is not None:
        return frames, rate
    return None


# ---------------------------------------------------------------------------
# XML 小物
# ---------------------------------------------------------------------------

def _fmt(value) -> str:
    """Live の書き方に寄せた数値文字列 (10 桁精度)。文字列はそのまま。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return "%.10g" % float(value)


def _set_value(parent, tag, value):
    """<tag Value="..."/> の子要素を探して値を入れる。無ければ作る。"""
    el = parent.find(tag) if parent is not None else None
    if el is None:
        el = ET.SubElement(parent, tag)
    el.set("Value", _fmt(value))
    return el


def _get_value(parent, tag, default=None):
    el = parent.find(tag) if parent is not None else None
    if el is None:
        return default
    return el.get("Value", default)


def _strip_clips(element) -> None:
    """要素ツリーから AudioClip / MidiClip をすべて取り除く (Scene 複製用)。"""
    for child in list(element):
        if child.tag in ("AudioClip", "MidiClip"):
            element.remove(child)
        else:
            _strip_clips(child)


class _IdAllocator:
    """ファイル内で一意な Id を配る。NextPointeeId の更新も担う。"""

    def __init__(self, root):
        max_id = 0
        self._pointee_el = None
        for el in root.iter():
            raw = el.get("Id")
            if raw is not None:
                try:
                    max_id = max(max_id, int(raw))
                except ValueError:
                    pass
            if el.tag == "NextPointeeId" and self._pointee_el is None:
                self._pointee_el = el
        next_pointee = 0
        if self._pointee_el is not None:
            try:
                next_pointee = int(self._pointee_el.get("Value", 0))
            except ValueError:
                next_pointee = 0
        self._next = max(max_id + 1, next_pointee)

    def alloc(self) -> int:
        value = self._next
        self._next += 1
        return value

    def finish(self) -> None:
        if self._pointee_el is not None:
            self._pointee_el.set("Value", str(self._next))


# ---------------------------------------------------------------------------
# トラック / スロット / クリップの探索
# ---------------------------------------------------------------------------

def _audio_tracks(root) -> list:
    live_set = root.find("LiveSet")
    if live_set is None:
        raise AlsError("LiveSet 要素がありません (.als ではない可能性)")
    tracks_el = live_set.find("Tracks")
    if tracks_el is None:
        raise AlsError("Tracks 要素がありません")
    return [el for el in tracks_el if el.tag == "AudioTrack"]


def _slot_list(track) -> ET.Element:
    """トラックの Session クリップスロット一覧 (ClipSlotList) を返す。"""
    sequencer = track.find("./DeviceChain/MainSequencer")
    if sequencer is not None:
        slot_list = sequencer.find("ClipSlotList")
        if slot_list is not None:
            return slot_list
    slot_list = track.find(".//ClipSlotList")
    if slot_list is None:
        raise AlsError("トラックに ClipSlotList がありません")
    return slot_list


def _slots(track) -> list:
    return [el for el in _slot_list(track) if el.tag == "ClipSlot"]


def _slot_value(slot_el):
    """スロットの <Value> (クリップの入れ物) を返す。無ければ None。"""
    inner = slot_el.find("ClipSlot")
    container = inner if inner is not None else slot_el
    return container.find("Value")


def _find_donor_clip(tracks, root):
    """複製元にする既存 AudioClip を探す (デッキのトラック優先)。"""
    for track in tracks:
        for slot in _slots(track):
            value = _slot_value(slot)
            if value is not None:
                clip = value.find("AudioClip")
                if clip is not None:
                    return clip
    for clip in root.iter("AudioClip"):
        return clip
    return None


def _ensure_slots(track, needed, report, deck_label,
                  allocator=None) -> list:
    """スロットが needed 個になるまで、そのトラック自身のスロットを複製する。

    Pass 40: 複製したスロットは複製元の Id を持ち込まない。Id は
    要素の同一性なので、同じ Id のスロットが並ぶ書き出しは
    Live 側でクリップの重複・欠落として現れかねない静かな破損。
    allocator があればそこから、無ければそのトラック自身の
    スロット番号の続きから、新しい Id を振り直す。"""
    slot_list = _slot_list(track)
    slots = _slots(track)
    if not slots:
        raise AlsError(f"Deck {deck_label} のトラックにスロットが 1 つもありません")
    added = 0
    next_local = None
    while len(slots) < needed:
        clone = copy.deepcopy(slots[-1])
        value = _slot_value(clone)
        if value is not None:
            for child in list(value):
                value.remove(child)
        if next_local is None:
            existing = [int(s.get('Id')) for s in slots
                        if (s.get('Id') or '').lstrip('-').isdigit()]
            next_local = (max(existing) + 1) if existing else 0
        for el in clone.iter():
            if el.get('Id') is not None:
                if allocator is not None:
                    el.set('Id', str(allocator.alloc()))
                else:
                    el.set('Id', str(next_local))
                    next_local += 1
        slot_list.append(clone)
        slots.append(clone)
        added += 1
    if added:
        report["slots_extended"][deck_label] = added
    return slots


def _ensure_scenes(root, needed, report, allocator=None) -> None:
    """Scenes があれば、スロット数に足りるまで最後の Scene を複製する。"""
    live_set = root.find("LiveSet")
    scenes_el = live_set.find("Scenes") if live_set is not None else None
    if scenes_el is None:
        return
    scenes = [el for el in scenes_el if el.tag == "Scene"]
    added = 0
    while scenes and len(scenes) < needed:
        clone = copy.deepcopy(scenes[-1])
        _strip_clips(clone)
        if allocator is not None and clone.get("Id") is not None:
            clone.set("Id", str(allocator.alloc()))
        scenes_el.append(clone)
        scenes.append(clone)
        added += 1
    if added:
        report["scenes_extended"] = added


# ---------------------------------------------------------------------------
# 最小スケルトン (ベースにクリップが無い場合の最終手段・実機未検証)
# ---------------------------------------------------------------------------

def _skeleton_clip() -> ET.Element:
    """公開資料で確認できる要素名だけで組んだ最小 AudioClip。

    ベースに複製元のクリップがある場合は使わない。使う場合は必ず警告する。
    """
    clip = ET.Element("AudioClip", {"Id": "0", "Time": "0"})
    _set_value(clip, "LomId", 0)
    _set_value(clip, "LomIdView", 0)
    _set_value(clip, "CurrentStart", 0)
    _set_value(clip, "CurrentEnd", 0)
    loop = ET.SubElement(clip, "Loop")
    for tag, val in (("LoopStart", 0), ("LoopEnd", 0), ("StartRelative", 0),
                     ("LoopOn", False), ("OutMarker", 0),
                     ("HiddenLoopStart", 0), ("HiddenLoopEnd", 0)):
        _set_value(loop, tag, val)
    _set_value(clip, "Name", "")
    _set_value(clip, "Annotation", "")
    _set_value(clip, "ColorIndex", -1)
    _set_value(clip, "LaunchMode", 0)
    _set_value(clip, "LaunchQuantisation", 0)
    _set_value(clip, "Legato", False)
    _set_value(clip, "Ram", False)
    _set_value(clip, "Disabled", False)
    _set_value(clip, "VelocityAmount", 0)
    _set_value(clip, "FreezeStart", 0)
    _set_value(clip, "FreezeEnd", 0)
    _set_value(clip, "IsWarped", False)
    sample_ref = ET.SubElement(clip, "SampleRef")
    file_ref = ET.SubElement(sample_ref, "FileRef")
    for tag, val in (("RelativePathType", 0), ("RelativePath", ""),
                     ("Path", ""), ("LivePackName", ""), ("LivePackId", ""),
                     ("OriginalFileSize", 0), ("OriginalCrc", 0)):
        _set_value(file_ref, tag, val)
    _set_value(sample_ref, "LastModDate", 0)
    context = ET.SubElement(sample_ref, "SourceContext")
    ET.SubElement(context, "SourceContext")
    _set_value(sample_ref, "SampleUsageHint", 0)
    _set_value(sample_ref, "DefaultDuration", 0)
    _set_value(sample_ref, "DefaultSampleRate", 44100)
    ET.SubElement(clip, "Onsets")
    _set_value(clip, "WarpMode", 0)
    ET.SubElement(clip, "WarpMarkers")
    _set_value(clip, "SavedPlayingOffset", 0)
    _set_value(clip, "Transposition", 0)
    ET.SubElement(clip, "Envelopes")
    return clip


# ---------------------------------------------------------------------------
# クリップの差し替え
# ---------------------------------------------------------------------------

def _prepare_clip(template, entry, allocator, report) -> ET.Element:
    """テンプレート (複製 or スケルトン) にプランの 1 曲分を反映する。"""
    clip = copy.deepcopy(template)
    clip.set("Time", "0")
    _set_value(clip, "Name", entry.get("name") or "")

    file_path = entry.get("file") or ""
    sample_ref = clip.find("SampleRef")
    file_ref = clip.find(".//FileRef")
    stat = None
    audio_file = Path(file_path)
    if audio_file.is_file():
        stat = audio_file.stat()
    else:
        report["warnings"].append(
            f"{entry.get('name')}: ファイルがこの PC に無いため "
            "サイズ/日付は元のままです")
    if file_ref is not None:
        _set_value(file_ref, "Path",
                   str(audio_file.resolve()) if stat is not None else str(audio_file))
        _set_value(file_ref, "RelativePath", "")
        if stat is not None:
            _set_value(file_ref, "OriginalFileSize", stat.st_size)
        _set_value(file_ref, "OriginalCrc", 0)
    if sample_ref is not None:
        if stat is not None:
            _set_value(sample_ref, "LastModDate", int(stat.st_mtime))
        info = wav_info(audio_file) if stat is not None else None
        duration = entry.get("duration_seconds")
        if info is not None:
            _set_value(sample_ref, "DefaultDuration", info[0])
            _set_value(sample_ref, "DefaultSampleRate", info[1])
        elif duration:
            rate = _get_value(sample_ref, "DefaultSampleRate", "44100")
            try:
                rate = int(float(rate))
            except (TypeError, ValueError):
                rate = 44100
            _set_value(sample_ref, "DefaultDuration", int(round(duration * rate)))

    bpm = entry.get("bpm")
    duration = entry.get("duration_seconds")
    if bpm and duration:
        end_beats = duration * bpm / 60.0
        start_beats = entry.get("start_offset_beats")
        if start_beats is None:
            start_beats = (entry.get("start_offset_seconds") or 0.0) * bpm / 60.0
        loop_plan = entry.get("loop")
        loop_start = start_beats
        loop_end = end_beats
        if loop_plan:
            loop_start = loop_plan.get("start_beats")
            if loop_start is None:
                loop_start = loop_plan["start_seconds"] * bpm / 60.0
            loop_end = loop_plan.get("end_beats")
            if loop_end is None:
                loop_end = loop_plan["end_seconds"] * bpm / 60.0
        _set_value(clip, "CurrentStart", start_beats)
        _set_value(clip, "CurrentEnd", end_beats)
        _set_value(clip, "IsWarped", True)
        loop_el = clip.find("Loop")
        if loop_el is None:
            loop_el = ET.SubElement(clip, "Loop")
        _set_value(loop_el, "LoopStart", loop_start)
        _set_value(loop_el, "LoopEnd", loop_end)
        _set_value(loop_el, "StartRelative", 0)
        _set_value(loop_el, "LoopOn", bool(loop_plan))
        _set_value(loop_el, "OutMarker", end_beats)
        _set_value(loop_el, "HiddenLoopStart", loop_start)
        _set_value(loop_el, "HiddenLoopEnd", loop_end)
        markers = clip.find("WarpMarkers")
        if markers is None:
            markers = ET.SubElement(clip, "WarpMarkers")
        for child in list(markers):
            markers.remove(child)
        for sec, beat in ((0.0, 0.0), (float(duration), end_beats)):
            marker = ET.SubElement(markers, "WarpMarker")
            marker.set("Id", "0")  # 後段の Id 振り直しで一意化する
            marker.set("SecTime", _fmt(sec))
            marker.set("BeatTime", _fmt(beat))
    else:
        report["warnings"].append(
            f"{entry.get('name')}: BPM または曲の長さが無いため "
            "Warp / クリップ長は元のままです")

    # Id の振り直し (クリップ内の全 Id 属性をファイル内で一意に)
    for el in clip.iter():
        if el.get("Id") is not None:
            el.set("Id", str(allocator.alloc()))
    return clip


def _set_tempo(root, set_bpm, report) -> None:
    """MasterTrack (Live 12 では MainTrack) の Tempo/Manual に set_bpm を書く。"""
    if set_bpm is None:
        report["warnings"].append("set_bpm が無いためセットのテンポは元のままです")
        return
    live_set = root.find("LiveSet")
    master = None
    if live_set is not None:
        master = live_set.find("MasterTrack")
        if master is None:
            master = live_set.find("MainTrack")
    if master is not None:
        for tempo in master.iter("Tempo"):
            manual = tempo.find("Manual")
            if manual is not None:
                manual.set("Value", _fmt(set_bpm))
                report["tempo_set"] = True
                return
    report["warnings"].append("MasterTrack の Tempo が見つからず、テンポは未設定です")


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

def inject_plan(root, plan) -> dict:
    """パース済み .als の root にプランを注入する。報告 dict を返す。"""
    tracks = _audio_tracks(root)
    if len(tracks) < 2:
        raise AlsError(
            f"AudioTrack が {len(tracks)} 本しかありません (Deck A / B の 2 本が必要)")
    deck_tracks = {"A": tracks[0], "B": tracks[1]}
    by_deck = {"A": [], "B": []}
    for entry in plan.get("clips", []):
        if entry.get("deck") in by_deck:
            by_deck[entry["deck"]].append(entry)
    for entries in by_deck.values():
        entries.sort(key=lambda e: e.get("slot", 0))

    report = {"clips_written": 0, "decks": {"A": 0, "B": 0},
              "donor": None, "tempo_set": False,
              "slots_extended": {}, "scenes_extended": 0, "warnings": []}

    donor = _find_donor_clip(deck_tracks.values(), root)
    if donor is not None:
        report["donor"] = "clone"
        template = donor
    else:
        report["donor"] = "skeleton"
        report["warnings"].append(
            "ベース .als に既存クリップが無いため、最小スケルトンでクリップを"
            "作りました。この経路は実機未検証です (テンプレートに試しクリップを"
            "1 つ入れて保存し直すと、複製経路になります)")
        template = _skeleton_clip()

    allocator = _IdAllocator(root)
    needed_max = 0
    for deck in ("A", "B"):
        entries = by_deck[deck]
        if not entries:
            continue
        needed = max(e.get("slot", 0) for e in entries) + 1
        needed_max = max(needed_max, needed)
        slots = _ensure_slots(deck_tracks[deck], needed, report, deck,
                              allocator=allocator)
        for entry in entries:
            slot_el = slots[entry.get("slot", 0)]
            value = _slot_value(slot_el)
            if value is None:
                report["warnings"].append(
                    f"{entry.get('name')}: スロットの入れ物 (Value) が無く配置できませんでした")
                continue
            for child in list(value):
                value.remove(child)
            value.append(_prepare_clip(template, entry, allocator, report))
            report["clips_written"] += 1
            report["decks"][deck] += 1
    if needed_max:
        _ensure_scenes(root, needed_max, report, allocator)
    _set_tempo(root, plan.get("set_bpm"), report)
    allocator.finish()
    return report


def generate(base_path, plan, out_path) -> dict:
    """ベース .als + プラン → 新しい .als を書き出し、報告 dict を返す。"""
    if isinstance(plan, (str, Path)):
        plan = live_plan_mod.load_plan(plan)
    tree = load_als(base_path)
    report = inject_plan(tree.getroot(), plan)
    write_als(tree, out_path)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="クリッププランをベース .als に注入して新しい .als を作ります")
    parser.add_argument("base", help="ベースにするテンプレート .als")
    parser.add_argument("plan", help="live_plan.py が作ったプラン JSON")
    parser.add_argument("out", help="出力先 .als (ベースとは別の名前)")
    args = parser.parse_args(argv)

    base = Path(args.base)
    if not base.is_file():
        print(f"NG: ベース .als が見つかりません: {base}\n"
              "  Live でテンプレートを開いて「名前を付けて保存」した .als を"
              "指定してください (SETLIST_TO_LIVE.md 手順 3)。", file=sys.stderr)
        return 2
    if Path(args.out).resolve() == base.resolve():
        print("NG: 出力先がベースと同じです。ベースを上書きしないため、"
              "別の名前を指定してください。", file=sys.stderr)
        return 2
    try:
        plan = live_plan_mod.load_plan(args.plan)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"NG: プランが読めません: {exc}", file=sys.stderr)
        return 1
    try:
        report = generate(base, plan, args.out)
    except AlsError as exc:
        print(f"NG: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {report['clips_written']} クリップを書き出しました: {args.out}")
    print(f"  Deck A: {report['decks']['A']} / Deck B: {report['decks']['B']}"
          f" / クリップ元: {report['donor']} / テンポ設定: "
          f"{'済' if report['tempo_set'] else '未'}")
    for warning in report["warnings"]:
        print(f"  注意: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
