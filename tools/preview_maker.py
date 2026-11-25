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
import shutil
import subprocess
import sys
import time
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


def make_entry(source_name, duration, preview_name, start, length, now=None) -> dict:
    return {
        "source": source_name,
        "title": title_from_filename(source_name),
        "duration_seconds": (round(float(duration), 3) if duration is not None else None),
        "preview": preview_name,
        "preview_start_seconds": round(float(start), 3),
        "preview_seconds": round(float(length), 3),
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
    """未処理かどうか: manifest に無い、またはプレビュー実ファイルが消えている。"""
    entry = find_entry(manifest, source_name)
    if entry is None:
        return True
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
# 処理本体
# ---------------------------------------------------------------------------


def process_file(source, out_dir, manifest, now=None,
                 preview_seconds=PREVIEW_SECONDS, start_fraction=START_FRACTION):
    """1 ファイルを処理して manifest を更新する。成功したらエントリ、失敗は None。"""
    source = Path(source)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(source)
    start, length = compute_window(duration, preview_seconds, start_fraction)
    taken = taken_preview_names(manifest, out_dir, exclude_source=source.name)
    preview_name = preview_name_for(source.name, taken)
    dest = out_dir / preview_name
    if not make_preview(source, dest, start, length):
        print(f"  [skip] 変換に失敗しました: {source.name}")
        return None
    entry = make_entry(source.name, duration, preview_name, start, length, now=now)
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
                    start_fraction=START_FRACTION) -> int:
    """未処理ファイルをまとめて処理する。処理できた件数を返す。

    ffmpeg が無い場合は明確に表示して何もしない (落とさない)。
    only_names が与えられたら、その名前のファイルだけを対象にする。
    """
    if not ffmpeg_available():
        print("  [warn] ffmpeg が見つかりません。プレビュー生成をスキップします。")
        print("         ffmpeg をインストールして PATH を通してください"
              " (tools/README_preview.md 参照)。")
        return 0
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
                            start_fraction=start_fraction) is not None:
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
    args = parser.parse_args(argv)

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

    if args.once:
        done = process_pending(inbox, out_dir, manifest,
                               preview_seconds=args.preview_seconds,
                               start_fraction=args.start_fraction)
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
                                start_fraction=args.start_fraction)
            previous_sizes = current_sizes
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n終了します。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
