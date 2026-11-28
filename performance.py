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
  - pass 26: SYNC の拍位相合わせ (位置のシフト) は、エンジンが
    'sync_align' イベントとして絶対位置で記録する。再構成は
    それをそのまま適用するので、位相まで含めて正確に再現する
    (再導出はしない)。
  - pass 26: マスター SET BPM の移動は 'set_bpm' イベント
    (絶対 BPM) として残り、追随する各デッキの rate は通常の
    'tempo' イベントとして記録される。再構成は set_bpm 系列を
    保持し、プランの Master Tempo エンベロープに反映する。
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

pass 27 (実機の書き出しファイルの解剖から):
  - 位相ロックの微調整 (sync_align、ドリフト 0.03 拍超で発火) が
    1 回ごとにセグメントを切っていたため、2 曲の演奏が約 100 個の
    薄片クリップになっていた (実ファイルで確認)。対策は 2 層:
    (1) ABSORB_BEATS (0.1 拍) 未満の sync_align は「位相補正級」と
    みなしてセグメントを切らず、開いているセグメントの基準位置を
    ずらして吸収する。根拠: ロック補正は閾値 0.03 拍の少し先で
    発火するので本物の補正はほぼ 0.1 拍未満に収まる一方、音楽的に
    意味のあるジャンプ (16 分音符 = 0.25 拍) には届かない。意図的な
    seek / cue ジャンプは sync_align ではないので一切吸収しない。
    (2) build_perf_plan で、同一トラック・同一ファイルで時間的に
    隙間なく隣接し、位置が連続する断片を 1 クリップに結合する
    (レート変化で切れた断片など、吸収をすり抜けた微小な分割の
    受け皿)。位置が閾値以上飛ぶ断片は結合しないので、ジョグの
    意図的な seek はクリップ境界のまま残る。
  - セグメントは閉じる時点のデッキ位置 (end_offset_frames) を
    保持し、クリップの終端はそれで決める。吸収した微小ジャンプの
    累積があっても、クリップの終端は演奏した実位置と一致する。

pass 28 (実機の書き出しファイルの解剖から):
  - エンベロープの値域が Live のパラメータの素の値域と食い違って
    いた。実テンプレの Manual 値が証拠: Utility Gain と EQ Three
    の各帯域は「振幅」(1.0 = 0 dB、EQ の 0.000316 = -70 dB)、
    Auto Filter の Cutoff は Hz で保存されるのに、書き出しは
    GAIN / EQ を dB の数字のまま、FILTER をノブ位置 (0..1) の
    まま書いていた。Live から見ると GAIN 中立 = 振幅 0 (無音)、
    EQ カット = 負の振幅、FILTER = 最小値張り付き — 「オート
    メーションが反映されてない」の正体。変換を素の値域に統一
    した (GAIN はエンジンの線形倍率そのまま、EQ は帯域倍率
    2×UI 値、FILTER はエンジン DSP と同じ式で Hz 化し、中央を
    跨ぐ演奏には FilterType のエンベロープも併記する)。
  - 'jog' イベント (pass 28 で新設、エンジンがデルタで記録) は
    seek と同じ位置ジャンプとして再生する。微小な jog の嵐は
    pass 27 の断片結合が受ける。
  - 小節合わせ: 最初のセグメントの曲のダウンビートが Live の
    小節線 (4 拍ごと) に乗るよう、タイムライン全体を Δ 拍
    (0 ≤ Δ < 4) ずらす。Δ は最初の曲の beat_offset (拍グリッド
    原点) と bar_offset (小節頭の拍、0..3) から決める。グリッド
    情報が無い曲では Δ = 0 (従来通りセグメント開始 = 0 拍)。

