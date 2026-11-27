#!/usr/bin/env python3
"""演奏データの再構成 (MixCraft DJ Studio / MixAble).

ユーザー方向 (2026-11-27): 「アプリで完結 Liveで修整が理想」+
「録音は難しければLiveで書き出すよ 演奏のオートメーションデータ
などがしっかり残せるなら」。つまり本番はアプリで演奏し、その
演奏を Ableton Live で開いて直せる形に書き出す。その土台が
ここ: エンジンの操作ログ (Journal が自動保存するイベント列)
から、演奏を再構成する。

イベントの意味は engine.Engine._apply と対で読むこと。再構成は
エンジンの状態遷移をそのままなぞる:

  - 位置は再生中だけ rate 倍速で進む。ループ有効中は
    [loop_start, loop_end) で巻き戻る。曲の終わりに達したら
    再生は止まる (エンジンと同じ)。
  - seek / cue / hotcue のイベント値は「適用前の要求値」で、
    cue_set 等は位置そのものを含まない。だから再構成側も
    キューポイント等を全部自前で追跡する (エンジンと同じ規則)。
  - sync の rate はイベントに残らないので、両デッキの BPM が
    分かればエンジンと同じ式で再計算する。分からなければ警告。
  - preview (ミックス試聴) の間は演奏ではないので区間を除外し、
    終了時に試聴前の状態へ復元する (エンジンの復元と同じ)。
    試聴の終了イベントは無いので、開始フレーム + 秒数×RATE で
    終わりを決める (stop_preview が来ればそこで終わる)。

出力は (a) デッキごとの再生セグメント (どの曲を、いつから、
曲内のどこから、どの rate で) と、(b) コマンド値域のままの
オートメーション系列 (cross / master / 各デッキの volume /
gain / eq_low / eq_mid / eq_high / filter)。Live のパラメータ
値域への変換は build_perf_plan が担う。

既知の近似 (正直に書く):
  - クオンタイズ ON 時の拍吸着は再現しない (seek 等の位置が
    最大で半拍ずれることがある)。
  - イベントのフレームはエンジンがコマンドを適用したオーディオ
    ブロックの先頭。ブロック内 (512 フレーム ≒ 11 ms) の時刻は
    丸められる。
"""
from __future__ import annotations

import math
from pathlib import Path

from engine import RATE

# コマンド値域の初期値 (エンジンの _new_deck / Engine.__init__
# と一致させること。再構成はこの状態からイベントを再生する)
PARAM_DEFAULTS = {
    "volume": 0.8, "gain": 1.0,
    "eq_low": 0.5, "eq_mid": 0.5, "eq_high": 0.5, "filter": 0.5,
}
GLOBAL_DEFAULTS = {"cross": 0.5, "master": 0.8}
DECK_PARAMS = tuple(PARAM_DEFAULTS)


def load_events(source):
    """操作ログをイベント列にする。

    受ける形: 操作保存が書き出す dict (schema 1: {'events': [...]})、
    イベント dict のリストそのもの、またはその JSON ファイルのパス。
    """
    if isinstance(source, (str, Path)):
        import json
        source = json.loads(Path(source).read_text(encoding="utf-8"))
    if isinstance(source, dict):
        return list(source.get("events") or [])
    return list(source)


def _new_state():
    # 'at' は「position がその値だったフレーム」(デッキごとの時計)
    return dict(track=None, duration=None, position=0.0, at=0.0,
                playing=False, rate=1.0, cue=0.0, hotcues={},
                loop_start=None, loop_end=None, loop_active=False,
                params=dict(PARAM_DEFAULTS))


