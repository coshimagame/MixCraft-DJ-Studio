#!/usr/bin/env python3
"""下ごしらえフォルダの自動プレビュー生成ツール (MixCraft DJ Studio).

指定フォルダ (デフォルト: prep_inbox) に音声ファイルを入れておくと、
ポーリングで検出して 90 秒のプレビュー MP3 を自動生成し、
出力フォルダ (デフォルト: prep_previews) に manifest.json と一緒に置く。
スマホの下ごしらえページが将来この manifest を読む想定。

使い方 (Windows / どこでも):
    python tools/preview_maker.py                      # 常駐 (prep_inbox を監視)
    python tools/preview_maker.py --watch D:\\prep --out D:\\previews
    python tools/preview_maker.py --once               # 1 回だけ処理して終了

変換には ffmpeg が必要 (PATH にあること)。ffprobe があれば元の長さも
manifest に記録する。ffmpeg が無いファイルは理由を表示してスキップし、
ツール全体は落とさない。

オンライン試聴 (2026-11-25 追加): ローカル切り出しの前に、iTunes Search
API と Deezer API (どちらも公開・キー不要) でその曲の公式試聴 (約30秒)
を探し、アーティスト/タイトルが十分一致した候補があれば、切り出しの
代わりに試聴 URL を manifest に記録する (preview_type="online")。
見つからない・一致しない・通信できない場合は、従来どおりローカル
切り出し (preview_type="local_cut") にフォールバックする。
`--no-online` でオンライン検索を無効化できる。検索は 1 曲につき
1 回だけ行い、結果は manifest にキャッシュする (再実行で再検索しない)。

プレビュー仕様:
    - 長さ 90 秒、開始位置は曲の 25% 地点 (開始+90 秒が曲尾を超える場合は
      末尾に収まるよう前倒し。90 秒以下の曲は頭から全体)
    - フェードイン / フェードアウト各 1 秒、MP3 128kbps

純粋ロジック (開始位置計算、出力名決定、manifest 操作) は ffmpeg に
依存しない関数に分けてあり、tests/test_preview_maker.py で検証する。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 定数 (プレビュー仕様)
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aiff", ".aif"}
PREVIEW_SECONDS = 90.0        # プレビューの長さ
START_FRACTION = 0.25         # 開始位置 = 曲の 25% 地点
FADE_SECONDS = 1.0            # フェードイン / アウト
PREVIEW_BITRATE = "128k"      # MP3 ビットレート
DEFAULT_INBOX = "prep_inbox"
DEFAULT_OUT = "prep_previews"
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
DEFAULT_INTERVAL = 5.0        # 監視ポーリング間隔 (秒)

# オンライン試聴の検索 (公開 API、キー不要)
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
ONLINE_MATCH_THRESHOLD = 0.7  # このスコア以上でオンライン試聴を採用する
ONLINE_VERSION_PENALTY = 0.35  # 版表記 (Remix / Extended 等) が食い違う場合の減点
ONLINE_SEARCH_LIMIT = 10      # 検索 API に要求する候補数
ONLINE_TIMEOUT = 10.0         # HTTP タイムアウト (秒)
ONLINE_USER_AGENT = "MixCraftPreviewMaker/1.0"
# 版違いとみなす単語 (タイトル内の括弧書き・末尾表記から拾う)
VERSION_WORDS = {
    "remix", "extended", "edit", "mix", "version", "radio", "club", "dub",
    "instrumental", "acoustic", "live", "remaster", "remastered",
    "vip", "bootleg", "rework", "flip",
}

# ---------------------------------------------------------------------------
# 純粋ロジック (ffmpeg 非依存、単体テスト対象)
# ---------------------------------------------------------------------------


def is_audio_file(path) -> bool:
    """対応する音声ファイルかどうかを拡張子で判定する。"""
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def compute_window(duration, preview_seconds=PREVIEW_SECONDS,
                   start_fraction=START_FRACTION):
    """プレビューの (開始秒, 長さ秒) を返す。

    - duration が不明 (None) または preview_seconds 以下なら頭から。
      長さは duration が分かればその分だけ、分からなければ preview_seconds。
    - 長い曲は start_fraction 地点から。開始+長さが曲尾を超える場合は
      末尾にちょうど収まるよう開始を前倒しする。
    """
    if duration is None or duration <= 0:
        return 0.0, float(preview_seconds)
    duration = float(duration)
    if duration <= preview_seconds:
        return 0.0, duration
    start = duration * start_fraction
    start = min(start, duration - preview_seconds)
    return start, float(preview_seconds)


def title_from_filename(filename) -> str:
    """ファイル名からタイトルを推定する (拡張子を除き _ を空白に)。"""
    stem = Path(filename).stem
    title = " ".join(stem.replace("_", " ").split())
    return title or stem


_TRACK_NUMBER_RE = re.compile(r"^\d{1,3}\s*(?:[-–—.]|\s)\s*")


def guess_track_info(filename) -> dict:
    """ファイル名から (artist, title) を推定する。

    - 拡張子を除き、_ は空白として扱う
    - 先頭のトラック番号 ("01 - ", "01. ", "01 ") は取り除く
    - "Artist - Title" 形式なら最初の " - " で分ける
    - 区切りが無ければ artist は None、全体を title とする
    """
    stem = Path(filename).stem
    text = " ".join(stem.replace("_", " ").split())
    text = _TRACK_NUMBER_RE.sub("", text, count=1)
    if " - " in text:
        artist, title = text.split(" - ", 1)
        return {"artist": artist.strip() or None, "title": title.strip()}
    return {"artist": None, "title": text}


def normalize_tokens(text) -> set:
    """文字列を比較用トークン集合にする (小文字化、アクセント除去、記号除去)。"""
    if not text:
        return set()
    decomposed = unicodedata.normalize("NFKD", str(text))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = plain.replace("&", " and ")
    return set(re.findall(r"[^\W_]+", plain.lower(), flags=re.UNICODE))


_FEAT_BRACKET_RE = re.compile(r"[\(\[](?:feat\.?|ft\.?|featuring)\b[^\)\]]*[\)\]]",
                              re.IGNORECASE)
_FEAT_TAIL_RE = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\b.*$", re.IGNORECASE)
_BRACKET_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")


def strip_feat(text) -> str:
    """feat. / ft. / featuring 以降の表記を取り除く (括弧内も含む)。"""
    if not text:
        return ""
    text = _FEAT_BRACKET_RE.sub(" ", str(text))
    text = _FEAT_TAIL_RE.sub(" ", text)
    return text


def parse_title(title):
    """タイトルを (本体トークン集合, 版トークン集合) に分ける。

    括弧書きの中身と、" - " の後ろに続く版表記 (Extended Mix 等) を
    版トークンとして分離する。feat. 表記はどちらにも数えない。
    """
    text = strip_feat(title)
    version_tokens = set()
    for inner in _BRACKET_RE.findall(text):
        version_tokens |= normalize_tokens(inner)
    core_text = _BRACKET_RE.sub(" ", text)
    # feat. 以降を切った残りなど、閉じていない括弧の中身も版情報として拾う
    unclosed = re.search(r"[\(\[]([^\)\]]*)$", core_text)
    if unclosed:
        version_tokens |= normalize_tokens(unclosed.group(1))
        core_text = core_text[:unclosed.start()]
    segments = core_text.split(" - ")
    core = segments[0]
    for segment in segments[1:]:
        tokens = normalize_tokens(segment)
        if tokens & VERSION_WORDS:
            version_tokens |= tokens
        else:
            core += " - " + segment
    return normalize_tokens(core), version_tokens


def _jaccard(a, b) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap(a, b) -> float:
    """小さい方の集合に対する一致率 (片方が他方を含むと 1.0)。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def score_match(query_artist, query_title, cand_artist, cand_title) -> float:
    """検索候補の一致度を 0.0〜1.0 で返す。

    タイトルは本体同士の Jaccard、アーティストは overlap (候補側に
    feat. 相手などが並んでいても落ちないように) で評価し、平均する。
    版トークン (Remix / Extended 等) が食い違えば大きく減点する。
    タイトル本体が全く一致しない候補は 0。
    """
    q_core, q_version = parse_title(query_title)
    c_core, c_version = parse_title(cand_title)
    title_sim = _jaccard(q_core, c_core)
    if title_sim == 0.0:
        return 0.0
    artist_sim = _overlap(normalize_tokens(strip_feat(query_artist)),
                          normalize_tokens(strip_feat(cand_artist)))
    score = 0.5 * title_sim + 0.5 * artist_sim
    if q_version != c_version and (q_version or c_version):
        score -= ONLINE_VERSION_PENALTY
    return max(0.0, min(1.0, score))


