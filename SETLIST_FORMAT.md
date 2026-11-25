# セットリスト JSON 形式 (mixcraft-setlist v1)

スマホの下ごしらえページ ⇄ PC (MixCraft) でセットリストを受け渡すための
共通 JSON 形式の正本仕様。実装は `setlist.py`（読み書き・検証・変換）、
検証は `tests/test_setlist.py`。

## 形式

```json
{
  "format": "mixcraft-setlist",
  "version": 1,
  "exported_at": "2026-11-25T12:00:00+09:00",
  "set_bpm": 122,
  "started": false,
  "current_index": -1,
  "tracks": [
    {"title": "曲名", "artist": "アーティスト or null", "bpm": 120.0,
     "key": "8A", "duration_seconds": 390,
     "preview_url": "試聴URL or null",
     "preview_source": "itunes/deezer/local/file or null",
     "source_url": "曲ページURL or null",
     "local_preview": "切り出しプレビューのファイル名 or null"}
  ]
}
```

### トップレベル

| フィールド | 型 | 意味 |
|---|---|---|
| `format` | string | 固定で `"mixcraft-setlist"`。違う値は別形式として弾く |
| `version` | int | 固定で `1`。対応外の version は弾く |
| `exported_at` | string / null | 書き出し時刻 (ISO 8601)。保存時に無ければ自動で打つ |
| `set_bpm` | number / null | セット全体の固定 BPM（開始時に決めて演奏中は動かさない） |
| `started` | bool | 演奏開始済みかどうか |
| `current_index` | int | 再生中の曲の位置 (0 始まり)。未開始は `-1` |
| `tracks` | array | 曲順どおりの配列。再生済み・再生中は固定、まだの曲は並べ替え可 |

### tracks[] の各曲

| フィールド | 型 | 意味 |
|---|---|---|
| `title` | string | 曲名 |
| `artist` | string / null | アーティスト名 |
| `bpm` | number / null | 原曲 BPM (Live 側で set_bpm に Warp する) |
| `key` | string / null | Camelot 表記 (`8A` など) |
| `duration_seconds` | int / null | 曲の長さ (秒) |
| `preview_url` | string / null | オンライン試聴の URL (iTunes / Deezer 等) |
| `preview_source` | string / null | 試聴の出どころ: `itunes` / `deezer` / `local` / `file` |
| `source_url` | string / null | 曲ページの URL |
| `local_preview` | string / null | preview_maker が切り出したプレビューのファイル名 (manifest と同じ置き場にある前提) |

## 読み取りの寛容さ (PC 側 `load_setlist`)

- 欠けたフィールドは `null` / デフォルト (`started=false`, `current_index=-1`) で補う
- 別名も受け付ける: `name`→`title`、`tempo`→`bpm`、`camelot` / `tonality`→`key`、
  `length_seconds`→`duration_seconds`、`duration` は `"6:30"` のような
  mm:ss 文字列 (h:mm:ss も可) でもよい
- ただし `format` / `version` が違うデータは `SetlistFormatError` で明確に弾く

## 運用

1. スマホの下ごしらえページで曲順を組み、この形式の `setlist.json` を書き出す
2. NAS / Google Drive の共有フォルダに `setlist.json` を置く
   (preview_maker の `manifest.json` と同じ置き場に並べる運用)
3. PC 側で `setlist.py` が読み込み、`setlist_to_clip_tracks()` で
   library.py の track dict に変換して、Live のクリップ準備につなげる

v1 はこの「ファイル手渡し」で成立する (自動同期は将来分)。
逆方向 (PC → スマホ) も同じ形式で、`save_setlist()` が書き出す。

## preview_maker の manifest からの変換

NAS/Drive の置き場にある `manifest.json` からも setlist を組み立てられる:

```bash
python setlist.py convert manifest.json setlist.json
```

- `preview_type="online"` のエントリ → `preview_url` / `preview_source` / `source_url` を持つ曲
- `preview_type="local_cut"` のエントリ → `local_preview` (切り出しファイル名) を持つ曲
- 両者が混在してよい。manifest には BPM / キーが無いので、その項目は null
  (スマホ側や rekordbox XML 取り込みで後から埋める)

## CLI

```bash
python setlist.py check setlist.json            # 形式検証 + 曲数・合計時間の表示
python setlist.py convert manifest.json out.json # manifest → setlist 変換
```