class _Reconstructor:
    def __init__(self, durations, bpms):
        self.durations = durations or {}   # path -> seconds
        self.bpms = bpms or {}             # path -> bpm
        self.decks = [_new_state(), _new_state()]
        self.globals = dict(GLOBAL_DEFAULTS)
        self.series = {"cross": [(0, GLOBAL_DEFAULTS["cross"])],
                       "master": [(0, GLOBAL_DEFAULTS["master"])]}
        for i in range(2):
            for key in DECK_PARAMS:
                self.series[(i, key)] = [(0, PARAM_DEFAULTS[key])]
        self.segments = {0: [], 1: []}
        self.open_seg = {0: None, 1: None}
        self.warnings = []
        self.now = 0.0                     # 最後に処理したフレーム
        self.preview = None                # (snapshot, end_frame)

    # -- セグメント管理 ------------------------------------------------
    def _close_seg(self, i, frame):
        seg = self.open_seg[i]
        if seg is not None:
            seg["end_frame"] = float(frame)
            if seg["end_frame"] - seg["start_frame"] > 1:
                self.segments[i].append(seg)
            self.open_seg[i] = None

    def _sync_seg(self, i, frame):
        """再生状態と開いているセグメントを一致させる。位置の
        飛び (seek 等) や rate 変化はセグメントを切り直す。"""
        d = self.decks[i]
        seg = self.open_seg[i]
        want = d["playing"] and d["track"] is not None \
            and self.preview is None
        if seg is not None:
            implied = seg["offset_frames"] \
                + (frame - seg["start_frame"]) * seg["rate"]
            if not want or seg["rate"] != d["rate"] \
                    or abs(implied - d["position"]) > 1.5:
                self._close_seg(i, frame)
                seg = None
        if want and seg is None:
            self.open_seg[i] = dict(
                track=d["track"], start_frame=float(frame),
                offset_frames=float(d["position"]), rate=d["rate"])

    # -- 時間を進める --------------------------------------------------
    def _advance_deck(self, i, to_frame):
        d = self.decks[i]
        while d["playing"] and d["track"] is not None \
                and d["at"] < to_frame:
            rate = d["rate"]
            step_end = float(to_frame)
            hit = None
            if d["loop_active"] and d["loop_end"] is not None \
                    and d["loop_start"] is not None \
                    and d["loop_end"] > d["loop_start"]:
                wrap_at = d["at"] + (d["loop_end"] - d["position"]) / rate
                if wrap_at < step_end:
                    step_end, hit = wrap_at, "wrap"
            if d["duration"] is not None:
                end_at = d["at"] + (d["duration"] - d["position"]) / rate
                if end_at < step_end:
                    step_end, hit = end_at, "end"
            step_end = max(step_end, d["at"])
            d["position"] += (step_end - d["at"]) * rate
            d["at"] = step_end
            if hit == "wrap":
                self._close_seg(i, step_end)
                d["position"] = float(d["loop_start"])
                self._sync_seg(i, step_end)
            elif hit == "end":
                d["position"] = float(d["duration"])
                d["playing"] = False
                self._close_seg(i, step_end)
            elif step_end <= d["at"]:
                break
        d["at"] = float(to_frame)

    def _advance(self, to_frame):
        if self.preview is not None:
            for d in self.decks:
                d["at"] = float(to_frame)
        else:
            for i in range(2):
                self._advance_deck(i, to_frame)
        self.now = float(to_frame)

    # -- イベント適用 --------------------------------------------------
    def _record(self, key, frame, value):
        series = self.series[key]
        if series and series[-1][1] == value:
            return
        series.append((int(frame), value))

    def _apply_event(self, event):
        action = event.get("action")
        index = event.get("deck")
        value = event.get("value")
        frame = float(event.get("frame") or 0)
        if self.preview is not None:
            if action == "stop_preview":
                self._advance(frame)
                self._end_preview(frame)
                return
            if frame >= self.preview[1]:
                self._end_preview(self.preview[1])
                # このイベントは通常通り適用する (下へ続く)
            else:
                self.warnings.append(
                    f"試聴中の操作イベントは再構成から除きました: {action}")
                return
        self._advance(frame)
        d = self.decks[index] if index in (0, 1) else None
        if action == "load" and d is not None:
            self._close_seg(index, frame)
            keep = dict(d["params"])
            fresh = _new_state()
            fresh["params"] = keep
            fresh["track"] = value
            fresh["at"] = frame
            seconds = self.durations.get(value)
            fresh["duration"] = seconds * RATE if seconds else None
            if seconds is None:
                self.warnings.append(
                    f"曲の長さが不明です (終端検出なし): {value}")
            self.decks[index] = fresh
            d = fresh
        elif action == "play" and d is not None:
            if d["track"] is not None:
                if d["duration"] is not None \
                        and d["position"] >= d["duration"]:
                    d["position"] = 0.0
                d["playing"] = bool(value)
        elif action == "seek" and d is not None:
            if d["track"] is not None:
                pos = float(value or 0)
                if d["duration"] is not None:
                    pos = min(max(pos, 0.0), d["duration"])
                d["position"] = pos
        elif action in DECK_PARAMS and d is not None:
            d["params"][action] = float(value)
            self._record((index, action), frame, float(value))
        elif action in GLOBAL_DEFAULTS:
            self.globals[action] = float(value)
            self._record(action, frame, float(value))
        elif action == "cue_set" and d is not None:
            d["cue"] = d["position"]
        elif action == "cue_jump" and d is not None:
            if d["track"] is not None:
                d["position"] = d["cue"]
        elif action == "hotcue_set" and d is not None:
            if d["track"] is not None:
                d["hotcues"][int(value)] = d["position"]
        elif action == "hotcue_jump" and d is not None:
            if int(value) in d["hotcues"]:
                d["position"] = d["hotcues"][int(value)]
        elif action == "loop_in" and d is not None:
            d["loop_active"] = False
            d["loop_start"] = d["position"]
        elif action == "loop_out" and d is not None:
            d["loop_end"] = d["position"]
            d["loop_active"] = d["loop_start"] is not None \
                and d["loop_end"] > d["loop_start"]
        elif action == "loop_exit" and d is not None:
            d["loop_active"] = False
        elif action == "tempo" and d is not None:
            d["rate"] = min(2.0, max(0.5, float(value)))
        elif action == "sync" and d is not None:
            other = self.decks[1 - index]
            if value and d["track"] and other["track"]:
                own_bpm = self.bpms.get(d["track"])
                other_bpm = self.bpms.get(other["track"])
                if own_bpm and other_bpm:
                    d["rate"] = min(2.0, max(
                        0.5, other_bpm * other["rate"] / own_bpm))
                else:
                    self.warnings.append(
                        "SYNC の rate を再計算できません (BPM 不明)")
            elif not value:
                d["rate"] = 1.0
        elif action == "preview":
            self._start_preview(frame, value)
            return  # セグメントは閉じた。sync は試聴終了時に行う
        elif action == "stop_preview":
            pass  # preview 中でなければ何も起きない (エンジンと同じ)
        for i in range(2):
            self._sync_seg(i, frame)

    def _start_preview(self, frame, seconds):
        for i in range(2):
            self._close_seg(i, frame)
        snapshot = [dict(track=d["track"], duration=d["duration"],
                         position=d["position"], playing=d["playing"],
                         rate=d["rate"], cue=d["cue"],
                         hotcues=dict(d["hotcues"]),
                         loop_start=d["loop_start"],
                         loop_end=d["loop_end"],
                         loop_active=d["loop_active"])
                    for d in self.decks]
        self.preview = (snapshot, frame + float(seconds or 0) * RATE)

    def _end_preview(self, frame):
        snapshot, _ = self.preview
        self.preview = None
        for i, saved in enumerate(snapshot):
            self.decks[i].update(saved)
            self.decks[i]["at"] = float(frame)
        self.now = float(frame)
        for i in range(2):
            self._sync_seg(i, frame)

    def run(self, events):
        events = sorted((e for e in events if e.get("action")),
                        key=lambda e: e.get("frame") or 0)
        for event in events:
            self._apply_event(event)
        if self.preview is not None:
            self._end_preview(self.preview[1])
        last = float(events[-1].get("frame") or 0) if events else 0.0
        self._advance(max(last, self.now))
        for i in range(2):
            self._close_seg(i, self.now)
        automation = {"cross": self.series["cross"],
                      "master": self.series["master"]}
        for i in range(2):
            automation[i] = {key: self.series[(i, key)]
                             for key in DECK_PARAMS}
        return dict(segments=self.segments, automation=automation,
                    warnings=self.warnings,
                    end_frame=float(self.now))