pass 29 (ユーザーの実ジャーナルでの検証、2026-11-28):
  - 実セッションのジャーナル (1448 イベント) をそのまま再生
    して確定した欠陥: デッキ 0 のスクラッチ (DDJ-RB のジョグ
    ±3024 フレーム ×351) が tick ごとにセグメントを切り、
    1 デッキ 50 クリップ (大半が 1 拍未満の薄片で、Live 上の
    時間範囲が互いに重なる) になっていた。ジョグは大きさに
    よらず連続操作なので、replay では常にセグメントを切らず
    再アンカーする (seek 等の意図的な編集だけが境界のまま)。
    逆行して終わるセグメント (スピンバック) は、終端位置が
    開始位置以下なら「経過時間分を真っ直ぐ再生した長さ」に
    フォールバックしてクリップの形を保つ。
  - 実ジャーナルには stop / pause イベントが一切無い (再生
    しっぱなしで書き出し)。セグメントの終端は従来通り
    ジャーナルの最終フレームで閉じる — 実データの形に合わせ
    たこの規則を tests/test_pass29_journal_replay.py が固定
    する。
"""
from __future__ import annotations

import math
from pathlib import Path

from engine import RATE

# コマンド値域の初期値 (エンジンの _new_deck / Engine.__init__
# と一致させること。再構成はこの状態からイベントを再生する)
# Pass 28: volume / master の初期値は 1.0 (ユニティ)。かつて
# 0.8 始まりで、チャンネル (-1.9 dB) とマスター (-1.9 dB) の
# 減衰が積み重なって「音が小さい」一因だった (実測は
# tests/test_pass28_gain_chain.py)。
PARAM_DEFAULTS = {
    "volume": 1.0, "gain": 1.0,
    "eq_low": 0.5, "eq_mid": 0.5, "eq_high": 0.5, "filter": 0.5,
}
GLOBAL_DEFAULTS = {"cross": 0.5, "master": 1.0}
DECK_PARAMS = tuple(PARAM_DEFAULTS)

# Pass 27: sync_align のうち、この拍数未満のジャンプは位相ロックの
# 微調整とみなしてセグメント境界にしない (根拠はモジュール冒頭)。
# クリップ結合時の位置連続性の閾値にも同じ値を使う。
ABSORB_BEATS = 0.1


def _absorb_frames(bpm):
    """ABSORB_BEATS をフレーム数にする。BPM 不明なら 0 (吸収しない)。"""
    if not bpm:
        return 0.0
    return ABSORB_BEATS * RATE * 60.0 / float(bpm)


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
    def __init__(self, durations, bpms, set_bpm=None):
        self.durations = durations or {}   # path -> seconds
        self.bpms = bpms or {}             # path -> bpm
        self.set_bpm = float(set_bpm) if set_bpm else None
        # Pass 26: the master SET BPM series, [(frame, bpm)] —
        # seeded with the set's base tempo and extended by every
        # 'set_bpm' journal event. Feeds the plan's Master Tempo
        # envelope; empty when the base is unknown.
        self.set_bpm_series = [(0.0, float(set_bpm))] if set_bpm else []
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
    def _close_seg(self, i, frame, end_pos=None):
        seg = self.open_seg[i]
        if seg is not None:
            seg["end_frame"] = float(frame)
            # Pass 27: 閉じる瞬間の実位置も残す。吸収した位相補正の
            # 累積があると (経過フレーム × rate) からは終端がずれ
            # るので、クリップの終端はこちらで決める。ただし位置の
            # 飛び (seek / 大きな align) で切る場合は、飛ぶ前の到達
            # 位置 (end_pos = 予測位置) が終端 — 飛んだ先を終端に
            # すると前後のセグメントが「位置連続」に見えて結合
            # されてしまう。
            seg["end_offset_frames"] = float(
                self.decks[i]["position"] if end_pos is None
                else end_pos)
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
            # Pass 27: 位置の予測はセグメントの「基準」から。基準は
            # 開いた時点の位置で、吸収した sync_align のたびに現在
            # 位置へ付け替わる (offset_frames はクリップ開始位置と
            # して不変に保つ)。
            implied = seg["anchor_frames"] \
                + (frame - seg["anchor_frame"]) * seg["rate"]
            if not want or seg["rate"] != d["rate"] \
                    or abs(implied - d["position"]) > 1.5:
                self._close_seg(i, frame, end_pos=implied)
                seg = None
        if want and seg is None:
            self.open_seg[i] = dict(
                track=d["track"], start_frame=float(frame),
                offset_frames=float(d["position"]), rate=d["rate"],
                anchor_frames=float(d["position"]),
                anchor_frame=float(frame))

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
        elif action == "jog" and d is not None:
            # Pass 28: a jog DELTA in frames (the engine applies
            # it to its live position with no quantize snap).
            # Replay applies the same delta to the reconstructed
            # position.
            # Pass 29 (the user's REAL journal, 2026-11-28): a
            # jog is a continuous-control gesture at ANY size —
            # the real session's scratch passage was 351 hardware
            # ticks of ±3024 frames each, far beyond the absorb
            # window, and replay cut a segment at every tick:
            # 50 clips for one deck, 45 of them sub-beat slivers
            # whose Live-time spans overlap each other (clip
            # length comes from the offset span, start times are
            # dense), and the deck's second half simply did not
            # survive into a readable set. So jog now ALWAYS
            # re-anchors the open segment (the sync_align
            # treatment), never cuts: one continuous play of a
            # track is one segment, and the clip's end position
            # is the deck's real position when the segment closes
            # (pass 27's end_offset_frames), so the performed
            # travel — scratches included — is carried by the
            # clip's span. Deliberate edits (seek / cue_jump /
            # hotcue_jump) still cut, exactly as before.
            if d["track"] is not None:
                pos = d["position"] + float(value or 0)
                if d["duration"] is not None:
                    pos = min(max(pos, 0.0), d["duration"])
                d["position"] = pos
                seg = self.open_seg[index]
                if seg is not None:
                    seg["anchor_frames"] = float(pos)
                    seg["anchor_frame"] = float(frame)
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
        elif action == "sync_align" and d is not None:
            # Pass 26: the engine's phase alignment, recorded as
            # an absolute position — apply it verbatim.
            # Pass 27: a sub-ABSORB_BEATS jump is a phase-lock
            # micro-correction, not a musical edit — re-anchor the
            # open segment onto the new position instead of cutting
            # it (the real-machine export had ~100 sliver clips
            # from exactly this). A bigger align still cuts below.
            if d["track"] is not None:
                pos = float(value or 0)
                if d["duration"] is not None:
                    pos = min(max(pos, 0.0), d["duration"])
                shift = pos - d["position"]
                d["position"] = pos
                seg = self.open_seg[index]
                if seg is not None and abs(shift) <= _absorb_frames(
                        self.bpms.get(d["track"]) or self.set_bpm):
                    seg["anchor_frames"] = float(pos)
                    seg["anchor_frame"] = float(frame)
        elif action == "set_bpm":
            # Pass 26: a master tempo move. The deck rates arrive
            # as their own 'tempo' events right after this one,
            # so here only the series is extended.
            if value:
                bpm = float(value)
                if not self.set_bpm_series \
                        or self.set_bpm_series[-1][1] != bpm:
                    self.set_bpm_series.append((frame, bpm))
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

    def _rebase(self):
        """タイムラインを「最初の再生セグメント開始 = 0」にずらす
        (pass 27)。ジャーナルの frame 0 はエンジン起動時点なので、
        そのまま書き出すと準備時間 (読み込みや、最初の再生までの
        静寂) がセット先頭の無音になり、開いた先頭が空に見える。
        オートメーションとテンポ列も同じだけずらし、新しい 0 の
        位置の値を種点として残す (最初の音の時点のノブ位置)。"""
        starts = [s["start_frame"]
                  for segs in self.segments.values() for s in segs]
        if not starts:
            return
        shift = min(starts)
        if shift <= 0:
            return
        for segs in self.segments.values():
            for seg in segs:
                seg["start_frame"] -= shift
                seg["end_frame"] -= shift

        def shifted(series):
            if not series:
                return series
            out = [(0, _step_value(series, shift))]
            out.extend((f - shift, v) for f, v in series if f > shift)
            return out

        for key in self.series:
            self.series[key] = shifted(self.series[key])
        self.set_bpm_series = shifted(self.set_bpm_series)
        self.now -= shift

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
        self._rebase()
        automation = {"cross": self.series["cross"],
                      "master": self.series["master"]}
        for i in range(2):
            automation[i] = {key: self.series[(i, key)]
                             for key in DECK_PARAMS}
        return dict(segments=self.segments, automation=automation,
                    warnings=self.warnings,
                    end_frame=float(self.now),
                    set_bpm_series=self.set_bpm_series)


def reconstruct(events, durations=None, bpms=None, set_bpm=None):
    """イベント列 → dict(segments, automation, warnings, end_frame,
    set_bpm_series)。

    durations / bpms は {ファイルパス: 秒 / BPM}。segments は
    デッキ番号 (0/1) → [{track, start_frame, end_frame,
    offset_frames, rate}]、automation はコマンド値域の
    [(frame, value), ...] 系列 (系列は (0, 初期値) から始まる)。
    set_bpm はセットの基準テンポで、set_bpm_series の種になる。
    """
    recon = _Reconstructor(durations, bpms, set_bpm)
    return recon.run(load_events(events))


# ---------------------------------------------------------------------------
# Live の値域への変換と書き出しプラン
# ---------------------------------------------------------------------------

def _filter_hz(value):
    """エンジンの FILTER 位置 (0..1、0.5 = 全開) → Auto Filter の
    Cutoff (Hz)。エンジン DSP (engine._process_deck) と同じ式:
    中央より左はローパスで 100 Hz × 200^(2v) (0.5 で 20 kHz =
    実質全開)、右はハイパスで 20 Hz × 200^(2(v-0.5)) (0.5 で
    20 Hz = 実質全開、1.0 で 4 kHz)。pass 28 で Hz 化した —
    以前はノブ位置をそのまま書いており、Live には最小値に
    張り付いて見えた。"""
    v = min(1.0, max(0.0, float(value)))
    if v <= 0.5:
        return 100.0 * 200.0 ** (2.0 * v)
    return 20.0 * 200.0 ** (2.0 * (v - 0.5))


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


def _coalesce_segments(segments, bpms, set_bpm):
    """Pass 27: 断片の結合。同一トラックで、時間的に隙間なく隣接し
    (前の終端フレームと次の開始フレームがほぼ一致)、位置が連続する
    (次の開始位置が前の終端位置から ABSORB_BEATS 以内) セグメント
    を 1 つにまとめる。位相補正の吸収をすり抜けた微小な分割や、
    レート変化 (SET BPM 移動) で切れた断片の受け皿。位置が閾値
    以上飛ぶもの — ジョグの意図的な seek やキュージャンプ — は
    結合しないのでクリップ境界のまま残る。"""
    merged = []
    for seg in segments:
        if merged:
            prev = merged[-1]
            bpm = bpms.get(seg["track"]) or set_bpm
            prev_end_pos = prev.get("end_offset_frames")
            if prev_end_pos is None:
                prev_end_pos = prev["offset_frames"] \
                    + (prev["end_frame"] - prev["start_frame"]) \
                    * prev["rate"]
            if seg["track"] == prev["track"] \
                    and seg["start_frame"] - prev["end_frame"] <= 1.5 \
                    and abs(seg["offset_frames"] - prev_end_pos) \
                    <= _absorb_frames(bpm):
                prev["end_frame"] = seg["end_frame"]
                if seg.get("end_offset_frames") is not None:
                    prev["end_offset_frames"] = seg["end_offset_frames"]
                else:
                    prev.pop("end_offset_frames", None)
                continue
        merged.append(dict(seg))
    return merged


def build_perf_plan(recon, set_bpm, track_meta=None):
    """再構成結果 → perf_als が注入できるプラン。

    track_meta: {パス: {'bpm': float|None, 'name': str|None,
    'duration_seconds': float|None, 'beat_offset': int|None
    (拍グリッド原点のフレーム), 'bar_offset': int (小節頭の
    拍 0..3)}}。beat_offset / bar_offset は小節合わせ用で、
    無い曲は合わせない。

    変換の約束事 (マニュアルにも明記する):
      - チャンネル音量のエンベロープにはクロスフェーダーを
        焼き込む (Live のクロスフェーダー自体には Arrangement
        オートメーションが無いため)。焼き込みはエンジンと同じ
        等パワー (cos / sin) カーブ。値は Live の素の値域 =
        振幅 (1.0 = 0 dB) そのまま。
      - GAIN は Utility の Gain へ、エンジンの線形倍率 (0..2)
        をそのまま書く (Live の素の値域も振幅 — 実テンプレの
        Manual 1.17 が証拠。pass 27 までは dB の数字を書いて
        いて、中立が振幅 0 = 無音になっていた)。
      - EQ は EQ Three の各帯域へ、帯域倍率 (UI 値 ×2、0.5 で
        1.0 = 0 dB) を振幅として書く (実テンプレの Manual
        0.000316 = -70 dB が証拠)。
      - FILTER は Auto Filter の Cutoff へ Hz で書く (エンジン
        DSP と同じ式)。中央を跨ぐ演奏は FilterType のエンベ
        ロープ (0=LP / 1=HP) も併記し、片側だけの演奏は
        plan['filter_sides'] で perf_als に伝え、出力側の
        FilterType 初期値をそちらへ合わせる。一度も触らな
        かったデッキには FILTER のエンベロープ自体を書かない
        (テンプレの状態を保存する)。
      - テンポは SET BPM が基準。演奏中にマスター SET BPM を
        動かした場合は、その移動が階段状にエンベロープへ入る
        (拍位置は基準 SET BPM のグリッドで換算する)。
      - クリップは演奏した時刻 (SET BPM 換算の拍) に置く。
        デッキの rate × 曲 BPM が SET BPM と違う区間は、Live
        上のクリップ長が演奏時間とずれる (警告を出す)。
      - 小節合わせ (pass 28): 最初のクリップの曲のダウン
        ビートが Live の小節線に乗るよう、クリップと全エン
        ベロープを一律に Δ 拍ずらす (相対配置は不変、ドリ
        フトなし)。最初の曲のグリッド情報 (beat_offset /
        bar_offset) が無い場合はずらさない。
    """
    track_meta = track_meta or {}
    warnings = list(recon["warnings"])
    end_frame = recon["end_frame"]
    auto = recon["automation"]

    # クロスフェーダーを焼き込んだチャンネル音量系列
    cross = auto["cross"]
    envelopes = {}
    filter_sides = {}
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
        # Pass 28: Live's plain domain for Utility Gain and the
        # EQ Three bands is AMPLITUDE (see the module docstring's
        # evidence), and the engine's own values already ARE
        # amplitudes — the gain multiplier itself, and 2x the UI
        # value for a band (the engine multiplies the band by
        # exactly that). Write them unchanged; the old dB numbers
        # read as silence / invalid values in Live.
        envelopes[f"gain_{letter}"] = _to_beats(
            auto[i]["gain"], set_bpm, end_frame)
        for band in ("eq_low", "eq_mid", "eq_high"):
            envelopes[f"{band}_{letter}"] = _to_beats(
                [(f, 2.0 * v) for f, v in auto[i][band]],
                set_bpm, end_frame)
        # Pass 28: FILTER — Hz for Cutoff, a FilterType step
        # envelope only when the performance ENGAGES both sides,
        # and nothing at all when the knob was never touched.
        # The centre value 0.5 engages NEITHER side (the engine
        # is dry there), so a centre -> HP performance is a
        # single-sided 'hp', not a crossing.
        raw_filter = auto[i]["filter"]
        engaged = set()
        for _, v in raw_filter:
            if v < 0.5:
                engaged.add("lp")
            elif v > 0.5:
                engaged.add("hp")
        if not engaged:
            filter_sides[letter] = "none"
        elif engaged == {"lp"}:
            filter_sides[letter] = "lp"
            envelopes[f"filter_{letter}"] = _to_beats(
                [(f, _filter_hz(v)) for f, v in raw_filter],
                set_bpm, end_frame)
        elif engaged == {"hp"}:
            filter_sides[letter] = "hp"
            # Neutral points take the HP side's open value
            # (20 Hz) — the LP formula would give 20 kHz, which
            # as a high-pass cutoff is nearly silence.
            envelopes[f"filter_{letter}"] = _to_beats(
                [(f, 20.0 if v == 0.5 else _filter_hz(v))
                 for f, v in raw_filter], set_bpm, end_frame)
        else:
            filter_sides[letter] = "both"
            # The type at a neutral point carries the side in
            # force (the filter is open there on either type);
            # leading neutrals take the first engaged side.
            first_type = next(
                0.0 if v < 0.5 else 1.0
                for _, v in raw_filter if v != 0.5)
            current = first_type
            hz_points, type_points = [], []
            for f, v in raw_filter:
                if v < 0.5:
                    current = 0.0
                    hz_points.append((f, _filter_hz(v)))
                elif v > 0.5:
                    current = 1.0
                    hz_points.append((f, _filter_hz(v)))
                else:
                    hz_points.append(
                        (f, 20000.0 if current == 0.0 else 20.0))
                type_points.append((f, current))
            envelopes[f"filter_{letter}"] = _to_beats(
                hz_points, set_bpm, end_frame)
            envelopes[f"filter_type_{letter}"] = _to_beats(
                type_points, set_bpm, end_frame)
    total_beats = end_frame / RATE * set_bpm / 60.0
    # Pass 26: the Master Tempo envelope follows the recorded
    # SET BPM series — the base tempo, stepped at each master
    # move the performance made (frames convert to beats on the
    # base grid, like every other envelope here). No recorded
    # series (older logs / unknown base) keeps the constant line.
    series = recon.get("set_bpm_series") or []
    if series:
        envelopes["tempo"] = _to_beats(series, set_bpm, end_frame)
    else:
        envelopes["tempo"] = [(0.0, float(set_bpm)),
                              (total_beats, float(set_bpm))] \
            if total_beats > 0 else [(0.0, float(set_bpm))]

    seg_bpms = {path: (meta or {}).get("bpm")
                for path, meta in track_meta.items()}
    clips = {"A": [], "B": []}
    for i in range(2):
        # Pass 28.1 (ユーザー決定 2026-11-28「再演は両方残す」):
        # 同一開始位置 (offset_frames) からの再演は、後勝ちで
        # 消さず両方とも残す。pass 28 の置き換え規則
        # (_drop_superseded_starts) は廃止した。ここで落とすのは
        # _coalesce_segments が結合する連続断片だけ。
        for seg in _coalesce_segments(
                recon["segments"][i], seg_bpms, set_bpm):
            meta = track_meta.get(seg["track"]) or {}
            bpm = meta.get("bpm") or set_bpm
            if not meta.get("bpm"):
                warnings.append(
                    f"{seg['track']}: BPM 不明のため SET BPM で Warp します")
            start_s = seg["start_frame"] / RATE
            span_s = (seg["end_frame"] - seg["start_frame"]) / RATE
            offset_s = seg["offset_frames"] / RATE
            # Pass 27: 終端は「閉じる瞬間の実位置」があればそちら
            # (吸収した位相補正の累積を含む実位置)。無い場合のみ
            # 従来通り (開始位置 + 経過 × rate) で見積もる。
            if seg.get("end_offset_frames") is not None:
                end_s = seg["end_offset_frames"] / RATE
                # Pass 29: a net-backward segment (a spinback /
                # scratch that ends behind where it started) has
                # no valid single-clip form — CurrentEnd <=
                # CurrentStart is not a clip Live can show. Fall
                # back to the performed timeline span played
                # straight through, so the clip stays valid and
                # timeline-faithful.
                if end_s <= offset_s:
                    end_s = offset_s + span_s * seg["rate"]
            else:
                end_s = offset_s + span_s * seg["rate"]
            clips["AB"[i]].append({
                "file": seg["track"],
                "name": meta.get("name") or Path(seg["track"]).stem,
                "time_beats": start_s * set_bpm / 60.0,
                "offset_beats": offset_s * bpm / 60.0,
                "end_beats": end_s * bpm / 60.0,
                "duration_seconds": meta.get("duration_seconds"),
                "bpm": bpm,
                "rate": seg["rate"],
            })
            if abs(seg["rate"] * bpm - set_bpm) > 0.5:
                warnings.append(
                    f"{seg['track']}: デッキ rate × 曲 BPM が SET BPM と"
                    "違うため、Live 上のクリップ長が演奏時間とずれます")
    # Pass 28: bar alignment — shift the whole timeline so the
    # first clip's track downbeat lands on a Live bar line.
    shift = _bar_shift(clips, track_meta, set_bpm)
    if shift:
        for letter in ("A", "B"):
            for clip in clips[letter]:
                clip["time_beats"] += shift
        for key, points in envelopes.items():
            envelopes[key] = [(t + shift, v) for t, v in points]
        total_beats += shift
        warnings.append(
            "最初の曲の小節頭を Live の小節線に合わせました "
            f"(全体を {shift:.2f} 拍ずらしています)")
    return dict(set_bpm=set_bpm, total_beats=total_beats, clips=clips,
                envelopes=envelopes, warnings=warnings,
                filter_sides=filter_sides, bar_shift=shift)


def _bar_shift(clips, track_meta, set_bpm):
    """タイムラインを何拍ずらせば、最初のクリップの曲のダウン
    ビートが Live の小節線 (4 拍ごと) に乗るか (0 ≤ Δ < 4)。

    最初のクリップの曲について、その開始位置 (曲内のフレーム)
    から次のダウンビートまでの距離を曲の拍で数え、SET BPM の
    拍に直して、合計が 4 の倍数になる Δ を返す。ダウンビートの
    位置は beat_offset (拍グリッド原点、フレーム) + bar_offset
    (小節頭が何拍目か、0..3) で決まる。どちらか欠ける曲 (解析
    グリッド無し) では 0 — 従来通りセグメント開始が 0 拍。
    相対配置には一切触らないので、後のクリップは演奏した相対
    位置のまま小節に乗る (同期した演奏ではダウンビート同士も
    一致する)。"""
    first = None
    for letter in ("A", "B"):
        for clip in clips[letter]:
            if first is None or clip["time_beats"] < first["time_beats"]:
                first = clip
    if first is None:
        return 0.0
    meta = track_meta.get(first["file"]) or {}
    beat_offset = meta.get("beat_offset")
    bpm = first.get("bpm")
    if beat_offset is None or not bpm:
        return 0.0
    bar_offset = meta.get("bar_offset") or 0
    beat_frames = RATE * 60.0 / float(bpm)
    start_frame = first["offset_beats"] * beat_frames
    into_bar = ((start_frame - float(beat_offset)) / beat_frames
                - float(bar_offset)) % 4.0
    to_downbeat = (4.0 - into_bar) % 4.0     # track beats
    rate = first.get("rate") or 1.0
    downbeat_at = first["time_beats"] \
        + to_downbeat * float(set_bpm) / (float(bpm) * float(rate))
    return (4.0 - (downbeat_at % 4.0)) % 4.0
