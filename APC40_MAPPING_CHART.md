# APC40 → MixCraft DJ Studio マッピング図表

対象: `midi_map.py`（ブランチ muse/phase2-development / PR #2 時点 + 2026-11-25 ノブ配置変更）
初代 APC40 と APC40 mkII は現行プロファイルでは同一割当（違いは初代のみ HotCue パッドあり）。
**番号はすべて暫定・実機未検証。MIDI入出力は未接続。**

## ノブ配置（2026-11-25 むぅちゃんの指定）

ノブ16個を「上の8つ＝左デッキ（Deck A）」「下の8つ＝右デッキ（Deck B）」に分ける。

- 上の8つ = トラックコントロール ノブ（CC 16–23）→ **Deck A**
- 下の8つ = デバイスコントロール ノブ（CC 24–31）→ **Deck B**
- 各デッキ内: 上段 HIGH / MID / LOW / FILTER、下段の先頭が GAIN、残り3つは予備（MIDI Learn 用）

## 配置図

```
┌─ DECK A（左）／トラックコントロール ──┐  ┌─ MASTER ──────┐  ┌─ DECK B（右）／デバイスコントロール ──┐
│ HIGH CC 16 │ MID CC 17 │ LOW CC 18 │ FILTER CC 19 │  │ Master  CC 55 │  │ HIGH CC 24 │ MID CC 25 │ LOW CC 26 │ FILTER CC 27 │
│ GAIN CC 20 │ 予備 CC 21 │ 予備 CC 22 │ 予備 CC 23 │  │ Cross   CC 56 │  │ GAIN CC 28 │ 予備 CC 29 │ 予備 CC 30 │ 予備 CC 31 │
│ 音量   フェーダー CC 48 │  │               │  │ 音量   フェーダー CC 49 │
│ Play      ボタン Note 50│  │ LED: Playボタン │  │ Play      ボタン Note 49│
│ Cue       ボタン Note 52│  │ Note-On 速度1=緑│  │ Cue       ボタン Note 51│
│ HotCue    パッド Note 32/33 ※要検証            │ HotCue    — 未割当      │
└────────────────────────┘  └───────────────┘  └────────────────────────┘
```

## CC 一覧（ch 0、値 0–127 → 0.0–1.0）

| 機能 | Deck A | Deck B | 備考 |
|---|---|---|---|
| EQ HIGH | 16 | 24 | 中央(64)が中立、0でキル |
| EQ MID | 17 | 25 | 同上 |
| EQ LOW | 18 | 26 | 同上 |
| FILTER | 19 | 27 | 中央が中立、左=LP / 右=HP |
| GAIN | 20 | 28 | 2026-11-25 追加（旧配置では未割当） |
| 予備（未割当） | 21 / 22 / 23 | 29 / 30 / 31 | MIDI Learn 用 |
| チャンネル音量 | 48 | 49 | フェーダー想定 |
| マスター音量 | 55（共通） | | |
| クロスフェーダー | 56（共通） | | 等電力カーブ |

## Note 一覧（ch 0、Note-Off は無視）

| 機能 | Deck A | Deck B |
|---|---|---|
| Play / Pause | 50 | 49 |
| Cue ジャンプ | 52 | 51 |
| HotCue ジャンプ | 32 / 33（初代のみ） | — |

## 注意点

- HotCue は不具合あり: Note 32/33 の両方が Deck A の `hotcue_jump` で、キューの番号が渡らない
  （dispatch が True を返すため実質 HotCue 1 扱い）。配線実装時に番号を値として渡すよう直すこと。
- Loop、Quantize、SYNC、HotCue Set、Cue Set は APC40 に未割当（画面操作のみ）。
- MIDI Learn で任意の CC/Note を割り当て直し可能。割当は JSON で保存/読込可能。
- 実機の APC40 には機種固有の MIDI 仕様があるため、配線実装時に実機で番号を1つずつ照合すること。
- この変更はローカル（midi_map.py）のみ。PR #2 / Codex 側へは次の受け渡しで反映すること。
