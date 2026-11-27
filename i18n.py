"""UI language strings for the MixAble window (pass 10).

User direction, 2026-11-27: 「英語表記と日本語表記 切替ボタンで切り
替えたい じゃないと文字ガチャガチャして」 — the bilingual labels
('Load / 読込', 'NOW / 再生中', ...) looked cluttered, so the window
now shows ONE language at a time, switched by the header toggle.

This module is the pure half of that: the (en, jp) string table and
the small formatters for the dynamic texts (set-list status badges,
the summary line's state text, the mix timer's remaining caption).
No Qt imports — app.py owns the widgets and the registry of setters;
everything here is unit-tested in tests/test_i18n.py.

Conventions: DEFAULT_LANG is 'jp' (the user's language). Strings
that are already single-language DJ/hardware terminology in BOTH
languages (the knob captions GAIN / EQ HIGH / EQ MID / EQ LOW /
FILTER, the DECK A/B chips, the BPM / KEY column heads, unit labels)
are deliberately NOT in this table — they never mixed languages, and
real hardware prints them the same way in Japan. app.py keeps them
as literals and the pass-10 notes say so.
"""

DEFAULT_LANG = "jp"
LANGS = ("en", "jp")

STRINGS = {
    # --- header ---------------------------------------------------
    "header_mode": {
        "en": "DJ MODE  •  2 DECK PERFORMANCE  •  PHASE 2 TRIAL",
        "jp": "DJモード  •  2デッキ パフォーマンス  •  試遊版",
    },
    # --- transport / deck info ------------------------------------
    "load": {"en": "Load", "jp": "読込"},
    "play": {"en": "▶ Play", "jp": "▶ 再生"},
    "pause": {"en": "Ⅱ Pause", "jp": "Ⅱ 一時停止"},
    "reset": {"en": "■ Reset", "jp": "■ 先頭"},
    "no_track": {"en": "No track loaded", "jp": "未読込"},
    "loading": {"en": "Loading… ", "jp": "解析中… "},
    "load_failed": {"en": "Load failed", "jp": "読込失敗"},
    "key_line": {"en": "Key {key} ({camelot})", "jp": "キー {key} ({camelot})"},
    "drop_hint": {"en": "DROP AUDIO HERE", "jp": "音声ファイルをドロップ"},
    # --- performance pads ------------------------------------------
    "cue_set": {"en": "Cue Set", "jp": "キュー設定"},
    "cue": {"en": "Cue", "jp": "キュー"},
    "hot1": {"en": "Hot 1", "jp": "ホット1"},
    "hot1_set": {"en": "Hot1 Set", "jp": "ホット1設定"},
    "quantize": {"en": "Quantize", "jp": "クオンタイズ"},
    "sync": {"en": "SYNC", "jp": "シンク"},
    "sync_tip": {
        "en": "Tempo match only. Pitch shifts too. No beat-phase sync.",
        "jp": "テンポのみ一致。音程も変わります。ビート位相同期なし。",
    },
    # --- SET BPM cluster -------------------------------------------
    "set_bpm": {"en": "SET BPM", "jp": "セットBPM"},
    "level": {"en": "LEVEL", "jp": "レベル"},
    # --- mixer block ------------------------------------------------
    "mixer_caption": {"en": "MIXER", "jp": "ミキサー"},
    "channel_volume": {"en": "CHANNEL\nVOLUME", "jp": "チャンネル\nボリューム"},
    "master": {"en": "MASTER", "jp": "マスター"},
    "crossfader": {"en": "CROSSFADER", "jp": "クロスフェーダー"},
    # --- utility bar (transition audition + audio output) -----------
    "transition": {"en": "TRANSITION", "jp": "試聴"},
    "audition_tip": {
        "en": "A's tail → B's head (no tempo sync)",
        "jp": "Aの末尾 → Bの先頭（テンポ同期なし）",
    },
    "seconds_item": {"en": "{n} seconds", "jp": "{n} 秒"},
    "preview": {"en": "▶ Preview transition", "jp": "▶ ミックス試聴"},
    "stop_preview": {"en": "Stop preview", "jp": "試聴終了"},
    "save_events": {"en": "Save events JSON", "jp": "操作保存"},
    "output": {"en": "OUTPUT", "jp": "出力"},
    "device_auto": {"en": "Automatic", "jp": "自動（WASAPI優先）"},
    "device_default": {"en": "System default", "jp": "システム既定"},
    "apply": {"en": "Apply", "jp": "適用"},
    # --- set list / mix timer ---------------------------------------
    "setlist_caption": {"en": "SET LIST", "jp": "セットリスト"},
    "remaining": {"en": "LEFT", "jp": "残り"},
    "over": {"en": "OVER", "jp": "超過"},
    "start": {"en": "START", "jp": "開始"},
    "stop": {"en": "STOP", "jp": "停止"},
    "timer_reset": {"en": "RESET", "jp": "リセット"},
    "open_setlist": {"en": "Open setlist JSON", "jp": "セットリストを開く"},
    "setlist_dialog": {"en": "Setlist JSON", "jp": "セットリスト"},
    "setlist_empty": {
        "en": "No setlist loaded — open the JSON exported from the phone prep page",
        "jp": "未読込 — スマホの下ごしらえページで書き出した JSON を開いてください",
    },
    "summary_line": {
        "en": "SET BPM {bpm}  •  {count} TRACKS  •  TOTAL {total}  •  {state}",
        "jp": "セットBPM {bpm}  •  {count} 曲  •  合計 {total}  •  {state}",
    },
    "state_not_started": {"en": "NOT STARTED", "jp": "未開始"},
    "state_playing": {"en": "PLAYING TRACK {n}", "jp": "再生中 {n} 曲目"},
    "state_started": {"en": "STARTED", "jp": "開始済み"},
    "col_title": {"en": "TITLE / ARTIST", "jp": "タイトル / アーティスト"},
    "col_time": {"en": "TIME", "jp": "時間"},
    "col_status": {"en": "STATUS", "jp": "状態"},
    "badge_played": {"en": "PLAYED", "jp": "済"},
    "badge_playing": {"en": "NOW", "jp": "再生中"},
    "badge_next": {"en": "NEXT", "jp": "次"},
    "badge_queued": {"en": "QUEUED", "jp": "待機"},
    "untitled": {"en": "(untitled)", "jp": "(無題)"},
    "listen": {"en": "Listen ↗", "jp": "試聴 ↗"},
    # --- status bar / notices ---------------------------------------
    "ready": {"en": "Ready", "jp": "準備完了"},
    "notice": {
        "en": "BPM and key are estimates. SYNC matches tempo only and "
              "shifts pitch. No MIDI I/O or ALS support. Source files "
              "are never modified.",
        "jp": "BPM・キーは推定値。SYNCはテンポのみ一致し音程も変わります。"
              "MIDI入出力・ALS未対応。元ファイルは変更しません。",
    },
    "audio_active": {
        "en": "Audio active • 48 kHz / 512 frames • {host} • latency "
              "{ms} ms (reported) • autosaving actions",
        "jp": "音声アクティブ • 48 kHz / 512 frames • {host} • レイテンシ "
              "{ms} ms（報告値）• 操作を自動保存中",
    },
    "audio_error": {
        "en": "Audio unavailable: {error}",
        "jp": "出力エラー: {error}",
    },
    "load_both_first": {
        "en": "Load both decks first",
        "jp": "両デッキに音声を読み込んでください",
    },
    "setlist_error": {
        "en": "Setlist error: {error}",
        "jp": "セットリスト読込エラー: {error}",
    },
    "setlist_loaded": {
        "en": "Setlist loaded: {name} • {count} tracks • total {total} "
              "• {clips} clip conversions held",
        "jp": "セットリスト読込: {name} • {count} 曲 • 合計 {total} "
              "• クリップ変換 {clips} 件を保持中",
    },
}

_current = DEFAULT_LANG


def set_current(lang):
    """Set the process-wide current language (painted widgets such
    as the waveform read it — they have no setter to register)."""
    global _current
    if lang in LANGS:
        _current = lang


def current():
    return _current


def tr(key, lang=None):
    """The string for `key` in `lang` (default: the current one)."""
    return STRINGS[key][lang or _current]


def status_badge(status, lang=None):
    """Set-list status badge for a language-neutral status key
    ('played' / 'playing' / 'next' / 'queued')."""
    return tr("badge_" + status, lang)


def summary_state_text(started, current_index, track_count, lang=None):
    """The summary line's state text, derived from the same fields
    setlist.display_summary exposes (its own state_text stays
    Japanese — it is shared with the CLI; the window localizes)."""
    lang = lang or _current
    if not started:
        return tr("state_not_started", lang)
    if 0 <= current_index < track_count:
        return tr("state_playing", lang).format(n=current_index + 1)
    return tr("state_started", lang)
