# Phase 2 実装メモ (muse/phase2-development, 2026-11-24)

> 以下は受領時の記録。2026-11-25の修正・Windows検証は PHASE2_WINDOWS.md を参照。

担当: かまはげた (Mw-fActory)。基準 main: fc6ed7a (v0.1.0)。既存テスト14件は維持し、
Phase 2 テスト16件を追加して合計30件通過 (Linux / Python 3.12、合成信号のみ)。

## 実装したもの
- オーディオ: 全パラメータの5msデジッパー平滑化 (volume/cross/master/gain/EQ/filter)、
  0.90以上のソフトニーリミッター (小信号は無変更)、EQ/Filterの状態保持。
- DJ機能: 3バンドEQ (クロスオーバー方式、中立はビット透過)、GAIN、FILTER (中央から左=LP/右=HP)、
  CUE、HOT CUE (番号指定)、LOOP (in/out/exit)、Quantize (ビートグリッド吸着)、
  SYNC (相手BPMへレート追従) と tempo レート再生。UIに各デッキ分のスライダー/ボタンを追加。
- 解析: ビートグリッド (BeatGrid、オフセット推定+手動 nudge 用 API)、キー推定
  (クロマ+Krumhansl相関、Camelot併記)、SQLite解析キャッシュ (指紋が変われば再解析)。
- 自動ミックス土台: automix.py。フレーズ境界への吸着、8/16/32/64小節候補、ベース交換位置、
  キー相性・BPM差・ボーカル重なりヒューリスティクスの警告とスコア、候補のA/B比較用リスト。
- ライブラリ: library.py。SQLite楽曲/プレイリスト、rekordbox XML取り込み (読み取りのみ)、
  Ableton代替出力 export_bundle (arrangement.json + markers.csv)。ALS互換は未確認のまま。
- MIDI: midi_map.py。APC40 / APC40 mkII / DDJ-RB のプロファイル (暫定番号)、
  MIDI Learn 状態機械、LEDフィードバック生成、マッピング保存/読込。実機未検証。
- 低遅延: app.py で WASAPI ホスト検出時は WasapiSettings を使い latency='low' を指定、
  ストリームの報告レイテンシを画面表示。Windows実機での計測は未実施。

## 実装していない / 宣言しないもの
- 音程維持タイムストレッチ (SYNCはレート再生で音程も変わる)、ASIO、ヘッドホン独立出力、
  実曲でのBPM/キー精度、90分連続再生、Windows exe ビルドと実音声出力 (CIで検証予定)、
  APC40/DDJ-RB 実機、Ableton Live 10 ALS 互換、AUTO MIX の音声レンダリングと連続試聴UI、
  EDIT MODE タイムラインUI、同時WAV録音。
- EQ のクロスオーバーは Python ループ処理のため、EQ/Filter 使用時の RT 余裕は
  Windows 実機で要計測。製品版では C++ 化が前提 (SPECIFICATION の通り)。

## 次の作業
1. GitHub Actions (Windows) でテスト+exeビルド+同梱スモークを通す。
2. むぅちゃん実機: WASAPIレイテンシ実測、APC40/DDJ-RB の MIDI 実装配線 (rtmidi/mido 導入判断)。
3. AUTO MIX 候補のプレビュー再生 (重なり部分のみ試聴) を既存 preview 機構へ接続。
4. 解析キャッシュを load_track に標準接続し、ビートオフセット手動修正UIを追加。
