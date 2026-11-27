"""UI language strings for the MixAble window (pass 10).

User direction, 2026-11-27: 「英語表記と日本語表記 切替ボタンで切り
替えたい じゃないと文字ガチャガチャして」 — the bilingual labels
('Load / 読込', 'NOW / 再生中', ...) looked cluttered, so the window
now shows ONE language at a time, switched by the header toggle.

This module is the pure half of that: the (en, jp) string table and
the small formatters for the dynamic texts (set-list status badges,
the mix timer's remaining caption). No Qt imports — app.py owns the
widgets and the registry of setters; everything here is
unit-tested in tests/test_i18n.py.

Pass 13 (user direction, 2026-11-27: 「情報量もへらしていこう」)
removed the strings whose displays left the window: the header
subtitle, the bottom notice paragraph, the set-list summary line
(and its state text), and the utility bar's section captions. The
key line also changed: one notation only (Camelot) — see key_line.

Conventions: DEFAULT_LANG is 'jp' (the user's language). Strings
that are already single-language DJ/hardware terminology in BOTH
languages (the knob captions GAIN / HI / MID / LOW / FILTER —
bare band names since pass 20 restored HI / MID; the letters
'EQ' appear nowhere — the DECK A/B chips, the BPM / KEY column
heads, unit labels)
are deliberately NOT in this table — they never mixed languages, and
real hardware prints them the same way in Japan. app.py keeps them
as literals and the pass-10 notes say so.

Pass 11 (rugged console): the English LABELS (buttons, captions,
display placeholders) are uppercase — console lettering to match
the dot-matrix readouts. Status-bar prose and dialog titles keep
sentence case; they are messages, not labels.
"""

DEFAULT_LANG = "jp"
LANGS = ("en", "jp")

STRINGS = {
    # --- transport / deck info ------------------------------------
    "load": {"en": "LOAD", "jp": "読込"},
    "play": {"en": "▶ PLAY", "jp": "▶ 再生"},
    "pause": {"en": "Ⅱ PAUSE", "jp": "Ⅱ 一時停止"},
    "reset": {"en": "■ RESET", "jp": "■ 先頭"},
    "no_track": {"en": "NO TRACK LOADED", "jp": "未読込"},
    "loading": {"en": "LOADING… ", "jp": "解析中… "},
    "load_failed": {"en": "LOAD FAILED", "jp": "読込失敗"},
    # Pass 13: ONE notation — the Camelot code, matching the set
    # list's KEY column. The key name rides as the sub-line's
    # tooltip (app.py), not as a second notation in parentheses.
    "key_line": {"en": "KEY {camelot}", "jp": "キー {camelot}"},
    "drop_hint": {"en": "DROP AUDIO HERE", "jp": "音声ファイルをドロップ"},
    # Pass 15 (deck state markers): ON AIR is the filled chip on the
    # deck that is actually playing; MONITOR is the hollow chip —
    # the headphone-monitor select. The set-list row marker is the
    # short form of the monitor label.
    "on_air": {"en": "ON AIR", "jp": "再生中"},
    "monitor_on": {"en": "MONITOR", "jp": "モニター中"},
    "monitor_off": {"en": "MONITOR", "jp": "モニター"},
    "monitor_short": {"en": "MON", "jp": "モニター"},
    # --- performance pads ------------------------------------------
    "cue_set": {"en": "CUE SET", "jp": "キュー設定"},
    "cue": {"en": "CUE", "jp": "キュー"},
    "hot1": {"en": "HOT 1", "jp": "ホット1"},
    "hot1_set": {"en": "HOT1 SET", "jp": "ホット1設定"},
    "quantize": {"en": "QUANTIZE", "jp": "クオンタイズ"},
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
    "audition_tip": {
        "en": "Transition preview into the monitored deck "
              "(default B; no tempo sync)",
        "jp": "モニター中のデッキへの繋ぎ試聴（無指定はB・テンポ同期なし）",
    },
    # Pass 20: the audition-length caption beside the seconds
    # combo (the one utility-bar caption that came back — the
    # combo's meaning did not read without it).
    "audition_length": {"en": "AUDITION LENGTH", "jp": "試聴の長さ"},
    "seconds_item": {"en": "{n} SECONDS", "jp": "{n} 秒"},
    "preview": {"en": "▶ PREVIEW TRANSITION", "jp": "▶ ミックス試聴"},
    "stop_preview": {"en": "STOP PREVIEW", "jp": "試聴終了"},
    "save_events": {"en": "SAVE EVENTS JSON", "jp": "操作保存"},
    # Pass 21: the performance → Live export (the 'fix it in
    # Live' half of 「アプリで完結 Liveで修整が理想」).
    "export_live": {"en": "EXPORT FOR LIVE", "jp": "Live用に書き出し"},
    "live_need_performance": {
        "en": "No performance recorded yet — play something first",
        "jp": "まだ演奏の記録がありません。先に演奏してください",
    },
    "live_need_tempo": {
        "en": "No SET BPM source — load a setlist or a Deck A track first",
        "jp": "SET BPM の元がありません。セットリストかデッキAの曲を先に読み込んでください",
    },
    "live_base_dialog": {
        "en": "Live template (.als)",
        "jp": "Live テンプレート (.als)",
    },
    "live_out_dialog": {
        "en": "Save performance set (.als)",
        "jp": "演奏セットの保存先 (.als)",
    },
    "live_export_done": {
        "en": "Live set written: {path} • {clips} clips • {envelopes} envelopes",
        "jp": "Live用セットを書き出しました: {path} • クリップ {clips} • エンベロープ {envelopes}",
    },
    "live_export_error": {
        "en": "Live export failed: {error}",
        "jp": "Live書き出し失敗: {error}",
    },
    "device_auto": {"en": "AUTOMATIC", "jp": "自動（WASAPI優先）"},
    # Pass 23: DDJ-RB MIDI connection state (status bar).
    "midi_connected": {"en": "DDJ-RB connected", "jp": "DDJ-RB 接続済み"},
    "midi_disconnected": {"en": "DDJ-RB not connected", "jp": "DDJ-RB 未接続"},
    "device_default": {"en": "SYSTEM DEFAULT", "jp": "システム既定"},
    "apply": {"en": "APPLY", "jp": "適用"},
    # --- set list / mix timer ---------------------------------------
    "setlist_caption": {"en": "SET LIST", "jp": "セットリスト"},
    # Pass 16: the mix timer's 'remaining' / 'over' captions left
    # with their readout — the timer shows elapsed only.
    "start": {"en": "START", "jp": "開始"},
    "stop": {"en": "STOP", "jp": "停止"},
    "timer_reset": {"en": "RESET", "jp": "リセット"},
    "open_setlist": {"en": "OPEN SETLIST JSON", "jp": "セットリストを開く"},
    "setlist_dialog": {"en": "Setlist JSON", "jp": "セットリスト"},
    "col_title": {"en": "TITLE / ARTIST", "jp": "タイトル / アーティスト"},
    "col_time": {"en": "TIME", "jp": "時間"},
    "col_status": {"en": "STATUS", "jp": "状態"},
    "badge_played": {"en": "PLAYED", "jp": "済"},
    "badge_playing": {"en": "NOW", "jp": "再生中"},
    "badge_next": {"en": "NEXT", "jp": "次"},
    "badge_queued": {"en": "QUEUED", "jp": "待機"},
    "untitled": {"en": "(untitled)", "jp": "(無題)"},
    # --- status bar -------------------------------------------------
    "ready": {"en": "Ready", "jp": "準備完了"},
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
