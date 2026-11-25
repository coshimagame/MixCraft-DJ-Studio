# 自動プレビュー生成ツール (preview_maker)

下ごしらえ用フォルダに曲を入れておくと、**90 秒のプレビュー MP3 を自動で
作ってくれる**常駐ツールです。スマホの下ごしらえページからプレビューを
聴けるようにするための、PC 側の下準備ツールです。

## 仕組み

1. 監視フォルダ（デフォルト `prep_inbox`）を 5 秒ごとにポーリングします。
2. 新しい音声ファイル（wav / mp3 / flac / m4a / aiff）を見つけたら、
   コピーが終わったことを確認してから変換します。
3. プレビューは **曲の 25% 地点から 90 秒**（末尾を超える場合は前倒し、
   90 秒以下の曲は頭から全体）、フェードイン/アウト各 1 秒、
   MP3 128kbps で出力フォルダ（デフォルト `prep_previews`）に生成します。
4. 出力フォルダに `manifest.json` が作られ、元ファイル名・タイトル・
   元の長さ・プレビュー名・開始位置・生成日時が記録されます。
   スマホ側の下ごしらえページは、将来この manifest を読んで
   プレビューを並べる想定です。
5. 処理済みの曲は再処理しません（manifest と出力ファイルの存在で判定）。
   プレビューを消せば、次回ポーリングで作り直されます。

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

ffmpeg が見つからない場合は、その旨を表示してスキップします
（ツールは落ちません）。表示が出たら ffmpeg のインストールと
PATH を確認してください。

## 出力をスマホに届ける

出力フォルダ（プレビュー MP3 + manifest.json）をスマホから届く場所に
置くのがポイントです。代表的な 2 パターン:

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
      "preview": "Artist_-_Title_preview.mp3",
      "preview_start_seconds": 93.1,
      "preview_seconds": 90.0,
      "generated_at": "2026-11-25T12:00:00+09:00"
    }
  ]
}
```

- `duration_seconds`: 元ファイルの長さ。ffprobe が無い場合は `null`。
- `preview_start_seconds`: プレビューが元の何秒地点から始まるか。
  スマホ側で「本編のどこを聴いているか」を表示するのに使えます。
