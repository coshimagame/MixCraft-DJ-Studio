# MixCraft Remote Script v1 — 導入とテンプレート前提

`remote_script/MixCraft/` が Live 内で動く Remote Script 本体（v1）。
変換ロジックは同梱の Python 2.7 互換コピー（`_live_bridge.py` /
`_midi_map.py`）にあり、本体は MIDI の受け渡しと Live オブジェクト
操作だけを行う薄いアダプター。設計の背景は `README.md` の
「アダプターの責務」を参照。

## 導入手順

1. `remote_script/MixCraft/` フォルダを、Live 本体側の
   `C:\ProgramData\Ableton\Live 10 Suite\Resources\MIDI Remote Scripts\`
   にコピーする（貼り付け時に管理者権限の確認ダイアログが出る
   ので「続行」を押すこと）。
   ※ Live 10.1.43 実機（2026-11-25）では、ユーザーライブラリ内
   （`Documents/Ableton/User Library/Remote Scripts/`）に置いても
   Live が走査せず、コントロールサーフェイスの一覧に一切
   現れなかった（ログにも記録なし）。本体側フォルダに入れた
   ところ即座に現れて読み込まれた。新しい Live では
   User Library 側の置き方でもよいが、Live 10 では本体側が確実。
2. DDJ-RB を USB で接続し、rekordbox など他のアプリを**完全に
   終了**してから Live 10 を起動する（Windows の MIDI ポートは
   単一クライアントのため、rekordbox が掴んでいると Live に
   MIDI が届かない — 2026-11-25 実機で切り分け済み）。
3. 環境設定 → Link/MIDI → コントロールサーフェイスの空き枠で
   **MixCraft** を選び、入力・出力の両方に **DDJ-RB** を割り当てる。
4. 下の MIDI Ports で DDJ-RB の入力 **Remote = On**（Track も On で
   よい）、出力 **Remote = On**（LED 送信用）にする。

## v1.1 修正（2026-11-25 実機）: MIDI フォワーディング

Live は Remote Script の `receive_midi()` に対して、
`build_midi_map()` の中で `Live.MidiMap.forward_midi_note` /
`forward_midi_cc` により**明示的にフォワードしたメッセージしか
配送しない**（Ableton 純正スクリプトの仕様:「フォワードも
パラメータマッピングもしていなければ、MIDI メッセージは
一切届かない」）。v1 はフォワードを実装していなかったため、
実機ではスクリプトが読み込まれてログに
「MixCraft Remote Script v1 loaded」と出るのに、ノブや
ボタンを操作しても MIDI が 1 件も届かなかった。

v1.1 では `build_midi_map()` を追加し、DDJ-RB が使う
チャンネル（0-based: デッキ 0/1、FX 4/5、グローバル 6、
パッド 7/8）について、ノート 0–127 と CC 0–127 の全範囲を
フォワードする。入力ポートは DDJ-RB 専用なので全範囲
フォワードで問題なく、将来プロファイルに同チャンネルの
操作子が増えてもマップ変更なしで届く。

## v1.2 修正（2026-11-26 実機）: EQ Three のパラメータ名

実機テストで PLAY やフェーダーは効くのに EQ ノブだけが
無反応だった。原因はパラメータ名の不一致: EQ Three が
LOM（Live Object Model）で公開するゲインのパラメータ名は
**GainLow / GainMid / GainHi** であり、v1.1 までの
スクリプトが探していた 'Low Gain' / 'Mid Gain' /
'High Gain' は実在しない。デバイス自体は class 名で
見つかっていたが、パラメータが 1 つも一致せず、EQ の
アクションだけが黙ってスキップされていた。

v1.2 では候補名を順序付きタプルにし、実名
（GainLow/GainMid/GainHi）を先に、旧表記をフォール
バックとして後に探すようにした。旧表記で名前を公開する
ビルドがあっても従来どおり動く。

ついでに同日の確認事項: Auto Filter の Frequency
パラメータは Live 10 では 0–1 正規化されており、
`_apply_filter` の既存の線形フォールバックでそのまま
扱える（対数写像は min>0 の実周波数レンジ向け）。
Filter Type の列挙インデックスはレガシー順
（LP=0 / HP=1）のままで一致した。

## v1.3（2026-11-26 実機）: 診断ログとジョグ修正

同日の続報: v1.2 でも EQ LOW だけが動かず、CUE ボタンも
無反応、ジョグのナッジは回転と逆方向に効いた。LOW と CUE は
モックでは全層グリーンのため、実機の Live オブジェクトの
実態を取る診断を入れた（恒常機能ではなく調査用）:

- 起動後の初回だけ、両デッキトラックのデバイス一覧
  （class 名・表示名・全パラメータ名）を Log.txt に出す。
- EQ の照合結果（成功時は一致したパラメータ名、失敗時も）を
  (track, target) ごとに初回だけ出す。
- PLAY / CUE の transport 到達と結果（発射・再発射・停止・
  スロットなし）を初回だけ出す。ジョグもデッキごとに初回
  だけ、生の正規化値と計算 delta を出す。
- いずれも接頭辞 `MixCraft DIAG` で Log.txt を grep できる。

ジョグは実測に合わせて2点修正した（root の `live_bridge.py`
と同梱コピーの両方）: **符号を反転**（`_jog_delta` の戻り値を
負転。回転方向と再生位置の移動が逆だったため）と、
**刻みを 0.01 → 0.005 拍/ティックに細分化**
（`NUDGE_BEATS_PER_TICK`、使用者の希望）。

## v1.4 修正（2026-11-26 実機）: LOW 確定とヘッドホン CUE

v1.3 の診断ダンプが実機の正解を返した:

- EQ Three の実名は **class `FilterEQ3`**、パラメータは
  **`GainLo` / `GainMid` / `GainHi`** だった。LOW が死んでいた
  原因は `GainLow` との1文字違い。v1.4 で候補の先頭を
  `GainLo` にし、クラス候補にも `FilterEQ3` を追加した。
  （Utility は class `StereoGain`・`Gain`、Auto Filter は
  class `AutoFilter`・`Filter Type` / `Frequency` で従来どおり
  一致していた。）
- 診断ログ（`MixCraft DIAG`）は役割を終えたため全て撤去した。
  ジョグの符号反転と 0.005 刻みは実測どおり維持
  （生値 65 → +63 ティック = 前方向で確定）。

追加機能: **DDJ-RB の HEADPHONES CUE 1/2 ボタン**（デッキ側
チャンネルの Note 84。マッピングモードで実測）で、Live の
デッキごとのキューモニターを切り替えられるようにした。
押すたびにトラックの Solo がトグルし、ボタン LED が状態に
連動する。**前提として、その Set の Cue Out が Master と
別の出力（テンプレートでは 3/4）に設定されていること** —
その場合 Live は Solo を Cue として扱い、音はヘッドホン
だけに行く。Cue Out が 1/2 のままだと通常の Solo になる。

## テンプレート Set の前提

スクリプトは次の形の Set を前提に動く。外れた要素は黙って
スキップされる（落下はしない）。

- **Track 1 = Deck A、Track 2 = Deck B**。
- 各デッキトラックに次のデバイスを入れる（順序は不問。探索は
  LOM の class 名 → 表示名 → パラメータ名の順）:
  - **Utility** — TRIM（`input.gain`）はその Gain を操作する。
  - **EQ Three** — HI/MID/LOW は各バンド Gain を操作する。
    正規化 0.5 = 0 dB になる前提（EQ Three のバンド Gain は
    ±15 dB リニアで中央が 0 dB — テンプレート作成時に最終確認）。
  - **Auto Filter** — FILTER つまみは bipolar 制御: 中央から左が
    ローパス（周波数を対数スケールで下げる）、右がハイパス。
    「Filter Type」の列挙インデックスは LP=0 / HP=1 と仮定
    （**テンプレート作成時に実機確認が必要**）。
- **クロスフェーダー割当**: Deck A トラック → A、Deck B トラック
  → B（未割当だとクロスフェーダーが音に効かない — 実機確認済み）。
- **オーディオ**: DDJ-RB ASIO、Master Out = 1/2、Cue Out = 3/4
  （ヘッドホン独立は実機確認済み。Cue Out は Set ごとに保存
  されるためテンプレート側で固定すること）。
- **セット BPM は固定**: テンポスライダーと SYNC はスクリプトが
  意図的に無視する（設計判断 2026-11-25）。
- **ホットキュー用クリップ**: 各デッキトラックの Session スロット
  に、パッドで発射したい順に置く。スクリプトは「クリップのある
  スロットを順に数えたもの」をそのデッキのパッド 1〜8 に割り
  当てる（空きスロットが間にあってもズレない）。rekordbox の
  キューから作る clip plan（`library.cues_to_clip_plan`）の接続は
  次の段階で、このスロット走査が v1 の代替になっている。

## v1 の挙動メモ（近似・仮定）

- **CUE ボタン**: 再生中はそのクリップを先頭（スタートマーカー
  ＝キュー点）から再発射する。停止中はロード済みクリップを
  発射する。rekordbox の「キュー点で一時停止」とは違う v1 近似。
- **ジョグ**: bridge の仮定（0x01–0x3F = +、0x7F–0x41 = −、
  1 tick = 0.01 拍）のまま。再生中クリップへの適用は LOM の
  `move_playing_pos` が使える場合のみで、使えない環境では無効。
  実機ログを取りながら後日調整する。
- **14bit CC**: フェーダー類は MSB のみ使用（7bit 分解能）。
  実測で番号が一致したのは MSB 側。LSB 対応は将来課題。
- **LED**: PLAY は再生中点灯、CUE は「ロード済み・停止中」に
  点灯、パッドはクリップのあるスロット分だけ点灯。いずれも
  Note On velocity 127 = 点灯と仮定（手動マッピングでは一切
  点かなかったため、点灯仕様そのものが実機確認項目）。

## Python 2.7 互換コピーについて

Live 10 同梱 Python は 2.7 系で、リポジトリ直下の `live_bridge.py`
（dataclasses 使用）と `midi_map.py`（f-string、`**` 展開、
pathlib 使用）をそのままでは読み込めない。そのためパッケージ内に
互換コピーを同梱した。意味の変更はなく、機械的な置き換えのみ:

- `_live_bridge.py`: dataclass → 通常クラス（`__eq__`/`__repr__`
  実装）、型注釈の除去、`_jog_delta` の `round()` 結果を int 化。
- `_midi_map.py`: パッド表の `**{...}` 展開 → ループ構築、
  `MidiLearn` と `save_mapping`/`load_mapping`（ファイル IO 系、
  スクリプトでは未使用）を省略。

直下の原本側の変換ルールを変更したら、同梱コピーにも同じ変更を
入れること（逆も同様）。
