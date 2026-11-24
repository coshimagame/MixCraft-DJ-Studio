# MixCraft DJ Studio

Mw-fActory — オフラインで動作するWindows向け2デッキDJ試遊版 v0.1.0。

## 起動

Windows ZIPをすべて展開し、`MixCraftDJStudio.exe` を実行してください。
`_internal` フォルダーはexeと同じ場所に必要です。Pythonや有料APIは不要です。
`demo` のオリジナル合成WAV、または自分の音声ファイルを各デッキにドロップします。
Playで再生し、チャンネル音量・MASTER・A/Bクロスフェーダーでミックスします。
波形をクリックすると再生位置を変更できます。Resetで停止して先頭へ戻ります。
ミックス試聴はAの末尾とBの先頭を8/16/32/64秒で等電力クロスフェードし、終了時に元の再生状態へ戻ります。
出力を変更する場合はAUDIO OUTPUTで選択してApplyを押してください。

## 実装済み

- WAV / MP3 / FLAC / AIFF読込、2デッキ再生、一時停止、シーク
- バックグラウンド読込・簡易BPM推定、全体振幅波形
- 各チャンネル音量、マスター、等電力クロスフェーダー、出力デバイス選択
- ミックス部分試聴、操作JSON書き出し、操作JSONLの自動保存
- 日本語・英語併記のダークUI

## 制限

開発初期の試遊版です。音程維持テンポ変更、同期、EQ、Hot Cue、Loop、キー解析、
ライブラリ、MIDI、編集タイムライン、同時WAV録音、ALS出力は未実装です。
BPMは60〜200の探索範囲から推定し、90未満はダンス音楽向けに倍テンポとして表示します。誤推定があります。
1曲30分までを全量RAMに展開します。90分セットの運用検証は未実施です。
48kHz / 512フレーム処理ですが、実際の出力遅延はOS・デバイスに依存します。
Pythonコールバックはハードリアルタイムを保証しません。ASIOは未検証です。
出力振幅を±0.98に制限しますが、過大入力による歪みを防ぐリミッターではありません。
フェーダー平滑化は未実装のため急操作でクリック音が生じる可能性があります。

操作ログは `%LOCALAPPDATA%/MixCraftDJStudio/sessions/*.jsonl` に保存します。
40ms周期でflushします。異常終了直前や停電時の完全保存は保証しません。
保存ログには音声ファイルの絶対パスが含まれます。共有前に確認してください。
復旧変換は `python recover.py session.jsonl recovered.json`。
復旧JSONからのプレイ再現・編集UIはPhase 4予定です。
元の音声ファイルは読み取りのみです。アプリ実行中のネットワーク通信はありません。

## 開発

Python 3.14 x64。詳細は [BUILD_GUIDE.md](BUILD_GUIDE.md)。
状態一覧は [ROADMAP.md](ROADMAP.md)、検証は [TEST_CHECKLIST.md](TEST_CHECKLIST.md)。
[GitHub Actions](https://github.com/coshimagame/MixCraft-DJ-Studio/actions) の成功した
Windows trial実行から `MixCraftDJStudio-Windows-x64` artifactを取得できます（GitHubログインが必要）。

Live 10 ALS互換性は未確認であり、対応済みではありません。
