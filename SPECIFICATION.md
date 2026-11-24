# Specification / 設計

## 対象
Windows 10/11 x64優先、Ubuntuは将来対応。Phase 1はスタンドアロンのローカルファイル再生。
UIは日英併記。専用言語切替は将来。4デッキ化、非破壊編集、Live連携を次段階とする。

## 技術選定
|候補|判断|
|---|---|
|C++ / JUCE|製品版の低遅延DSP候補。今回未採用。商用/OSS条件と移行コストの評価が必要|
|C++ / Qt|長期UI候補。今回は同じQtのPythonバインディングで試遊を優先|
|Python / PySide6|採用。ネイティブデスクトップUIと解析実装速度を優先|
|librosa|高度なビート・キー・構造解析で検討。今回未導入|
|FFmpeg|将来の追加デコード候補。ビルドごとのライセンス確認が必要|
|Rubber Band / SoundTouch|音程維持テンポ処理候補。今回未導入・未対応|
|PortAudio / sounddevice|採用。2ch出力とデバイス列挙|
|RtMidi|Phase 2候補。今回未導入|
|SQLite|解析キャッシュ・ライブラリ用にPhase 2で導入予定|

## 処理分離
app.py: Qtメインスレッドが表示と入力を担当。ThreadPoolExecutorの2ワーカーでデコード・解析。
engine.py: PortAudioコールバックがSimpleQueueのコマンドをブロック先頭で適用。
ファイル読み書き・Qt操作は音声コールバックから実行しない。
音声は48kHz float32 stereoへ正規化。monoは左右複製、多チャンネルは先頭2chのみ。
全曲RAM方式・最大30分/曲。NumPyの割当とPython GILがあるため製品品質のRT保証はない。
BPMはRMSエネルギー差分の自己相関。60〜200 BPM探索後、90未満は倍テンポに補正する。
アンビエント・変拍子・生演奏には適さず、ビート位相や小節頭は検出しない。

## ミックス
A gain = cos(x*pi/2), B gain = sin(x*pi/2), x=0..1。
各デッキのchannel volumeを乗算、合算後master、最後に±0.98でclamp。
previewはA末尾とB先頭を指定秒数重ねる。試聴中はサンプル単位でフェード。
試聴終了/中断で元の位置・再生状態・crossを復元。バー単位試聴と同期は未実装。

## イベント
schema=1。frameはアプリ音声エンジン起動からの累積出力サンプル数。
sample_rate=48000。action, deck (0/1/null), valueを保存。
loadは絶対パス、playはbool、seekはフレーム、volume/cross/masterは0..1、previewは秒。
stop_previewは試聴を復元。適用精度は512frames（約10.67ms）のブロック境界。
DAC実時間の測定、ファイルfingerprint、完全な再現レンダラーは今後追加。

## 製品版で必須の改善
固定バッファのC++音声エンジン、ロックフリーキュー、パラメータ平滑化、lookahead limiter、
ストリーミングデコード、解析キャッシュ、キー/ビートグリッド修正、別系統のヘッドホン出力。
