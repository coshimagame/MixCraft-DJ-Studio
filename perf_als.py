#!/usr/bin/env python3
"""演奏データ → .als 書き出し (MixCraft DJ Studio / MixAble).

ユーザー方向 (2026-11-27): 「アプリで完結 Liveで修整が理想」。
アプリで演奏した操作ログ (performance.py が再構成する) を、
ユーザーの Live テンプレート .als に注入して「Live で開いて
直せるセット」を作る。als_writer.py と同じ方針:

  - クリップ XML は捏造しない。ベース .als の既存 AudioClip を
    複製し、Arrangement (MainSequencer / ClipTimeable /
    ArrangerAutomation / Events — 構造名は mixlist.py が参照する
    fileformats.org の ALS 形式ドキュメントで確認したもの) に
    演奏した位置・区間で置く。
  - オートメーションは ArrangerAutomation / Envelopes に
    Envelope (EnvelopeTarget / PointeeId → パラメータの
    AutomationTarget Id) として書く。対象パラメータがテン
    プレート内に見つからないエンベロープは書かず、警告する
    (存在しないパラメータを発明しない)。
  - ベース .als は上書きしない。

対象パラメータの探し方 (テンプレート = Remote Script 用に
Utility + EQ Three + Auto Filter を積んだデッキ構成が前提):
  - チャンネル音量: DeviceChain / Mixer / Volume
    (クロスフェーダーは performance.build_perf_plan が焼き込む)
  - GAIN: StereoGain (Utility) の Gain
  - EQ: FilterEQ3 (EQ Three) の GainLo / GainMid / GainHi
  - FILTER: AutoFilter の Frequency
  - テンポ: MasterTrack (Live 12 は MainTrack) の Tempo

検証範囲: tests/test_perf_als.py で合成 fixture に対して注入 →
再パース検証まで。生成した .als を本物の Live 10 が開けるか、
エンベロープが意図通り効くかは実機確認が別途必要。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import als_writer
import performance

AlsError = als_writer.AlsError
_set_value = als_writer._set_value
_get_value = als_writer._get_value
_fmt = als_writer._fmt


# ---------------------------------------------------------------------------
# パラメータ探索
# ---------------------------------------------------------------------------

def _target_id(param_el):
    """パラメータ要素の AutomationTarget Id (無ければ None)。"""
    if param_el is None:
        return None
    target = param_el.find("AutomationTarget")
    if target is None:
        return None
    raw = target.get("Id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _device(track, tag):
    """DeviceChain の直接の子からデバイス要素を探す。"""
    chain = track.find("./DeviceChain")
    if chain is None:
        return None
    for el in chain:
        if el.tag == tag:
            return el
    return None


def _param(track, spec):
    """spec ('mixer', 名前) / ('device', デバイスタグ, 候補名...) →
    パラメータ要素 (無ければ None)。"""
    if spec[0] == "mixer":
        mixer = track.find("./DeviceChain/Mixer")
        return mixer.find(spec[1]) if mixer is not None else None
    device = _device(track, spec[1])
    if device is None:
        return None
    for name in spec[2]:
        el = device.find(name)
        if el is not None:
            return el
    return None


DECK_TARGETS = {
    "volume": ("mixer", "Volume"),
    "gain": ("device", "StereoGain", ("Gain",)),
    "eq_low": ("device", "FilterEQ3", ("GainLo", "GainLow")),
    "eq_mid": ("device", "FilterEQ3", ("GainMid", "GainMiddle")),
    "eq_high": ("device", "FilterEQ3", ("GainHi", "GainHigh")),
    "filter": ("device", "AutoFilter", ("Frequency",)),
}


def _master_track(root):
    live_set = root.find("LiveSet")
    if live_set is None:
        return None
    master = live_set.find("MasterTrack")
    return master if master is not None else live_set.find("MainTrack")


def _tempo_param(root):
    master = _master_track(root)
    if master is None:
        return None
    for tempo in master.iter("Tempo"):
        return tempo
    return None


# ---------------------------------------------------------------------------
# Arrangement の入れ物
# ---------------------------------------------------------------------------

def _arranger(track):
    """(Events, Envelopes) を返す。途中の要素が無ければ作る
    (要素名は ALS 形式ドキュメントで確認したものだけ)。"""
    chain = track.find("./DeviceChain")
    sequencer = chain.find("MainSequencer") if chain is not None else None
    if sequencer is None:
        raise AlsError("トラックに MainSequencer がありません")
    timeable = sequencer.find("ClipTimeable")
    if timeable is None:
        timeable = ET.SubElement(sequencer, "ClipTimeable")
    arranger = timeable.find("ArrangerAutomation")
    if arranger is None:
        arranger = ET.SubElement(timeable, "ArrangerAutomation")
    events = arranger.find("Events")
    if events is None:
        events = ET.SubElement(arranger, "Events")
    envelopes = arranger.find("Envelopes")
    if envelopes is None:
        envelopes = ET.SubElement(arranger, "Envelopes")
    return events, envelopes


# ---------------------------------------------------------------------------
# クリップ / エンベロープの生成
# ---------------------------------------------------------------------------

def _prepare_perf_clip(template, entry, allocator, report):
    """複製したクリップに演奏セグメント 1 つ分を反映する。"""
    clip = copy.deepcopy(template)
    clip.set("Time", _fmt(entry["time_beats"]))
    _set_value(clip, "Name", entry.get("name") or "")

    file_path = entry.get("file") or ""
    sample_ref = clip.find("SampleRef")
    file_ref = clip.find(".//FileRef")
    audio_file = Path(file_path)
    stat = audio_file.stat() if audio_file.is_file() else None
    if stat is None:
        report["warnings"].append(
            f"{entry.get('name')}: ファイルがこの PC に無いため "
            "サイズ/日付は元のままです")
    if file_ref is not None:
        _set_value(file_ref, "Path",
                   str(audio_file.resolve()) if stat is not None
                   else str(audio_file))
        _set_value(file_ref, "RelativePath", "")
        if stat is not None:
            _set_value(file_ref, "OriginalFileSize", stat.st_size)
        _set_value(file_ref, "OriginalCrc", 0)
    duration = entry.get("duration_seconds")
    bpm = entry.get("bpm")
    if sample_ref is not None:
        if stat is not None:
            _set_value(sample_ref, "LastModDate", int(stat.st_mtime))
        info = als_writer.wav_info(audio_file) if stat is not None else None
        if info is not None:
            _set_value(sample_ref, "DefaultDuration", info[0])
            _set_value(sample_ref, "DefaultSampleRate", info[1])
            if duration is None:
                duration = info[0] / info[1]
        elif duration:
            rate = _get_value(sample_ref, "DefaultSampleRate", "44100")
            try:
                rate = int(float(rate))
            except (TypeError, ValueError):
                rate = 44100
            _set_value(sample_ref, "DefaultDuration",
                       int(round(duration * rate)))

    _set_value(clip, "CurrentStart", entry["offset_beats"])
    _set_value(clip, "CurrentEnd", entry["end_beats"])
    _set_value(clip, "IsWarped", True)
    loop_el = clip.find("Loop")
    if loop_el is None:
        loop_el = ET.SubElement(clip, "Loop")
    _set_value(loop_el, "LoopStart", entry["offset_beats"])
    _set_value(loop_el, "LoopEnd", entry["end_beats"])
    _set_value(loop_el, "StartRelative", 0)
    _set_value(loop_el, "LoopOn", False)
    _set_value(loop_el, "OutMarker", entry["end_beats"])
    _set_value(loop_el, "HiddenLoopStart", entry["offset_beats"])
    _set_value(loop_el, "HiddenLoopEnd", entry["end_beats"])
    if duration and bpm:
        markers = clip.find("WarpMarkers")
        if markers is None:
            markers = ET.SubElement(clip, "WarpMarkers")
        for child in list(markers):
            markers.remove(child)
        for sec, beat in ((0.0, 0.0), (float(duration),
                                       duration * bpm / 60.0)):
            marker = ET.SubElement(markers, "WarpMarker")
            marker.set("Id", "0")  # 後段の Id 振り直しで一意化する
            marker.set("SecTime", _fmt(sec))
            marker.set("BeatTime", _fmt(beat))
    else:
        report["warnings"].append(
            f"{entry.get('name')}: 曲の長さまたは BPM が無いため "
            "Warp マーカーは元のままです")

    for el in clip.iter():
        if el.get("Id") is not None:
            el.set("Id", str(allocator.alloc()))
    return clip


def _make_envelope(pointee_id, points, allocator):
    envelope = ET.Element("Envelope")
    target = ET.SubElement(envelope, "EnvelopeTarget")
    pointee = ET.SubElement(target, "PointeeId")
    pointee.set("Value", str(pointee_id))
    automation = ET.SubElement(envelope, "Automation")
    events = ET.SubElement(automation, "Events")
    for beats, value in points:
        event = ET.SubElement(events, "FloatEvent")
        event.set("Id", str(allocator.alloc()))
        event.set("Time", _fmt(beats))
        event.set("Value", _fmt(value))
    return envelope


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

def inject_perf(root, plan) -> dict:
    """パース済み .als の root に演奏プランを注入する。報告 dict。"""
    tracks = als_writer._audio_tracks(root)
    if len(tracks) < 2:
        raise AlsError(
            f"AudioTrack が {len(tracks)} 本しかありません (Deck A / B の 2 本が必要)")
    deck_tracks = {"A": tracks[0], "B": tracks[1]}
    report = {"clips_written": 0, "decks": {"A": 0, "B": 0},
              "envelopes_written": {}, "donor": None, "tempo_set": False,
              "warnings": list(plan.get("warnings") or [])}

    donor = als_writer._find_donor_clip(deck_tracks.values(), root)
    if donor is not None:
        report["donor"] = "clone"
        template = donor
    else:
        report["donor"] = "skeleton"
        report["warnings"].append(
            "ベース .als に既存クリップが無いため、最小スケルトンでクリップを"
            "作りました。この経路は実機未検証です (テンプレートに試しクリップを"
            "1 つ入れて保存し直すと、複製経路になります)")
        template = als_writer._skeleton_clip()

    allocator = als_writer._IdAllocator(root)
    arrangers = {}
    for deck in ("A", "B"):
        entries = (plan.get("clips") or {}).get(deck) or []
        if not entries:
            continue
        events_el, _ = _arranger(deck_tracks[deck])
        arrangers[deck] = events_el
        for entry in entries:
            events_el.append(
                _prepare_perf_clip(template, entry, allocator, report))
            report["clips_written"] += 1
            report["decks"][deck] += 1

    for key, points in (plan.get("envelopes") or {}).items():
        if not points:
            continue
        if key == "tempo":
            param = _tempo_param(root)
            master = _master_track(root)
            if param is None or master is None:
                report["warnings"].append(
                    "テンポの書き込み先 (MasterTrack の Tempo) が無く、"
                    "テンポエンベロープは書けませんでした")
                continue
            pointee = _target_id(param)
            if pointee is None:
                report["warnings"].append(
                    "Tempo に AutomationTarget が無く、テンポ"
                    "エンベロープは書けませんでした")
                continue
            try:
                _, envelopes_el = _arranger(master)
            except AlsError:
                report["warnings"].append(
                    "MasterTrack に Arrangement の入れ物が無く、テンポ"
                    "エンベロープは書けませんでした")
                continue
            envelopes_el.append(_make_envelope(pointee, points, allocator))
            report["envelopes_written"][key] = len(points)
            continue
        name, _, letter = key.rpartition("_")
        track = deck_tracks.get(letter)
        spec = DECK_TARGETS.get(name)
        if track is None or spec is None:
            report["warnings"].append(f"未知のエンベロープです: {key}")
            continue
        pointee = _target_id(_param(track, spec))
        if pointee is None:
            report["warnings"].append(
                f"Deck {letter} の {name} の書き込み先がテンプレートに"
                "無いため、そのエンベロープは書けませんでした")
            continue
        _, envelopes_el = _arranger(track)
        envelopes_el.append(_make_envelope(pointee, points, allocator))
        report["envelopes_written"][key] = len(points)

    als_writer._set_tempo(root, plan.get("set_bpm"), report)
    allocator.finish()
    return report


def _track_meta(paths, setlist_data=None, durations=None, bpms=None):
    """{パス: {'bpm','name','duration_seconds'}} を組み立てる。

    長さは soundfile で実測 (失敗したら None)、BPM と名前は
    セットリストの該当エントリ (パス完全一致 → ファイル名一致)
    から。呼び出し側が渡した durations / bpms が最優先。
    """
    durations = dict(durations or {})
    bpms = dict(bpms or {})
    entries = (setlist_data or {}).get("tracks") or []
    meta = {}
    for path in paths:
        entry = None
        for candidate in entries:
            cpath = candidate.get("path")
            if cpath and (cpath == path
                          or Path(cpath).name == Path(path).name):
                entry = candidate
                break
        seconds = durations.get(path)
        if seconds is None:
            try:
                import soundfile as sf
                seconds = sf.info(path).duration
            except Exception:
                seconds = None
        bpm = bpms.get(path) or (entry or {}).get("bpm")
        name = None
        if entry:
            title = entry.get("title")
            artist = entry.get("artist")
            if title and artist:
                name = f"{title} — {artist}"
            elif title:
                name = title
            if seconds is None:
                seconds = entry.get("duration_seconds")
        meta[path] = {"bpm": bpm, "name": name,
                      "duration_seconds": seconds}
    return meta


def generate(base_path, events_source, out_path, set_bpm=None,
             setlist_data=None, durations=None, bpms=None) -> dict:
    """ベース .als + 操作ログ → 演奏 .als。報告 dict を返す。"""
    events = performance.load_events(events_source)
    paths = []
    for event in events:
        if event.get("action") == "load" and event.get("value") \
                and event["value"] not in paths:
            paths.append(event["value"])
    meta = _track_meta(paths, setlist_data, durations, bpms)
    if set_bpm is None and setlist_data is not None:
        from setlist import display_summary
        set_bpm = display_summary(setlist_data)["set_bpm"]
    if set_bpm is None:
        raise AlsError(
            "SET BPM が決まりません (セットリストか --set-bpm が必要です)")
    recon = performance.reconstruct(
        events,
        durations={p: m["duration_seconds"] for p, m in meta.items()
                   if m["duration_seconds"]},
        bpms={p: m["bpm"] for p, m in meta.items() if m["bpm"]})
    plan = performance.build_perf_plan(recon, set_bpm, meta)
    tree = als_writer.load_als(base_path)
    report = inject_perf(tree.getroot(), plan)
    als_writer.write_als(tree, out_path)
    report["segments"] = {letter: len(recon["segments"][i])
                          for i, letter in enumerate("AB")}
    return report


def validate_base(path):
    """ベース .als が書き出しに使えるかを先に確かめる (pass 24)。

    (reason, has_donor) を返す。reason が None なら使える。
    reason は 'missing' (ファイルが無い) / 'not_als' (Live の
    セットとして読めない) / 'few_tracks' (AudioTrack が 2 本
    未満 — inject_perf が要求する) のいずれか。has_donor は
    複製元のクリップがあるかどうか (無い場合は generate が
    スケルトン経路に落ちて警告する — 失敗ではない)。
    UI は reason を平易な言葉に訳してその場で伝える。
    """
    p = Path(path)
    if not p.is_file():
        return 'missing', False
    try:
        tree = als_writer.load_als(p)
        tracks = als_writer._audio_tracks(tree.getroot())
    except Exception:
        return 'not_als', False
    if len(tracks) < 2:
        return 'few_tracks', False
    try:
        donor = als_writer._find_donor_clip(tracks, tree.getroot())
    except Exception:
        donor = None
    return None, donor is not None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="演奏の操作ログをベース .als に注入して、Live で"
                    "開ける演奏セットを作ります")
    parser.add_argument("base", help="ベースにするテンプレート .als")
    parser.add_argument("events", help="操作保存したイベント JSON")
    parser.add_argument("out", help="出力先 .als (ベースとは別の名前)")
    parser.add_argument("--set-bpm", type=float, default=None,
                        help="セット BPM (省略時はセットリストから)")
    parser.add_argument("--setlist", default=None,
                        help="セットリスト JSON (曲名 / BPM / SET BPM の元)")
    args = parser.parse_args(argv)

    base = Path(args.base)
    if not base.is_file():
        print(f"NG: ベース .als が見つかりません: {base}", file=sys.stderr)
        return 2
    if Path(args.out).resolve() == base.resolve():
        print("NG: 出力先がベースと同じです。ベースを上書きしないため、"
              "別の名前を指定してください。", file=sys.stderr)
        return 2
    setlist_data = None
    if args.setlist:
        try:
            from setlist import load_setlist
            setlist_data = load_setlist(args.setlist)
        except Exception as exc:
            print(f"NG: セットリストが読めません: {exc}", file=sys.stderr)
            return 1
    try:
        report = generate(base, args.events, args.out,
                          set_bpm=args.set_bpm, setlist_data=setlist_data)
    except (AlsError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"NG: {exc}", file=sys.stderr)
        return 1
    envs = report["envelopes_written"]
    print(f"OK: クリップ {report['clips_written']} 個 / "
          f"エンベロープ {len(envs)} 本を書き出しました: {args.out}")
    print(f"  Deck A: {report['decks']['A']} / Deck B: {report['decks']['B']}"
          f" / クリップ元: {report['donor']} / テンポ設定: "
          f"{'済' if report['tempo_set'] else '未'}")
    for warning in report["warnings"]:
        print(f"  注意: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
