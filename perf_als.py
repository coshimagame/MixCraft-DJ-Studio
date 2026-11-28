#!/usr/bin/env python3
"""演奏データ → .als 書き出し (MixCraft DJ Studio / MixAble).

ユーザー方向 (2026-11-27): 「アプリで完結 Liveで修整が理想」。
アプリで演奏した操作ログ (performance.py が再構成する) を、
ユーザーの Live テンプレート .als に注入して「Live で開いて
直せるセット」を作る。als_writer.py と同じ方針:

  - クリップ XML は捏造しない。ベース .als の既存 AudioClip を
    複製し、Arrangement (オーディオトラックは MainSequencer /
    Sample / ArrangerAutomation / Events、マスターは
    FreezeSequencer / AudioSequencer / Sample 配下) に演奏した
    位置・区間で置く。入れ物の場所は pass 27 の実機検証で確定
    した: 以前使っていた ClipTimeable は MIDI トラック用で、
    オーディオトラックに書いても Live 10.1.43 は丸ごと無視
    した (ユーザーの実ファイルでは本物の Sample /
    ArrangerAutomation が空のまま残り、書き出し分だけが
    ClipTimeable に入っていた)。
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
  - GAIN: StereoGain (Utility) の Gain (素の値域は振幅)
  - EQ: FilterEQ3 (EQ Three) の GainLo / GainMid / GainHi (振幅)
  - FILTER: AutoFilter の Cutoff (Hz)。中央を跨ぐ演奏では
    FilterType (0=LP / 1=HP) のエンベロープも書く。片側だけの
    演奏では、出力側デバイスの FilterType 初期値を演奏した
    側へ合わせる (テンプレ側が逆タイプだと、カットオフだけ
    写してもフィルターの向きが逆になるため)。一度も触らな
    かったデッキには FILTER 系を一切書かない。
  - テンポ: MasterTrack (Live 12 は MainTrack) の Tempo

pass 28: エンベロープの値域は Live の素の値域に統一した
(performance.py の docstring に実テンプレ Manual 値の証拠)。
以前は GAIN / EQ を dB、FILTER をノブ位置で書いており、Live
では無音・無効値・最小値張り付きになっていた。

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
    """トラックのデバイス要素を探す。

    本物の Live 10 ではデバイスは DeviceChain / DeviceChain /
    Devices の中に入っている (ユーザーの実テンプレで確認)。
    以前は外側 DeviceChain の直接の子しか見ておらず、実テンプ
    レートでは Mixer (直接の子) 以外の全デバイス — Utility /
    EQ Three / Auto Filter — を見落として、GAIN・EQ・FILTER
    のエンベロープが全部書けていなかった。Devices の中を
    優先し、直接の子 (合成フィクスチャの形) にも当てる。"""
    chain = track.find("./DeviceChain")
    if chain is None:
        return None
    for devices in chain.iter("Devices"):
        for el in devices:
            if el.tag == tag:
                return el
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
    # 実 Live 10.1.43 の AutoFilter のカットオフは <Cutoff>
    # (直接の子に Frequency は無い — 奥にある同名要素は LFO
    # 側で別物)。フィクスチャ等の旧形に Frequency も残す。
    "filter": ("device", "AutoFilter", ("Cutoff", "Frequency")),
    # Pass 28: フィルターの向き (0=LP / 1=HP)。中央を跨ぐ
    # 演奏でエンベロープとして書き、片側だけの演奏では出力
    # 側の Manual を合わせる (inject_perf の filter_sides)。
    "filter_type": ("device", "AutoFilter", ("FilterType",)),
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

def _timeable_sample(track):
    """Arrangement の入れ物 (<Sample>) を返す (pass 27 実機確定)。

    オーディオトラック: DeviceChain / MainSequencer / Sample。
    マスター: DeviceChain / FreezeSequencer / AudioSequencer /
    Sample (マスターに MainSequencer は無い)。FreezeSequencer
    直下の Sample はフリーズ用の別物なので踏まない。無ければ
    None — ClipTimeable のような「Live が読まない入れ物」を
    作って成功したことにはしない。"""
    chain = track.find("./DeviceChain") if track is not None else None
    if chain is None:
        return None
    sequencer = chain.find("MainSequencer")
    if sequencer is not None:
        sample = sequencer.find("Sample")
        if sample is not None:
            return sample
    for audio_sequencer in chain.iter("AudioSequencer"):
        sample = audio_sequencer.find("Sample")
        if sample is not None:
            return sample
    return None


def _arranger(track):
    """(Events, Envelopes) を返す。本物の <Sample> の中の
    ArrangerAutomation を使い、Events / Envelopes だけが無け
    れば作る (本物の空の ArrangerAutomation は Events と
    AutomationTransformViewState を持ち、Envelopes はオート
    メーションがある時に Live 自身が足す形)。"""
    sample = _timeable_sample(track)
    if sample is None:
        raise AlsError(
            "トラックに Arrangement の入れ物 (Sample) がありません")
    arranger = sample.find("ArrangerAutomation")
    if arranger is None:
        arranger = ET.SubElement(sample, "ArrangerAutomation")
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

def _scrub_donor_ref(file_ref, audio_file, stat):
    """FileRef を演奏したファイルの参照として一貫させる (pass 27)。

    実ファイルの解剖で確認した問題: 複製元の FileRef には、差し替え
    先とは別ファイル (ドナー) の <Name> が残り、実 Live 10 形式では
    相対パス要素 (RelativePathElement) と <Data> (絶対パスの blob)
    もドナーのファイルのまま残っていた。Path だけ差し替えた参照は
    「名前と中身が食い違う」ので、Live のファイル解決がドナー側に
    引っ張られかねない。ここで:

      - <Name> を演奏ファイル名に合わせる
      - 相対参照 (RelativePath の子要素) を空にし、HasRelativePath
        を false にする — ファイルの同一性は絶対 <Path> だけにする
      - <Data> はドナーの blob を消すのでなく、演奏ファイルの
        絶対パスで書き直す — 実 Live 10 形式は「絶対パスの
        UTF-16LE (+ 終端 NUL) の hex」(実テンプレのドナーで確認)
      - <SearchHint> の子要素 (ドナーのパスの残り) を消す
    """
    abs_path = (str(audio_file.resolve()) if stat is not None
                else str(audio_file))
    _set_value(file_ref, "Path", abs_path)
    _set_value(file_ref, "Name", audio_file.name)
    _set_value(file_ref, "RelativePath", "")
    rel = file_ref.find("RelativePath")
    if rel is not None:
        for child in list(rel):
            rel.remove(child)
    _set_value(file_ref, "HasRelativePath", False)
    data = file_ref.find("Data")
    if data is not None:
        data.text = (abs_path + "\0").encode("utf-16-le").hex().upper()
    hint = file_ref.find("SearchHint")
    if hint is not None:
        for child in list(hint):
            hint.remove(child)
        hint.text = None


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
        _scrub_donor_ref(file_ref, audio_file, stat)
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

def _clear_arrangement(track):
    """トラックの ArrangerAutomation の中身 (クリップとエンベロープ)
    を空にする。取り除いたクリップ数を返す。入れ物が無ければ何も
    しない (作りもしない)。

    掃除先は本物の入れ物 (Sample 配下)。加えて、pass 27 以前の
    書き出しを土台にした場合に残る旧入れ物 (MainSequencer /
    ClipTimeable 配下) も空にする — Live には見えないが、出力の
    Arrangement は「この演奏だけ」という約束をファイル上でも
    守るため。"""
    if track is None:
        return 0
    arrangers = []
    sample = _timeable_sample(track)
    if sample is not None:
        arranger = sample.find("ArrangerAutomation")
        if arranger is not None:
            arrangers.append(arranger)
    chain = track.find("./DeviceChain")
    sequencer = chain.find("MainSequencer") if chain is not None else None
    timeable = sequencer.find("ClipTimeable") \
        if sequencer is not None else None
    if timeable is not None:
        legacy = timeable.find("ArrangerAutomation")
        if legacy is not None:
            arrangers.append(legacy)
    removed = 0
    for arranger in arrangers:
        events = arranger.find("Events")
        if events is not None:
            removed += len([el for el in events
                            if el.tag in ("AudioClip", "MidiClip")])
            for child in list(events):
                events.remove(child)
        envelopes = arranger.find("Envelopes")
        if envelopes is not None:
            for child in list(envelopes):
                envelopes.remove(child)
    return removed


def inject_perf(root, plan) -> dict:
    """パース済み .als の root に演奏プランを注入する。報告 dict。"""
    tracks = als_writer._audio_tracks(root)
    if len(tracks) < 2:
        raise AlsError(
            f"AudioTrack が {len(tracks)} 本しかありません (Deck A / B の 2 本が必要)")
    deck_tracks = {"A": tracks[0], "B": tracks[1]}
    report = {"clips_written": 0, "decks": {"A": 0, "B": 0},
              "envelopes_written": {}, "donor": None, "tempo_set": False,
              "arrangement_cleared": 0,
              "warnings": list(plan.get("warnings") or [])}

    donor = als_writer._find_donor_clip(deck_tracks.values(), root)
    if donor is not None:
        report["donor"] = "clone"
        # Pass 27: 複製元は先に確保してから土台を掃除する (掃除で
        # ツリーから外れても複製元として使えるように)。
        template = copy.deepcopy(donor)
    else:
        report["donor"] = "skeleton"
        report["warnings"].append(
            "ベース .als に既存クリップが無いため、最小スケルトンでクリップを"
            "作りました。この経路は実機未検証です (テンプレートに試しクリップを"
            "1 つ入れて保存し直すと、複製経路になります)")
        template = als_writer._skeleton_clip()

    # Pass 27: 土台 .als 自身の Arrangement に残っているクリップ /
    # エンベロープは演奏ではないので、書き込む前に取り除く (実機の
    # 土台は実セットから保存された物で、前の内容が残り得る)。出力の
    # Arrangement は「この演奏だけ」になる。セッション側のスロット
    # (複製元の置き場) には触らない。
    cleared = 0
    for track in deck_tracks.values():
        cleared += _clear_arrangement(track)
    cleared += _clear_arrangement(_master_track(root))
    report["arrangement_cleared"] = cleared
    if cleared:
        report["warnings"].append(
            f"土台 .als の Arrangement に残っていたクリップ {cleared} 個を"
            "取り除いてから書き出しました")

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

    # Pass 28: a single-sided filter performance needs the
    # OUTPUT device's FilterType to start on the performed side
    # (the Cutoff envelope alone would sweep the template's own
    # type — the real template sits on HP). A performance that
    # crossed the centre carries a FilterType envelope instead,
    # and an untouched filter ('none' / absent) leaves the
    # device exactly as the template has it. Only the exported
    # copy changes; the base file is never written.
    for letter, side in (plan.get("filter_sides") or {}).items():
        if side not in ("lp", "hp"):
            continue
        track = deck_tracks.get(letter)
        param = _param(track, DECK_TARGETS["filter_type"]) \
            if track is not None else None
        if param is None:
            report["warnings"].append(
                f"Deck {letter} の FilterType がテンプレートに無いため、"
                "フィルターの向き (LP / HP) を合わせられませんでした")
            continue
        want = "0" if side == "lp" else "1"
        if _get_value(param, "Manual") != want:
            _set_value(param, "Manual", int(want))
            report["warnings"].append(
                f"Deck {letter} のフィルタータイプを "
                f"{'LP' if side == 'lp' else 'HP'} に合わせました "
                "(演奏のフィルター方向)")

    als_writer._set_tempo(root, plan.get("set_bpm"), report)
    allocator.finish()
    return report


def _track_meta(paths, setlist_data=None, durations=None, bpms=None,
                grids=None):
    """{パス: {'bpm','name','duration_seconds',...}} を組み立てる。

    長さは soundfile で実測 (失敗したら None)、BPM と名前は
    セットリストの該当エントリ (パス完全一致 → ファイル名一致)
    から。呼び出し側が渡した durations / bpms が最優先。
    grids ({パス: {'beat_offset','bar_offset'}}、pass 28) は
    小節合わせ用のグリッド情報で、呼び出し側 (アプリ) が解析
    キャッシュとデッキ上の曲から渡す。
    """
    grids = grids or {}
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
        grid = grids.get(path) or {}
        meta[path] = {"bpm": bpm, "name": name,
                      "duration_seconds": seconds,
                      "beat_offset": grid.get("beat_offset"),
                      "bar_offset": grid.get("bar_offset")}
    return meta


def generate(base_path, events_source, out_path, set_bpm=None,
             setlist_data=None, durations=None, bpms=None,
             grids=None) -> dict:
    """ベース .als + 操作ログ → 演奏 .als。報告 dict を返す。

    grids は {パス: {'beat_offset','bar_offset'}} (pass 28 の
    小節合わせ用)。CLI からは渡らないので、その場合は小節
    合わせなし (従来通り) で書き出す。"""
    events = performance.load_events(events_source)
    paths = []
    for event in events:
        if event.get("action") == "load" and event.get("value") \
                and event["value"] not in paths:
            paths.append(event["value"])
    meta = _track_meta(paths, setlist_data, durations, bpms, grids)
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
        bpms={p: m["bpm"] for p, m in meta.items() if m["bpm"]},
        set_bpm=set_bpm)
    # Pass 27: 再生区間が 1 つも無い記録 (読み込んだだけ / 再起動後
    # のつまみだけ / 試聴だけ / 音声が止まっていて時刻が進まなかっ
    # た) からは、空のセットを黙って書き出さず、理由を言って断る。
    # 実機で「書き出せたのに何も入っていない」が起きた経路。
    if not any(recon["segments"][i] for i in (0, 1)):
        raise AlsError(
            "記録の中に再生した区間が無いため、書き出せる演奏があり"
            "ません。曲を再生してから「Live用に書き出し」を押して"
            "ください (読み込みだけ・試聴だけでは演奏になりません)")
    plan = performance.build_perf_plan(recon, set_bpm, meta)
    tree = als_writer.load_als(base_path)
    report = inject_perf(tree.getroot(), plan)
    als_writer.write_als(tree, out_path)
    report["segments"] = {letter: len(recon["segments"][i])
                          for i, letter in enumerate("AB")}
    # Pass 28 (user workflow 2026-11-28): he deletes the app's
    # mix record when he moves over to Live, so an export would
    # outlive the only record of the performance it came from.
    # Save a copy of the journal next to the .als — same folder,
    # output name + '-events.json' — so the pair always survives
    # together. 操作保存's behaviour is unchanged; this is an
    # additional copy made by the export itself.
    report["events_copy"] = _save_events_copy(events, out_path)
    return report


def _events_copy_path(out_path):
    p = Path(out_path)
    if p.suffix.lower() == ".als":
        return str(p.with_name(p.name[:-4] + "-events.json"))
    return str(p) + "-events.json"


def _save_events_copy(events, out_path):
    """書き出しと対になるジャーナルのコピーを保存し、その
    パスを返す。失敗しても書き出し自体は成功扱い (None)。"""
    try:
        target = _events_copy_path(out_path)
        Path(target).write_text(json.dumps(
            {"schema": 1, "sample_rate": performance.RATE,
             "events": events},
            ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        return target
    except Exception:
        return None


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
