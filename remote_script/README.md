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
（2026-11-25 実機結果: Live 10.1.43 では User Library 側は走査
されず、実際に効いた置き場所は
`C:\ProgramData\Ableton\Live 10 Suite\Resources\MIDI Remote Scripts\`
だった。下のチェックリスト「スクリプトの置き場所」参照。）

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

2026-11-25 夜、実機（Windows / Ableton Live 10.1.43 Suite + DDJ-RB）で
確認した結果を各項目に記録した。未了の項目はそのまま残してある。

- [x] **DDJ-RB のヘッドホン出力の見え方**: Cue Out = 3/4 で
      独立モニター可能（2026-11-25 実聴で確認、2026-11-26 確定）。
      DDJ-RB ASIO を選ぶとステレオ出力 1/2 と 3/4 の2組が見え、
      Master Out = 1/2、Cue Out = 3/4 にする。ただし Master
      ストリップの Solo/Cue モード切替が **Cue モード**であることが
      必須で、Solo モードのままだとモニター音は出ない
      （「MASTER CUE を点けないと音が出ない」症状の真因）。
      注意: Cue Out / Master Out の選択は Live Set ごとに保存される
      ため、テンプレート Set で 3/4 に固定すること。レイテンシー:
      44.1 kHz / バッファ 256 で入出力合計 11.6 ms。
      詳細は TEMPLATE_GUIDE.md の完了記録を参照。
- [x] **DDJ-RB ヘッドホン系ツマミ**: 本体に MIX ツマミは
      **存在しない**（LEVEL=音量のみ、ジャック付近にもツマミ無し）。
      ヘッドホンは Live が USB 3/4 に送った音をそのまま再生する
      （2026-11-26 確認。2026-11-25 の「MIX を CUE 側に振ると
      キューだけが聞こえた」という記録は誤認で、実際はこの経路で
      聞こえていたもの）。
- [x] **MIDI 番号の実照合**: **主要6件を確認（2026-11-25）、全て
      AlphaTheta 公式一覧および `midi_map.py` の DDJ_RB
      プロファイルと一致**。実測（チャンネルは 1-based 表記）:
      クロスフェーダー = ch7 CC 31 / PLAY (deck1) = ch1 Note 11 /
      チャンネルフェーダー (deck1) = ch1 CC 19 / FILTER (deck1) =
      ch7 CC 23 / TRIM (deck1) = ch1 CC 4 / EQ HI (deck1) =
      ch1 CC 7。特に FILTER はプロファイルで仮置きしていた
      「グローバル ch の Filter Parameter CC 23」が正解だった。
      未了の細部: 14bit CC の MSB/LSB の出方、ジョグの相対値
      エンコーディング（`live_bridge` は 0x01–0x3F = +、
      0x7F–0x41 = − と仮定）はスクリプト導入時に実値ログで確認する。
- [x] **LED**: **手動マッピングでは点灯しないことを確認
      （2026-11-25）**。Live の手動 MIDI マップ（PLAY を
      Transport Start に割当）では、再生を始めても DDJ-RB の
      PLAY LED は点灯しなかった。結論: LED フィードバックは
      Remote Script 側が `midi_map.led_feedback` の Note-On を
      明示的に送る設計どおり必要。色・明滅の velocity 仕様と
      APC40 側（velocity=1 で緑という既存仮定）の実照合は
      スクリプト導入時に行う。
      **スクリプト経由の点灯を確認（2026-11-25 深夜）**:
      MixCraft Remote Script v1.1 が送るフィードバックで
      DDJ-RB の PLAY LED が点灯した。点灯のタイミングはボタン
      押下の瞬間ではなく、クリップが実際に再生を開始した時点
      （発射クオンタイズの分だけ遅れる）。これは仕様どおりの
      挙動であり故障ではない。
- [ ] **EQ Three のパラメータ**: 未了。バンドゲインの正規化値と
      dB の対応、「0.5 = 0 dB」をテンプレート側のレンジ調整で
      実現できるかは、テンプレート作成時に確認する。
- [x] **クロスフェーダーの割当**: **確認済み（2026-11-25）**。
      Live のクロスフェーダーはトラックごとの A/B 割当が前提で、
      未割当のトラックには音に効かない（割当後に左右で音が
      消える/残ることを実聴で確認）。テンプレートでは Deck A
      トラック→A、Deck B トラック→B を事前に割り当てておく。
- [x] **オートメーション録音**: **確認済み（OK、2026-11-25）**。
      手動マッピング経由の RB のフェーダー/クロスフェーダーの
      動きが Arrangement にエンベロープ（実線＋ブレークポイント）
      として記録され、後から編集できることをスクリーンショットで
      確認した。Session のクリップ発射も Arrangement にクリップ
      として記録された。注意（Live 10 の要点）: Arrangement 録音
      ボタンとは別に「オートメーション・アーム」を ON にする
      必要がある。OFF だとクリップだけが録れてオートメーション
      の線が残らない。テンプレート/運用手順ではアーム ON を
      前提にする。
- [ ] **クリップ発射と nudge**: 未了（スクリプト段階）。Session
      クリップ発射の量子化挙動、再生中クリップの位置補正を
      API で行う方法（代替案: ビートジャンプへの読み替え）。
- [x] **Remote Script の Python 環境**: **確認済み
      （2026-11-25 深夜）**。MixCraft Remote Script が
      Live 10.1.43 で読み込まれた（Log に
      `(MixCraft) MixCraft Remote Script v1 loaded` の行を
      確認）。Live 10 の同梱 Python は 2.7 で、スクリプトは
      2.7 互換で書いてある（`live_bridge` / `midi_map` は
      Python 2.7 対応に直した同梱コピーを使用し、dataclasses
      等の Python 3 専用機能は同梱側で回避済み）。
- [x] **スクリプトの置き場所**: **確認済み（2026-11-25
      深夜）**。Live 10.1.43 は User Library 内の
      `Remote Scripts/` を走査しなかった（コントロール
      サーフェイスの一覧に出ず、Log にも痕跡なし）。実際に
      効いた置き場所は
      `C:\ProgramData\Ableton\Live 10 Suite\Resources\MIDI Remote Scripts\`
      （貼り付け時に管理者権限の確認ダイアログあり）。
- [x] **スクリプト経由の操作**: **確認済み（2026-11-25
      深夜）**。v1 ではスクリプトに MIDI が一切届かなかった。
      原因は Live の仕様で、`build_midi_map` の中で
      `Live.MidiMap.forward_midi_note` /
      `forward_midi_cc` による明示転送を宣言しないと
      `receive_midi` には何も届かない（Ableton 純正
      スクリプトのソースでも裏取り済み）。v1.1 で DDJ-RB の
      使用チャンネル（0-based で 0, 1, 4, 5, 6, 7, 8）の
      全ノート/全 CC を転送するよう直した結果、手動
      マッピング無しで DDJ-RB がスクリプト経由で Live を
      駆動した（ユーザー判定「うごいた」）。

## 運用メモ（実機で判明したこと）

- **MIDI ポートの掴み合い（Windows）**: Windows の MIDI ポートは
  単一クライアントのため、rekordbox が DDJ-RB のポートを掴んで
  いると、Live の設定にポートが見えていても MIDI が一切届かない。
  Live を使う時は rekordbox を完全終了（トレイ常駐分も含む）
  してから Live を起動する順番が必須。2026-11-25 に実機で
  切り分け済み（rekordbox では RB が正常動作 → Live は無反応 →
  rekordbox 完全終了 + Live 再起動で復旧）。
