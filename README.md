# MixAble DJ Studio

> 2026-11-26 に **MixCraft** から **MixAble** へ改名しました。旧名 MixCraft で書かれた
> 文書・記録も、同じ製品を指します。

Mw-fActory — オフラインで動作するWindows向け2デッキDJ試遊版 v0.2.0-trial (Phase 2)。

## Phase 2 試遊版

EQ HIGH/MID/LOW、GAIN、LP/HP FILTER、Cue Set/Cue、Hot1 Set/Hot1、Loop In/Out/Exit、
Quantize、SYNC、キー/Camelot推定を追加。EQ/FILTER中央が中立です。
SYNCは押した時点の相手デッキのテンポへレートを合わせます。音程も変わり、
ビート位相の同期や相手テンポへの継続追従はありません。
Quantizeは推定ビートへ位置を吸着します。小節頭の自動認識ではありません。
Automatic出力はWASAPIの既定出力を優先します。接続エラー時は出力を選び直してください。
本版は `muse/phase2-development` の試遊用で、mainへの統合は承認待ちです。
詳細・検証範囲は [PHASE2_WINDOWS.md](PHASE2_WINDOWS.md)。

## 起動

Windows ZIPをすべて展開し、`MixAble.exe` を実行してください。
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

開発初期の試遊版です。音程維持テンポ変更、ビート位相同期、ライブラリUI、MIDI入出力、
編集タイムライン、同時WAV録音、ALS出力は未実装です。
ライブラリ・MIDI・自動ミックス候補・代替出力はPythonモジュールの土台のみです。
BPMは60〜200の探索範囲から推定し、90未満はダンス音楽向けに倍テンポとして表示します。誤推定があります。
1曲30分までを全量RAMに展開します。90分セットの運用検証は未実施です。
48kHz / 512フレーム処理ですが、実際の出力遅延はOS・デバイスに依存します。
Pythonコールバックはハードリアルタイムを保証しません。ASIOは未検証です。
0.90からソフトニーで出力を制限します。これは先読みリミッターではなく、強い入力は歪みます。
音量/EQ/FILTERは5msで平滑化します。再生開始、Cue、seek、任意Loop境界のクリック除去は未対応です。

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
Windows trial実行から `MixAble-Windows-x64` artifactを取得できます（GitHubログインが必要）。

Live 10 ALS互換性は未確認であり、対応済みではありません。
