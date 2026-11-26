# MIXLIST — 演奏後の .als からトラックリストを自動作成

## 何をするものか

本番ではセット BPM が固定でキューポイントも事前に決まっているため、
DDJ-RB の BROWSE ノブで演奏中に曲順を変えても成立する。その代わり、
計画したセットリスト (SETLIST_TO_LIVE.md の流れ) と実際に掛かった
順番は食い違いうる。

`mixlist.py` は、演奏後に保存した Live セット (.als) の Arrangement に
記録されたクリップを読み、「実際に掛かった順番」のトラックリストを
貼り付け用テキストで書き出す (SoundCloud / Mixcloud の説明欄など)。

```
# Mixlist — 2026-11-27 / Total 14:00 / 120 BPM
1. 00:00 First — Alpha
2. 03:20 Second — Beta
3. 06:40 Third — Gamma
4. 10:00 First — Alpha
```

## いつ・どうやって使うか

録音 (演奏) が終わったら:

1. Live でセットを**保存する** (これを忘れると Arrangement の記録が
   .als に残らない)。
2. PC で実行する:

```
python mixlist.py 演奏後のセット.als
python mixlist.py 演奏後のセット.als --out tracklist.txt
```

省略時は標準出力に表示する。ヘッダの日付は .als の保存日、
Total は最後のクリップの終わりまで、時刻はセットのテンポで
拍から換算する。

## 読み取りとマージの規則

- デッキは最初の 2 本の AudioTrack (Deck A / Deck B)。Arrangement の
  クリップ (MainSequencer の ArrangerAutomation 配下の AudioClip)
  だけを対象にし、Session のクリップは数えない。
- 開始位置はクリップの Time 属性 (拍)。テンポはセットの
  Tempo (MasterTrack) から読む。セット BPM 固定が前提なので、
  テンポオートメーションは追わない。
- 時間順に連続する同じ曲のクリップは 1 件にまとめる (Hot Cue の
  打ち直し、ループの再トリガ)。同じ曲の判定は、同じソースファイル →
  同じクリップ名 → Hot Cue クリップ ("Title – Hot Cue X") の
  タイトル部分一致、の順。別の曲が間に入った後の再登場は新しい
  エントリになる。
- タイトルは推測しない。表示はクリップ名 ("Title - Artist" を
  "Title — Artist" に整形) → ファイル名 ("Artist - Title" 規則) の順。
  名前もファイルも無いクリップは飛ばす。

## 正直な限界

- 読むのは**保存された .als** である。演奏後に保存し忘れた分、
  Arrangement に記録されなかったクリップは出てこない (セッションで
  鳴らしただけの音は Arrangement に残らない)。
- 合成 fixture での検証は tests/test_mixlist.py で済んでいるが、
  本物の Live 10 が録音した .als で読めるかは実機確認が別途必要
  (als_writer と同じ立場)。
