# 自動プレビュー生成ツール (preview_maker)

下ごしらえ用フォルダに曲を入れておくと、プレビューを自動で用意して
くれる常駐ツールです。スマホの下ごしらえページからプレビューを
聴けるようにするための、PC 側の下準備ツールです。

プレビューは 2 段構えです:

1. **まずオンライン試聴を探します**（iTunes / Deezer の公式試聴、
   約30秒）。見つかればファイルは作らず、試聴 URL を manifest に
   記録します。
2. **無ければ従来どおり 90 秒のプレビュー MP3 を切り出します**
   （曲の 25% 地点から、フェード付き、128kbps）。

## 仕組み

1. 監視フォルダ（デフォルト `prep_inbox`）を 5 秒ごとにポーリングします。
2. 新しい音声ファイル（wav / mp3 / flac / m4a / aiff）を見つけたら、
   コピーが終わったことを確認してから処理します。
3. 曲情報（アーティスト / タイトル）を、ffprobe のタグから優先して
   取得します。タグが無ければファイル名から推定します
   （`Artist - Title.wav` 形式、先頭のトラック番号は除去）。
   両方そろった曲だけオンライン検索します（タイトルだけでは
   誤マッチが多すぎるため）。
4. オンライン検索の詳細は次の節を見てください。見つからなければ
   ローカル切り出し: **曲の 25% 地点から 90 秒**（末尾を超える場合は
   前倒し、90 秒以下の曲は頭から全体）、フェードイン/アウト各 1 秒、
   MP3 128kbps で出力フォルダ（デフォルト `prep_previews`）に生成します。
5. 出力フォルダに `manifest.json` が作られ、元ファイル名・タイトル・
   元の長さ・プレビューの情報・生成日時が記録されます。
   スマホ側の下ごしらえページは、将来この manifest を読んで
   プレビューを並べる想定です。
6. 処理済みの曲は再処理しません（manifest と出力ファイルの存在で判定）。
   プレビューを消せば、次回ポーリングで作り直されます。

## オンライン試聴の検索

- 検索先は **iTunes Search API** と **Deezer API** です。どちらも
  公開 API で、キー登録は要りません。iTunes → Deezer の順に探し、
  十分一致する候補が見つかった時点で採用します。
- 一致判定は、アーティスト名とタイトルを正規化（小文字化・記号除去・
  feat. 表記の除去）したうえでトークンの一致度をスコア化し、
  **0.7 以上**の最良候補だけを採用します。
- **版違いは別物として減点します**。タイトルに Remix / Extended /
  Radio Edit などの版表記が付いた候補は、元が無印なら大きく減点され、
  採用されにくくなります（逆に、ファイル側が Remix 版なら Remix 版
  同士は減点されません）。
- 通信に失敗したりタイムアウトした場合は、その曲は黙ってローカル
  切り出しにフォールバックします。ツールは落ちません。
- 検索は **1 曲につき 1 回だけ** 行い、結果は manifest にキャッシュ
  します。再実行しても再検索しません（マッチした曲はもちろん、
  マッチしなかった曲も `online_attempted_at` に検索日時が残り、
  再検索しません。再処理ループを避けるための設計です）。
  ファイル名やタグを直して再挑戦したい場合は、manifest.json から
  その曲のエントリを削除してから再実行してください。
- `--no-online` を付けるとオンライン検索をしません（常に切り出し）。
- **Bandcamp は対象外**です。公式の検索 API が提供されていないため
  自動検索できません（将来課題）。

### 誤マッチの可能性と確認方法

自動判定なので、別バージョンや同名曲を拾う可能性はゼロではありません。
manifest の各エントリには、マッチした曲の `matched_artist` /
`matched_title` / `matched_album` と、曲ページへの `source_url`、
一致度 `match_score` が残ります。スマホ側で聴く前に（あるいは聴いて
違和感があったら）、`source_url` を開いて本物の曲かどうか確認して
ください。間違っていたら、manifest からそのエントリを削除して
再実行すれば、その曲はローカル切り出しで作り直されます。

## 必要なもの

- Python 3.10 以上
- **ffmpeg**（変換に必須）。ffprobe（ffmpeg に同梱）があると元の長さも
  manifest に記録されます。無くても動きますが、長さは `null` になります。

### ffmpeg の入れ方 (Windows)

どれか 1 つで OK です。

- winget: `winget install Gyan.FFmpeg`
- Chocolatey: `choco install ffmpeg`
- 手動: https://www.gyan.dev/ffmpeg/builds/ から
  `ffmpeg-release-essentials.zip` をダウンロードして解凍し、
  中の `bin` フォルダに PATH を通す

