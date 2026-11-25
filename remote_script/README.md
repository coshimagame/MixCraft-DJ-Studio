# MixCraft Remote Script — アダプター設計メモ

Ableton Live 相棒型の第1層 (Python Remote Script) のうち、**Live の中に
置く側**の設計メモ。変換ロジック本体は Live 非依存の `live_bridge.py`
（リポジトリ直下）にあり、ここに書くのは Live 側の薄いアダプターの
責務と呼び出し方だけ。

## なぜ Live 本体のコードをまだ書かないのか

Remote Script は Live の内部 API (`_Framework`, `Live` モジュール) に
強く依存し、API の細部（Live 10 の Python 環境、デバイスパラメータの
列挙方法、セッションクリップの操作）は**実機の Live 10 Suite で
当ててみないと検証できない**。検証手段の無いコードを先に書くと、
番号違い・API 違いのまま「動くはずのもの」が溜まっていく。
そのため順序をこう固定する:

1. 変換ロジック（`live_bridge.py` + `midi_map.py`）を Live の外で
   単体テスト可能な形で完成させる ← **現在ここ**
2. 実機で下の「確認すべき項目」を潰す
3. その結果を反映して、薄いアダプターだけを書く

## 置き場所（将来）

Live の User Library 内 `Remote Scripts/MixCraft/` に、Live が要求する
パッケージ形（`__init__.py` が `create_instance` を返す）で置く。
`live_bridge.py` / `midi_map.py` / `library.py` は Live 非依存なので、
そのまま同梱するか、開発リポジトリからコピーして使う。

## アダプターの責務（これだけ）

1. **MIDI 受信**: DDJ-RB（主）/ APC40（補助）からの MIDI を受け、
   `(kind, channel, number, value)` を `midi_map.dispatch` に渡す。
   - 注意: dispatch は Hot Cue パッドの番号を落とす（既知の制約）。
     アダプターは受信した note 番号を保持し、`cue_index` として
     bridge に渡すこと。APC40 の Note 32/33 が両方 Deck A の
     `hotcue_jump` になる既知の不具合もここで吸収する。
2. **計画の取得**: `LiveBridge.handle_dispatch(cmd, cue_index=...)` を
   呼び、`LiveAction` のリストを受け取る。デッキごとのクリップ案は
   `library.cues_to_clip_plan` で作って `LiveBridge(clip_plans=...)`
   に渡しておく。セット BPM は固定値として保持するだけ（テンポ
   アクションは設計上存在しない）。
3. **実行**: LiveAction を Live オブジェクトへの操作に落とす。
   - `set_parameter` (track=N): トラック N のデバイスパラメータ。
     `eq_three.*` = EQ Three の各バンド（0.5 = ニュートラルになる
     ようテンプレート側でレンジ調整）、`input.gain` = Utility 等の
     入力ゲイン、`track.volume` = トラックフェーダー、
     `filter.bipolar` = Auto Filter（負 = LP 側、正 = HP 側）。
   - `set_parameter` (track=None): `mixer.crossfader` /
     `mixer.master_volume` はソングのミキサーへ。
   - `transport`: `clip.toggle_play` / `clip.cue_jump` /
     `clip.loop_in` / `clip.loop_out` は対象トラックの再生中クリップへ。
     `track.load` は当面 no-op でよい（選曲は事前固定のため）。
   - `fire_clip`: 対象トラックのセッションクリップ
     （clip_plan の clips[clip_index] に対応するスロット）を発射。
   - `nudge`: 再生中クリップの位置を value（拍）だけ一時補正する。
     テンポ自体は変えない。
4. **LED フィードバック**: `midi_map.led_feedback` が作る Note-On を
   そのまま送る。DDJ-RB 側の LED 仕様は実機確認後に追加する。
5. **Arrangement 録音の前提**: ユーザーの手の動きが後編集できるのは
   Live 本体のオートメーション録音による。アダプターはパラメータを
   「Live のデバイスパラメータ」として操作するので、標準の
   Automation Arm + Arrangement 録音でそのまま記録される想定
   （実機で確認する）。

## Live 10 Suite 実機で確認すべき項目

番号・仕様はすべて未検証。確認したらこのファイルに結果を追記する。

- [ ] **DDJ-RB のヘッドホン出力の見え方**: Live のオーディオ設定で
      DDJ-RB を選んだとき、Cue 出力を DDJ-RB のヘッドホン側へ
      独立して送れるか（出力チャンネルの列挙結果）。送れない場合の
      代替経路（別インターフェース）も含めて記録する。
- [ ] **DDJ-RB ヘッドホン系ツマミ**: HEADPHONES MIX / LEVEL が
      本体ハードウェア制御か（Live 側に MIDI が来るか）。
- [ ] **MIDI 番号の実照合**: `midi_map.py` の DDJ_RB プロファイル
      （AlphaTheta 公式一覧ベース）と実機の送信値の突き合わせ。
      特に 14bit CC の MSB/LSB の出方、ジョグの相対値エンコーディング
      （`live_bridge` は 0x01–0x3F = +、0x7F–0x41 = − と仮定）、
      フィルターのグローバル ch CC 23/24。
- [ ] **LED**: PLAY/CUE・パッドの LED が Note-On で点灯するか、
      色・明滅の velocity 仕様。APC40 側は `led_feedback` の既存仮定
      （velocity=1 で緑）の実照合。
- [ ] **EQ Three のパラメータ**: バンドゲインの正規化値と dB の対応、
      「0.5 = 0 dB」をテンプレート側のレンジ調整で実現できるか。
- [ ] **クロスフェーダーの割当**: テンプレートのトラック A/B 割当と
      `mixer.crossfader` 操作の対応。
- [ ] **オートメーション録音**: アダプター経由のパラメータ操作が
      Arrangement にオートメーションとして記録されるか。
- [ ] **クリップ発射と nudge**: Session クリップ発射の量子化挙動、
      再生中クリップの位置補正を API で行う方法（代替案: ビート
      ジャンプへの読み替え）。
- [ ] **Remote Script の Python 環境**: Live 10 同梱 Python の版と、
      同梱モジュール（dataclasses 等）の利用可否。
      使えない場合は `live_bridge.py` の書き換え範囲をここに記録する。
