# Roadmap / 機能状態

状態: 未着手 → 開発中 → 実装済み → テスト済み → 実機確認済み。不具合ありは併記。

|Phase|機能|状態|検証範囲|
|---|---|---|---|
|1|WAV/MP3/FLAC/AIFF decode、2 deck mixer|テスト済み|合成ファイル・DSP単体|
|1|波形、D&D、再生UI|実装済み|UI確認はTEST_CHECKLIST参照|
|1|簡易BPM|テスト済み|120 BPMクリック音。一般楽曲精度未検証|
|1|ミックス試聴|テスト済み|位置復元・中断・再実行|
|1|イベントJSON/JSONL・復旧変換|実装済み|非破壊記録の基礎。完全再現は未着手|
|1|Windows build / CI|開発中|ビルド結果はTEST_CHECKLIST参照|
|2|ビートグリッド・小節頭・同期・タイムストレッチ|未着手|次の優先項目|
|2|EQ/GAIN/FILTER/Hot Cue/Cue/Loop/Quantize|未着手|ResetはDJ Cue機能とは別|
|2|ライブラリ/タグ/SQLite/reKordbox XML|未着手|元ファイル非変更を維持|
|2|APC40初代/MKII/DDJ-RB/MIDI Learn/LED|未着手|実機未確認|
|2|ヘッドホン別出力|未着手|独立バスが必要|
|3|構造・エネルギー・キー・ボーカル解析|未着手|確信度と手動修正必須|
|3|8種ミックス/フレーズ配置/連続試聴|未着手|対象ジャンルで主観評価|
|4|同時WAV録音・操作再現・タイムライン|未着手|90分録音とクラッシュ復旧試験|
|4|Undo/Redo・automation・warp・project save|未着手|元音源参照方式|
|5|Live10 ALS/track stems/MIDI export|未着手|実際のALSテンプレートが必要|
|5|Live11/12 compatibility|未着手|対応宣言不可|

最優先: 実曲・長時間再生の計測 → 平滑化/音声基盤改善 → ビートグリッド/同期 → EQ/MIDI。