入ったかどうかは、新しいコマンドプロンプトで `ffmpeg -version` を
実行して表示されれば OK です。

## 使い方

リポジトリのルートで:

```bat
rem 常駐モード（フォルダを監視し続ける。Ctrl+C で終了）
python tools\preview_maker.py

rem フォルダを指定する
python tools\preview_maker.py --watch D:\DJ\prep_inbox --out D:\DJ\prep_previews

rem 1 回だけ処理して終わる
python tools\preview_maker.py --once
```

| オプション | デフォルト | 意味 |
|---|---|---|
| `--watch` | `prep_inbox` | 監視フォルダ |
| `--out` | `prep_previews` | 出力フォルダ |
| `--once` | （常駐） | 1 回だけ処理して終了 |
| `--interval` | `5` | ポーリング間隔（秒） |
| `--preview-seconds` | `90` | プレビューの長さ（秒） |
| `--start-fraction` | `0.25` | 開始位置の割合 |
| `--no-online` | （検索する） | オンライン試聴の検索をしない |

ffmpeg が見つからない場合は、その旨を表示してスキップします
（ツールは落ちません）。表示が出たら ffmpeg のインストールと
PATH を確認してください。

## 出力をスマホに届ける

出力フォルダ（プレビュー MP3 + manifest.json）をスマホから届く場所に
置くのがポイントです。なお、オンライン試聴が見つかった曲は MP3 が
作られず manifest に URL が入るだけなので、その曲については
manifest.json さえ届けばスマホで聴けます。代表的な 2 パターン:

### パターン A: Google Drive 同期フォルダに出力する（一番かんたん）

PC に Google Drive（パソコン版）を入れて同期フォルダを作り、
`--out` にその中のフォルダを指定します。

```bat
python tools\preview_maker.py --out "G:\マイドライブ\mixcraft_previews"
```

プレビューが生成されるたびに自動で Drive に上がり、スマホの
Google Drive アプリからそのままストリーミング再生できます。
90 秒・128kbps なので 1 曲あたり約 1.4MB と軽く、通信量も抑えられます。

### パターン B: Tailscale 経由で PC から直接聴く

PC とスマホの両方に Tailscale を入れて同じネットワークに参加させれば、
出力フォルダを PC 上に置いたまま、スマホから直接アクセスできます
（例: PC 上で簡易 HTTP サーバーを立てる、共有フォルダを使う等）。
この場合はクラウドに上げないので、Drive の容量も使いません。
設定はネットワーク側の話になるため、ここでは触れません。

> どちらのパターンでも、ツール自体の使い方は同じです。
> `--out` の置き場所が違うだけです。

## manifest.json の形式

```json
{
  "version": 1,
  "updated_at": "2026-11-25T12:00:00+09:00",
  "previews": [
    {
      "source": "Artist - Title.wav",
      "title": "Artist - Title",
      "duration_seconds": 372.5,
      "preview_type": "local_cut",
      "preview": "Artist_-_Title_preview.mp3",
      "preview_start_seconds": 93.1,
      "preview_seconds": 90.0,
      "online_attempted_at": "2026-11-25T12:00:00+09:00",
      "generated_at": "2026-11-25T12:00:00+09:00"
    },
    {
      "source": "Other Artist - Other Title.flac",
      "title": "Other Artist - Other Title",
      "duration_seconds": 401.2,
      "preview_type": "online",
      "preview": null,
      "preview_url": "https://audio-ssl.itunes.apple.com/...",
      "preview_source": "itunes",
      "matched_title": "Other Title",
      "matched_artist": "Other Artist",
      "matched_album": "Other Album",
      "source_url": "https://music.apple.com/...",
      "match_score": 1.0,
      "online_attempted_at": "2026-11-25T12:00:00+09:00",
      "generated_at": "2026-11-25T12:00:00+09:00"
    }
  ]
}
```

- `preview_type`: `"online"`（オンライン試聴）か `"local_cut"`
  （ローカル切り出し）か。
- `duration_seconds`: 元ファイルの長さ。ffprobe が無い場合は `null`。
- `preview_start_seconds`: プレビューが元の何秒地点から始まるか。
  スマホ側で「本編のどこを聴いているか」を表示するのに使えます
  （local_cut のみ）。
- online の場合: `preview` は `null` で、代わりに `preview_url`
  （試聴 URL）、`preview_source`（`"itunes"` / `"deezer"`）、
  `matched_*`（マッチした曲の情報）、`source_url`（曲ページ）、
  `match_score`（一致度 0.0〜1.0）が入ります。
- `online_attempted_at`: オンライン検索を行った日時。入っていれば
  その曲は再検索しません。local_cut で検索しなかった場合は `null`。
