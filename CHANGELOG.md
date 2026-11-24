# Changelog

## 0.2.0-dev — 2026-11-24 (Phase 2, 未リリース)
- パラメータ平滑化 (5ms) とソフトニーリミッターでクリック/音割れ対策。
- 3バンドEQ、GAIN、FILTER、CUE、HOT CUE、LOOP、Quantize、SYNC/tempo を追加 (UI配線含む)。
- ビートグリッド、キー推定 (Camelot)、SQLite解析キャッシュを追加。
- 自動ミックス計画 (フレーズ吸着、8/16/32/64小節候補、キー/BPM/ボーカル警告) の土台を追加。
- ライブラリ/SQLite、rekordbox XML取り込み、Ableton代替出力 (arrangement.json/markers.csv) を追加。
- MIDIマッピング/Learn/LED のロジック基盤を追加 (APC40/MKII/DDJ-RB、実機未検証)。
- WASAPI優先と報告レイテンシ表示。自動テスト 14件 → 30件。
- 未実装のまま: 音程維持タイムストレッチ、ASIO、ヘッドホン別出力、ALS互換、EDITタイムライン、同時WAV録音。

## 0.1.0 — 2026-11-24
- 初回Phase 1試遊版。2デッキ・波形・簡易BPM・音量・クロスフェーダー。
- A末尾/B先頭のミックス試聴と元状態の復元。
- 音声ブロック基準の操作イベント、自動JSONL、JSON書き出し。
- Windows実行可能ビルドとGitHub Actions定義。
- 高度DJ/自動セット/タイムライン/ALS/MIDIは含まれない。