def sanitize_stem(stem) -> str:
    """出力ファイル名に使える形に整える (英数・日本語・._- 以外は _ に)。"""
    out = []
    for ch in stem:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        elif ch.isspace():
            out.append("_")
        else:
            out.append("_")
    cleaned = "".join(out).strip("._")
    return cleaned or "track"


def preview_name_for(source_name, taken) -> str:
    """プレビューの出力ファイル名を決める。taken と衝突したら _2, _3… を付ける。"""
    base = sanitize_stem(Path(source_name).stem) + "_preview"
    candidate = base + ".mp3"
    n = 2
    while candidate in taken:
        candidate = f"{base}_{n}.mp3"
        n += 1
    return candidate


def now_iso(now=None) -> str:
    """ローカルタイムゾーン付き ISO 8601 文字列。now を渡せばそれを使う。"""
    dt = now if now is not None else datetime.now().astimezone()
    return dt.isoformat(timespec="seconds")


def empty_manifest() -> dict:
    return {"version": MANIFEST_VERSION, "updated_at": None, "previews": []}


def find_entry(manifest, source_name):
    """source (元ファイル名) でエントリを探す。無ければ None。"""
    for entry in manifest.get("previews", []):
        if entry.get("source") == source_name:
            return entry
    return None


def make_entry(source_name, duration, preview_name, start, length, now=None,
               online_attempted_at=None) -> dict:
    return {
        "source": source_name,
        "title": title_from_filename(source_name),
        "duration_seconds": (round(float(duration), 3) if duration is not None else None),
        "preview_type": "local_cut",
        "preview": preview_name,
        "preview_start_seconds": round(float(start), 3),
        "preview_seconds": round(float(length), 3),
        "online_attempted_at": online_attempted_at,
        "generated_at": now_iso(now),
    }


