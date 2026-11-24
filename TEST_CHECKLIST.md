# Test checklist

## Phase 2 — 2026-11-24 / Linux (このVM) / Python 3.12、合成信号のみ

- [x] 既存14件 + Phase 2新規16件 = 自動テスト30件通過 (pytest)
- [x] パラメータ平滑化: 急なミュートでもサンプル間ジャンプ < 0.05 (クリック防止)
- [x] ソフトリミッター: 大入力でも出力 ≤0.98、小信号は無変更、overload検出維持
- [x] EQ: 低域キルでDC信号が消える / 中立EQはビット透過 / フィルターLPで8kHz減衰
- [x] GAIN 2倍、Cue/HotCue、Loopラップ、Quantize吸着、SYNCレート算出
- [x] BeatGrid計算、キー推定とCamelot相性、解析キャッシュ往復
- [x] 自動ミックス計画のフレーズ整列と候補生成、MIDI Learn/LED/保存、ライブラリ/rekordbox XML
- [ ] スクリーンショット: このVMは libEGL が無く Qt オフスクリーン起動不可 (apt取得も停滞し中止)。Windows CI で要確認
- [ ] Windows exe ビルド / WASAPI実測 / 実機MIDI / 実曲精度 — すべて未確認 (PHASE2_NOTES 参照)

## Phase 1 — 2026-11-24 / Windows 11 build 26100 / Python 3.14.2 x64

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
- [x] 最終exe起動・実出力ストリーム: 終了コード0、両デッキ192000 frames、peak 0.09065
- [x] 最終exeの波形・120/125 BPM・再生位置表示をPNGで目視確認
- [x] 最終exeもMME報告遅延192ms、status/clampカウンター3。聴感/物理ループバック未検証
- [ ] 実曲のBPM精度と主観的なミックス品質
- [ ] 90分連続再生、負荷時のdropout、end-to-end latency
- [ ] Windows 10 / 別PC / クリーン環境
- [ ] ASIO / 独立ヘッドホン / MIDI実機
- [ ] 停電・ディスク容量不足・音声デバイス抜去
- [ ] Ableton Live 10/11/12

テスト初回はsandboxのOS一時フォルダー権限で6件setup error。
作業フォルダーにbasetempを指定した再実行で12件すべて通過。
これはアプリのテスト失敗ではなくテスト環境の書込制約だった。
初回exeはPATH上の別ソフトのICU DLL混入でQtCore起動失敗。build.pyのPATH限定と
PySide6-Essentialsへの構成統一で修正し、最終exeの実出力テストに合格。
OS経由のドラッグ&ドロップ、ファイル選択確定、全ボタンの手操作は未確認。
