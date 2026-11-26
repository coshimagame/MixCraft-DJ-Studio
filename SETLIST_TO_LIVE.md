# セットリスト → Ableton Live 連携 (setlist → Live)

スマホの下ごしらえで組んだセットリストを、Ableton Live の Session View に
クリップとして並んだ状態の `.als` に変換するまでの手順書。
実装は `live_plan.py`（セットリスト → クリッププラン JSON）と
`als_writer.py`（プラン → テンプレート .als への注入）。

```
スマホ下ごしらえ ──書き出し──> setlist.json
                                  │  NAS Mw-fA\MixCraft\setlists 経由で PC へ
                                  ▼
PC: python live_plan.py ──> plan.json (クリッププラン)
                                  │
PC: テンプレート .als ──> python als_writer.py ──> gig.als
                                  │
                                  ▼
                     Live で gig.als を開く → Remote Script で演奏
```

## 前提 (設計判断・確定分)

- セット BPM は開始時に固定し、演奏中は動かさない。プランの `set_bpm` が
  生成 .als のセットテンポにも書かれる。
- 曲はセットリスト順にクリップとして事前に並べる。デッキは交互:
  1,3,5… 曲目 → Deck A、2,4,6… 曲目 → Deck B
  (Remote Script の前提: Track 1 = Deck A / Track 2 = Deck B)。
- rekordbox キューの変換規則 (library.py と同じ): Hot Cue → クリップの
  開始オフセット、ループマーク → クリップのループ範囲、
  メモリーキュー → ロケーター (プラン JSON 内。後述の制限参照)。

## 手順

### 1. スマホでセットリストを書き出し、NAS に置く

下ごしらえページで曲順を組み、`mixcraft-setlist v1` の JSON を書き出す
(形式は SETLIST_FORMAT.md)。NAS の `Mw-fA\MixCraft\setlists` に置く。
曲の実ファイルは `Mw-fA\MixCraft\tracks`、preview_maker の `manifest.json`
は `Mw-fA\MixCraft\previews` という既存の運用に合わせる。

### 2. PC でクリッププランを作る

```bat
python live_plan.py setlist.json --tracks D:\path\to\tracks --manifest D:\path\to\manifest.json --out plan.json
```

- 実ファイルの解決は 3 段: セットリスト内の `path` → manifest 照合
  (エントリの `source` が元ファイル名) → 曲フォルダ内のファイル名照合。
- **解決できなかった曲は推測で埋めず、理由付きで表示される。**
  その曲はクリップにならないので、曲名の綴りや manifest を直して
  再実行する。
- `--tracks` は絶対パスで指定するのがおすすめ (生成 .als の FileRef に
  そのまま書かれるため)。

### 3. テンプレート .als を用意する (初回だけ)

Live でいつものテンプレート (Deck A / B の 2 本の AudioTrack に
Utility + EQ Three + Auto Filter、クロスフェーダーの A/B 割り当て、
Cue Out 3/4) を開き、**試しにどれか 1 つクリップを入れて**から
名前を付けて保存する (例: `MixCraft_Template.als`)。

- 試しクリップが 1 つ入っていると、als_writer はそのクリップ XML を
  複製して使う (本物の Live が書いた XML の形をそのまま借りる経路)。
- クリップが 1 つも無いテンプレートでも動くが、その場合は最小
  スケルトンでクリップを作り、警告が出る (実機未検証の経路)。
- スロット (シーン) が曲数に対して足りない場合、als_writer は
  テンプレート自身のスロット / Scene を複製して増やす。ただしこの
  自動拡張も実機未検証なので、テンプレート側でシーンを多めに
  作っておく方が確実。

### 4. .als を生成する

```bat
python als_writer.py MixCraft_Template.als plan.json gig_2026-11-28.als
```

- ベース .als は**絶対に上書きしない** (出力先に同じ名前を指定すると
  エラーになる)。ベースが見つからない場合も、その旨と対処が表示される。
- 生成時に差し替えるのは: クリップ名、FileRef のパス、Warp
  (曲の BPM で 2 点の WarpMarker を張り直し)、開始オフセットと
  ループ範囲 (拍換算)、セットのテンポ (= set_bpm)。
  デバイチェーンやミキサーなどベースの他要素は触らない。

### 5. Live で開いて演奏する

生成した .als を Live で開き、コントロールサーフェスに MixCraft
Remote Script を選び、DDJ-RB でデッキを操作する (セットアップは
remote_script/SETUP.md と TEMPLATE_GUIDE.md)。

## 検証済み / 実機確認が要るもの

### 検証済み (自動テスト、tests/test_live_plan.py / test_als_writer.py)

- プラン変換: デッキ交互とスロット順、キュー変換 (オフセット / ループ /
  ロケーターの拍換算)、未解決曲の報告、set_bpm の引き継ぎ。
- .als 注入: 合成した最小ベース fixture に対して注入 → 再オープン →
  XML をパースし、クリップの順序・パス・Warp 値・テンポ・
  Id の一意性・非クリップ内容 (他トラックや MasterTrack 等) の保存を検証。
- WAV の長さ・サンプルレートはヘッダから実測して DefaultDuration に書く。

### 実機確認が要るもの (未検証)

- **生成した .als を本物の Live 10.1.43 がエラーなく開けるか。**
  .als のスキーマは非公開で、こちらの検証は XML 構造まで。
  初回は必ずコピーしたテンプレートで試し、開けたらすぐ別名保存する。
- Warp の音 (2 点線形の WarpMarker で実曲が合うか)、クリップの発火を
  Remote Script 経由で問題なく行えるか。
- スロット / Scene の自動拡張が Live に受け入れられるか。
- スケルトン経路 (ベースにクリップが無い場合) で作ったクリップが
  Live で正しく読み込まれるか。

## 既知の制限 (v1)

- メモリーキューのロケーターはプラン JSON には入るが、**.als への
  書き込みは未実装**。Arrangement ロケーターの XML は実機サンプルで
  裏取りできていないため、推測で書かないことにした。必要なら Live 上で
  プラン JSON を見ながら手で打つ。
- FileRef は絶対パス (Path) で解決し、RelativePath は空にする。
  曲フォルダを引っ越したらプランから作り直す。
- WAV 以外 (MP3 等) の DefaultDuration は、セットリストの長さと
  既存サンプルレートからの換算値 (近似)。
- クリップの Id はベース内の最大値より上から振り直す。ベースに
  既存クリップがある場合、そのクリップはプランの曲で置き換わる
  (テンプレートは連携専用に分けておくのが安全)。