def make_online_entry(source_name, duration, match, now=None) -> dict:
    """オンライン試聴が見つかった曲の manifest エントリ。

    ローカルプレビューファイルは作らないので preview は None。
    マッチ情報 (matched_*) はスマホ側で後から確認・取り消しできるよう
    必ず残す。
    """
    return {
        "source": source_name,
        "title": title_from_filename(source_name),
        "duration_seconds": (round(float(duration), 3) if duration is not None else None),
        "preview_type": "online",
        "preview": None,
        "preview_url": match["preview_url"],
        "preview_source": match["preview_source"],
        "matched_title": match.get("matched_title"),
        "matched_artist": match.get("matched_artist"),
        "matched_album": match.get("matched_album"),
        "source_url": match.get("source_url"),
        "match_score": match.get("match_score"),
        "online_attempted_at": now_iso(now),
        "generated_at": now_iso(now),
    }


def upsert_entry(manifest, entry, now=None) -> dict:
    """エントリを source キーで追加/置換する (同じ source は二重に入らない)。

    previews は source 名順に保ち、updated_at を更新する。
    """
    previews = [e for e in manifest.get("previews", [])
                if e.get("source") != entry.get("source")]
    previews.append(entry)
    previews.sort(key=lambda e: e.get("source", ""))
    manifest["previews"] = previews
    manifest["version"] = MANIFEST_VERSION
    manifest["updated_at"] = now_iso(now)
    return manifest


