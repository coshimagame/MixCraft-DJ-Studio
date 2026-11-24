# Test checklist

2026-11-24 / Windows 11 build 26100 / Python 3.14.2 x64。

- [x] DSP・デコード・破損末尾復旧の自動テスト14件通過
- [x] クロスフェーダーA/B端点と中央の出力値
- [x] pause/seek/EOF/再スタート
- [x] 出力clamp
- [x] preview終了/中断/繰返しで状態復元
- [x] 合成WAV/MP3/FLAC/AIFFのデコードと44.1→48kHz変換
- [x] 元ファイルSHA256不変
- [x] 120 BPMクリック推定、無音でBPM不明
- [x] 操作イベントのブロック時刻とJSON保存
- [x] 不正音声ファイルで例外
- [x] Qt画面起動、ファイル選択ダイアログ表示、ネイティブ画面PNG保存
- [x] ソース版の実音声デバイス出力: 両デッキ192000 frames進行、非ゼロ出力
- [x] ソース版MME報告レイテンシ192ms、status/clampカウンター3（低遅延合格ではない）
- [ ] 実曲のBPM精度と主観的なミックス品質
- [ ] 90分連続再生、負荷時のdropout、end-to-end latency
- [ ] Windows 10 / 別PC / クリーン環境
- [ ] ASIO / 独立ヘッドホン / MIDI実機
- [ ] 停電・ディスク容量不足・音声デバイス抜去
- [ ] Ableton Live 10/11/12

テスト初回はsandboxのOS一時フォルダー権限で6件setup error。
作業フォルダーにbasetempを指定した再実行で12件すべて通過。
これはアプリのテスト失敗ではなくテスト環境の書込制約だった。