def reconstruct(events, durations=None, bpms=None):
    """イベント列 → dict(segments, automation, warnings, end_frame)。

    durations / bpms は {ファイルパス: 秒 / BPM}。segments は
    デッキ番号 (0/1) → [{track, start_frame, end_frame,
    offset_frames, rate}]、automation はコマンド値域の
    [(frame, value), ...] 系列 (系列は (0, 初期値) から始まる)。
    """
    recon = _Reconstructor(durations, bpms)
    return recon.run(load_events(events))


# ---------------------------------------------------------------------------
# Live の値域への変換と書き出しプラン
# ---------------------------------------------------------------------------

def _db(linear, floor=-60.0, ceil=24.0):
    """線形ゲイン → dB (Live の Utility / EQ Three の値域)。"""
    if linear <= 0:
        return floor
    return min(ceil, max(floor, 20.0 * math.log10(linear)))


def _step_value(series, frame):
    """階段系列の frame 時点の値 (最後の変更値を持続)。"""
    value = series[0][1]
    for at, v in series:
        if at > frame:
            break
        value = v
    return value


def _to_beats(series, set_bpm, end_frame):
    """(frame, value) 系列 → (beats, value) 点列。末尾は演奏の
    終わりまで最後の値を持続させる点を足す。"""
    points = []
    for frame, value in series:
        beats = frame / RATE * set_bpm / 60.0
        if points and points[-1][1] == value:
            continue
        points.append((beats, value))
    total = end_frame / RATE * set_bpm / 60.0
    if points and points[-1][0] < total:
        points.append((total, points[-1][1]))
    return points