def taken_preview_names(manifest, out_dir, exclude_source=None) -> set:
    """使用中のプレビュー名の集合 (manifest 内 + 出力フォルダの実ファイル)。

    exclude_source の既存エントリ名は除く (再処理時に自分の名前を再利用できる)。
    """
    own = None
    if exclude_source:
        prev = find_entry(manifest, exclude_source)
        own = prev.get("preview") if prev else None
    taken = set()
    for entry in manifest.get("previews", []):
        if entry.get("source") == exclude_source:
            continue
        if entry.get("preview"):
            taken.add(entry["preview"])
    out = Path(out_dir)
    if out.is_dir():
        for f in out.iterdir():
            if f.is_file() and f.suffix.lower() == ".mp3" and f.name != own:
                taken.add(f.name)
    return taken


def needs_processing(source_name, out_dir, manifest) -> bool:
    """未処理かどうか。

    - エントリが無い → 処理する
    - preview_type="online" のエントリ → 処理しない (検索結果は manifest に
      キャッシュ済み。再検索はしない)
    - local_cut (および旧形式) → プレビューファイルが消えていたら作り直す。
      オンライン検索はエントリ作成時に 1 回だけ行う設計なので、local_cut の
      作り直しでは再検索しない (再処理ループにならない)
    """
    entry = find_entry(manifest, source_name)
    if entry is None:
        return True
    if entry.get("preview_type") == "online":
        return False
    preview = entry.get("preview")
    if not preview:
        return True
    return not (Path(out_dir) / preview).is_file()


def stable_sources(current_sizes, previous_sizes) -> set:
    """前回ポーリングと同じサイズで再び現れたファイル名の集合。

    コピー途中のファイルを掴まないための判定 (watch モード用)。
    """
    return {name for name, size in current_sizes.items()
            if previous_sizes.get(name) == size}


# ---------------------------------------------------------------------------
# manifest の読み書き
# ---------------------------------------------------------------------------


def load_manifest(out_dir) -> dict:
    path = Path(out_dir) / MANIFEST_NAME
    if not path.is_file():
        return empty_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_manifest()
    if not isinstance(data, dict) or not isinstance(data.get("previews"), list):
        return empty_manifest()
    data.setdefault("version", MANIFEST_VERSION)
    return data


def save_manifest(out_dir, manifest) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, out / MANIFEST_NAME)


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe (外部コマンド)
# ---------------------------------------------------------------------------


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_duration(source) -> float | None:
    """ffprobe で元の長さ (秒) を取る。取れなければ None。"""
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def parse_probe_tags(json_text) -> dict:
    """ffprobe の JSON 出力から {artist, title, duration} を取り出す (純粋関数)。

    タグキーの大文字小文字 (title/TITLE 等) は区別しない。壊れた入力では
    空の値を返す。
    """
    info = {"artist": None, "title": None, "duration": None}
    try:
        data = json.loads(json_text)
    except (TypeError, ValueError):
        return info
    fmt = data.get("format") if isinstance(data, dict) else None
    if not isinstance(fmt, dict):
        return info
    tags = fmt.get("tags")
    if isinstance(tags, dict):
        lowered = {str(k).lower(): v for k, v in tags.items()}
        artist = lowered.get("artist")
        title = lowered.get("title")
        info["artist"] = str(artist).strip() if artist else None
        info["title"] = str(title).strip() if title else None
    duration = fmt.get("duration")
    if duration is not None:
        try:
            info["duration"] = float(duration)
        except (TypeError, ValueError):
            pass
    return info


def probe_tags(source) -> dict:
    """ffprobe で format tags (artist / title) を取る。取れなければ空 dict。"""
    if shutil.which("ffprobe") is None:
        return {}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format_tags=artist,title",
             "-of", "json", str(source)],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    info = parse_probe_tags(result.stdout)
    return {"artist": info["artist"], "title": info["title"]}


