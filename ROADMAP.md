# Roadmap / 機能状態

2026-11-25: 受領コード30件をWindowsで再検証、回帰修正後42件通過。
WASAPI自動選択とAPI報告遅延22msを短時間確認。詳細 PHASE2_WINDOWS.md。
AUTO MIX接続、cache/nudge UI、MIDI配線は次回。main統合はユーザー承認後。

状態: 未着手 → 開発中 → 実装済み → テスト済み → 実機確認済み。不具合ありは併記。

|Phase|機能|状態|検証範囲|
|---|---|---|---|
|1|WAV/MP3/FLAC/AIFF decode、2 deck mixer|テスト済み|合成ファイル・DSP単体|
|1|波形、D&D、再生UI|実装済み|UI確認はTEST_CHECKLIST参照|
|1|簡易BPM|テスト済み|120 BPMクリック音。一般楽曲精度未検証|
|1|ミックス試聴|テスト済み|位置復元・中断・再実行|
|1|イベントJSON/JSONL・復旧変換|テスト済み|非破壊記録の基礎。完全再現は未着手|
|1|Windows build|テスト済み|exe起動と実出力ストリームをWindows11で検証|
|1|GitHub Actions CI|実装済み|各runの成功状態・artifactはActionsで確認|
|2|ビートグリッド・小節頭・同期・タイムストレッチ|開発中|グリッド/オフセット推定/Quantize/SYNC(レート再生)はテスト済み。音程維持タイムストレッチと小節頭の実曲精度は未着手|
|2|EQ/GAIN/FILTER/Hot Cue/Cue/Loop/Quantize|テスト済み|合成信号・DSP単体。UI配線済み、実機聴感は未確認|
|2|ライブラリ/タグ/SQLite/reKordbox XML|開発中|SQLiteライブラリ/プレイリスト/rekordbox XML読込はテスト済み。UI未配線|
|2|APC40初代/MKII/DDJ-RB/MIDI Learn/LED|開発中|マッピング/Learn/LEDのロジックはテスト済み。MIDI入出力配線と実機は未確認|
|2|ヘッドホン別出力|未着手|独立バスが必要|
|3|構造・エネルギー・キー・ボーカル解析|未着手|確信度と手動修正必須|
|3|8種ミックス/フレーズ配置/連続試聴|未着手|対象ジャンルで主観評価|
|4|同時WAV録音・操作再現・タイムライン|未着手|90分録音とクラッシュ復旧試験|
|4|Undo/Redo・automation・warp・project save|未着手|元音源参照方式|
|5|Live10 ALS/track stems/MIDI export|未着手|実際のALSテンプレートが必要|
|5|Live11/12 compatibility|未着手|対応宣言不可|

最優先: 実曲・長時間再生の計測 → 平滑化/音声基盤改善 → ビートグリッド/同期 → EQ/MIDI。

Phase 2 (2026-11-24, muse/phase2-development): 平滑化+ソフトリミッター、EQ/GAIN/FILTER、
Cue/HotCue/Loop/Quantize/SYNC、ビートグリッド+キー推定+解析キャッシュ、自動ミックス計画、
MIDIマッピング基盤を実装し自動テスト30件通過。詳細は PHASE2_NOTES.md。