def build_perf_plan(recon, set_bpm, track_meta=None):
    """再構成結果 → perf_als が注入できるプラン。

    track_meta: {パス: {'bpm': float|None, 'name': str|None,
    'duration_seconds': float|None}}。

    変換の約束事 (マニュアルにも明記する):
      - チャンネル音量のエンベロープにはクロスフェーダーを
        焼き込む (Live のクロスフェーダー自体には Arrangement
        オートメーションが無いため)。焼き込みはエンジンと同じ
        等パワー (cos / sin) カーブ。
      - GAIN は Utility の Gain (dB)、EQ は EQ Three の各帯域
        (dB, ±15 でクリップ)、FILTER は Auto Filter の
        Frequency (0..1 正規化位置) に写す。
      - テンポは SET BPM 一定の線 (このアプリの演奏規則)。
      - クリップは演奏した時刻 (SET BPM 換算の拍) に置く。
        デッキの rate × 曲 BPM が SET BPM と違う区間は、Live
        上のクリップ長が演奏時間とずれる (警告を出す)。
    """
    track_meta = track_meta or {}
    warnings = list(recon["warnings"])
    end_frame = recon["end_frame"]
    auto = recon["automation"]

    # クロスフェーダーを焼き込んだチャンネル音量系列
    cross = auto["cross"]
    envelopes = {}
    for i in range(2):
        volume = auto[i]["volume"]
        frames = sorted({f for f, _ in volume} | {f for f, _ in cross})
        baked = []
        for frame in frames:
            gain = _step_value(volume, frame)
            x = _step_value(cross, frame)
            fade = math.cos(x * math.pi / 2) if i == 0 \
                else math.sin(x * math.pi / 2)
            baked.append((frame, gain * fade))
        letter = "AB"[i]
        envelopes[f"volume_{letter}"] = _to_beats(baked, set_bpm, end_frame)
        envelopes[f"gain_{letter}"] = _to_beats(
            [(f, _db(v)) for f, v in auto[i]["gain"]], set_bpm, end_frame)
        for band in ("eq_low", "eq_mid", "eq_high"):
            envelopes[f"{band}_{letter}"] = _to_beats(
                [(f, _db(2.0 * v, floor=-15.0, ceil=15.0))
                 for f, v in auto[i][band]], set_bpm, end_frame)
        envelopes[f"filter_{letter}"] = _to_beats(
            auto[i]["filter"], set_bpm, end_frame)
    total_beats = end_frame / RATE * set_bpm / 60.0
    envelopes["tempo"] = [(0.0, float(set_bpm)),
                          (total_beats, float(set_bpm))] \
        if total_beats > 0 else [(0.0, float(set_bpm))]

    clips = {"A": [], "B": []}
    for i in range(2):
        for seg in recon["segments"][i]:
            meta = track_meta.get(seg["track"]) or {}
            bpm = meta.get("bpm") or set_bpm
            if not meta.get("bpm"):
                warnings.append(
                    f"{seg['track']}: BPM 不明のため SET BPM で Warp します")
            start_s = seg["start_frame"] / RATE
            span_s = (seg["end_frame"] - seg["start_frame"]) / RATE
            offset_s = seg["offset_frames"] / RATE
            clips["AB"[i]].append({
                "file": seg["track"],
                "name": meta.get("name") or Path(seg["track"]).stem,
                "time_beats": start_s * set_bpm / 60.0,
                "offset_beats": offset_s * bpm / 60.0,
                "end_beats": (offset_s + span_s * seg["rate"]) * bpm / 60.0,
                "duration_seconds": meta.get("duration_seconds"),
                "bpm": bpm,
                "rate": seg["rate"],
            })
            if abs(seg["rate"] * bpm - set_bpm) > 0.5:
                warnings.append(
                    f"{seg['track']}: デッキ rate × 曲 BPM が SET BPM と"
                    "違うため、Live 上のクリップ長が演奏時間とずれます")
    return dict(set_bpm=set_bpm, total_beats=total_beats, clips=clips,
                envelopes=envelopes, warnings=warnings)