def track_info_for(source) -> dict:
    """曲情報 (artist, title) を決める: ffprobe タグ優先、無ければファイル名推定。

    オンライン検索には artist と title の両方が要る (title だけでは
    誤マッチが多すぎるため)。どちらか欠けたら呼び出し側で検索を諦める。
    """
    tags = probe_tags(source)
    guess = guess_track_info(Path(source).name)
    return {
        "artist": tags.get("artist") or guess["artist"],
        "title": tags.get("title") or guess["title"],
    }


def build_ffmpeg_args(source, dest, start, length,
                      fade_seconds=FADE_SECONDS, bitrate=PREVIEW_BITRATE):
    """ffmpeg の引数リストを組み立てる (テスト可能な純粋関数)。"""
    fade = min(float(fade_seconds), float(length) / 2.0)
    fade_out_start = max(0.0, float(length) - fade)
    audio_filter = (f"afade=t=in:st=0:d={fade:.3f},"
                    f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}")
    return ["ffmpeg", "-y", "-ss", f"{float(start):.3f}", "-i", str(source),
            "-t", f"{float(length):.3f}", "-vn", "-af", audio_filter,
            "-codec:a", "libmp3lame", "-b:a", bitrate, str(dest)]


def make_preview(source, dest, start, length) -> bool:
    """ffmpeg でプレビューを生成する。失敗したら False (部分ファイルは消す)。"""
    try:
        result = subprocess.run(build_ffmpeg_args(source, dest, start, length),
                                capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or not Path(dest).is_file():
        Path(dest).unlink(missing_ok=True)
        return False
    return True


# ---------------------------------------------------------------------------
# オンライン試聴の検索 (iTunes / Deezer、公開 API・キー不要)
# ---------------------------------------------------------------------------


def default_fetcher(url, timeout=ONLINE_TIMEOUT):
    """URL を GET して JSON を dict で返す。失敗時は例外を送出する。"""
    request = urllib.request.Request(
        url, headers={"User-Agent": ONLINE_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_itunes_results(data) -> list:
    """iTunes Search API のレスポンスを候補 dict のリストにする (純粋関数)。"""
    candidates = []
    if not isinstance(data, dict):
        return candidates
    for item in data.get("results") or []:
        if not isinstance(item, dict) or not item.get("previewUrl"):
            continue
        candidates.append({
            "preview_source": "itunes",
            "preview_url": item["previewUrl"],
            "matched_title": item.get("trackName"),
            "matched_artist": item.get("artistName"),
            "matched_album": item.get("collectionName"),
            "source_url": item.get("trackViewUrl"),
        })
    return candidates


def parse_deezer_results(data) -> list:
    """Deezer API のレスポンスを候補 dict のリストにする (純粋関数)。"""
    candidates = []
    if not isinstance(data, dict) or data.get("error"):
        return candidates
    for item in data.get("data") or []:
        if not isinstance(item, dict) or not item.get("preview"):
            continue
        artist = item.get("artist") or {}
        album = item.get("album") or {}
        candidates.append({
            "preview_source": "deezer",
            "preview_url": item["preview"],
            "matched_title": item.get("title"),
            "matched_artist": artist.get("name"),
            "matched_album": album.get("title"),
            "source_url": item.get("link"),
        })
    return candidates


def search_itunes(artist, title, fetcher) -> list:
    query = urllib.parse.urlencode({
        "term": f"{artist} {title}", "media": "music", "entity": "song",
        "limit": ONLINE_SEARCH_LIMIT,
    })
    return parse_itunes_results(fetcher(f"{ITUNES_SEARCH_URL}?{query}"))


def search_deezer(artist, title, fetcher) -> list:
    query = urllib.parse.urlencode({
        "q": f"{artist} {title}", "limit": ONLINE_SEARCH_LIMIT,
    })
    return parse_deezer_results(fetcher(f"{DEEZER_SEARCH_URL}?{query}"))


def find_online_preview(info, fetcher=None, threshold=ONLINE_MATCH_THRESHOLD):
    """オンライン試聴の最良マッチを探す。見つからなければ None。

    iTunes → Deezer の順に探し、閾値以上の候補が出た時点でその候補を
    採用する (それ以上は検索しない)。通信失敗など例外が出たソースは
    静かに諦めて次へ進む。この関数は例外を送出しない。
    """
    artist = (info or {}).get("artist")
    title = (info or {}).get("title")
    if not artist or not title:
        return None
    fetch = fetcher if fetcher is not None else default_fetcher
    for search in (search_itunes, search_deezer):
        try:
            candidates = search(artist, title, fetch)
        except Exception:
            continue
        best = None
        for cand in candidates:
            score = score_match(artist, title,
                                cand.get("matched_artist"),
                                cand.get("matched_title"))
            if best is None or score > best["match_score"]:
                best = dict(cand, match_score=round(score, 3))
        if best is not None and best["match_score"] >= threshold:
            return best
    return None


# ---------------------------------------------------------------------------
# 処理本体
# ---------------------------------------------------------------------------


def process_file(source, out_dir, manifest, now=None,
                 preview_seconds=PREVIEW_SECONDS, start_fraction=START_FRACTION,
                 online=True, fetcher=None):
    """1 ファイルを処理して manifest を更新する。成功したらエントリ、失敗は None。

    手順: (1) online が有効で曲情報 (artist+title) が取れれば、まず
    オンライン試聴を探す。(2) 見つかれば試聴 URL を manifest に記録して
    終わる (ローカルファイルは作らない)。(3) 見つからなければ従来どおり
    ローカル切り出しにフォールバックする。オンライン検索はこの 1 回だけ
    行い、結果 (成否とも) はエントリに記録して再検索しない。
    """
    source = Path(source)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(source)

    online_attempted_at = None
    if online:
        info = track_info_for(source)
        if info.get("artist") and info.get("title"):
            online_attempted_at = now_iso(now)
            match = find_online_preview(info, fetcher=fetcher)
            if match is not None:
                entry = make_online_entry(source.name, duration, match, now=now)
                upsert_entry(manifest, entry, now=now)
                save_manifest(out_dir, manifest)
                print(f"  [online] {source.name} -> {match['preview_source']} "
                      f"試聴 (score {match['match_score']:.2f}: "
                      f"{match.get('matched_artist')} - {match.get('matched_title')})")
                return entry

    if not ffmpeg_available():
        print(f"  [skip] ffmpeg が無いため切り出しできません: {source.name}")
        return None
    start, length = compute_window(duration, preview_seconds, start_fraction)
    taken = taken_preview_names(manifest, out_dir, exclude_source=source.name)
    preview_name = preview_name_for(source.name, taken)
    dest = out_dir / preview_name
    if not make_preview(source, dest, start, length):
        print(f"  [skip] 変換に失敗しました: {source.name}")
        return None
    entry = make_entry(source.name, duration, preview_name, start, length,
                       now=now, online_attempted_at=online_attempted_at)
    upsert_entry(manifest, entry, now=now)
    save_manifest(out_dir, manifest)
    print(f"  [done] {source.name} -> {preview_name} "
          f"(start {start:.1f}s, {length:.1f}s)")
    return entry


def scan_inbox(inbox) -> dict:
    """監視フォルダ内の音声ファイルを {ファイル名: サイズ} で返す。"""
    inbox = Path(inbox)
    found = {}
    if not inbox.is_dir():
        return found
    for f in sorted(inbox.iterdir()):
        if f.is_file() and is_audio_file(f):
            try:
                found[f.name] = f.stat().st_size
            except OSError:
                continue
    return found


def process_pending(inbox, out_dir, manifest, only_names=None, now=None,
                    preview_seconds=PREVIEW_SECONDS,
                    start_fraction=START_FRACTION,
                    online=True, fetcher=None) -> int:
    """未処理ファイルをまとめて処理する。処理できた件数を返す。

    オンライン試聴は ffmpeg 不要なので、ffmpeg が無い環境でも試す。
    ローカル切り出しが必要なファイルだけ ffmpeg が無いと失敗する
    (理由を表示してスキップし、全体は止めない)。
    only_names が与えられたら、その名前のファイルだけを対象にする。
    """
    if not ffmpeg_available():
        print("  [warn] ffmpeg が見つかりません。ローカル切り出しはできません"
              " (オンライン試聴の検索は行います)。")
        print("         ffmpeg をインストールして PATH を通してください"
              " (tools/README_preview.md 参照)。")
    inbox = Path(inbox)
    count = 0
    for name in sorted(scan_inbox(inbox)):
        if only_names is not None and name not in only_names:
            continue
        if not needs_processing(name, out_dir, manifest):
            continue
        try:
            if process_file(inbox / name, out_dir, manifest, now=now,
                            preview_seconds=preview_seconds,
                            start_fraction=start_fraction,
                            online=online, fetcher=fetcher) is not None:
                count += 1
        except Exception as exc:  # 1 ファイルの失敗で全体を止めない
            print(f"  [skip] {name}: {exc}")
    return count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="下ごしらえフォルダの自動プレビュー生成ツール")
    parser.add_argument("--watch", default=DEFAULT_INBOX,
                        help=f"監視フォルダ (デフォルト: {DEFAULT_INBOX})")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"出力フォルダ (デフォルト: {DEFAULT_OUT})")
    parser.add_argument("--once", action="store_true",
                        help="1 回だけ処理して終了する (常駐しない)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"監視ポーリング間隔・秒 (デフォルト: {DEFAULT_INTERVAL})")
    parser.add_argument("--preview-seconds", type=float, default=PREVIEW_SECONDS,
                        help=f"プレビューの長さ・秒 (デフォルト: {PREVIEW_SECONDS})")
    parser.add_argument("--start-fraction", type=float, default=START_FRACTION,
                        help=f"開始位置の割合 (デフォルト: {START_FRACTION})")
    parser.add_argument("--no-online", action="store_true",
                        help="オンライン試聴の検索をしない (常にローカル切り出し)")
    args = parser.parse_args(argv)
    online = not args.no_online

    inbox = Path(args.watch)
    out_dir = Path(args.out)
    inbox.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out_dir)

    print("MixCraft preview_maker")
    print(f"  監視フォルダ: {inbox.resolve()}")
    print(f"  出力フォルダ: {out_dir.resolve()}")
    if ffmpeg_available():
        print("  ffmpeg: あり")
    else:
        print("  ffmpeg: なし (プレビューは生成されません。"
              "インストール方法は tools/README_preview.md 参照)")
    if shutil.which("ffprobe") is None:
        print("  ffprobe: なし (元の長さは manifest に null で記録されます)")
    print(f"  オンライン試聴: {'有効 (iTunes / Deezer)' if online else '無効 (--no-online)'}")

    if args.once:
        done = process_pending(inbox, out_dir, manifest,
                               preview_seconds=args.preview_seconds,
                               start_fraction=args.start_fraction,
                               online=online)
        print(f"完了: {done} 件のプレビューを生成しました。")
        return 0

    print(f"監視中... ({args.interval:.0f} 秒ごとに確認、Ctrl+C で終了)")
    previous_sizes = {}
    try:
        while True:
            current_sizes = scan_inbox(inbox)
            # コピー完了を確認するため、前回と同じサイズで再出現したものだけ処理する
            ready = stable_sources(current_sizes, previous_sizes)
            if ready:
                process_pending(inbox, out_dir, manifest, only_names=ready,
                                preview_seconds=args.preview_seconds,
                                start_fraction=args.start_fraction,
                                online=online)
            previous_sizes = current_sizes
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n終了します。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
