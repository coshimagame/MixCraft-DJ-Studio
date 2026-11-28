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
    # Pass 28 (bar alignment): the deck's bar-offset stepper —
    # which beat of the analysed grid starts the bar (0..3).
    "bar_offset": {"en": "BAR", "jp": "小節"},
    "bar_offset_tip": {
        "en": "Bar offset — which beat starts the bar (0-3). "
              "Analysis finds the beats but not the downbeat; "
              "this moves the bar lines (waveform ticks and the "
              "Live export's bar alignment) by whole beats.",
        "jp": "小節オフセット ― どの拍が小節の頭か（0〜3）。"
              "解析で分かるのは拍の位置までで、小節の頭はここで"
              "合わせます。小節線（波形の目盛りと Live 書き出し"
              "の小節合わせ）が拍単位でずれます。",
    },
    # --- performance pads ------------------------------------------
    "cue_set": {"en": "CUE SET", "jp": "キュー設定"},
    "cue": {"en": "CUE", "jp": "キュー"},
    "hot1": {"en": "HOT 1", "jp": "ホット1"},
    "hot1_set": {"en": "HOT1 SET", "jp": "ホット1設定"},
    "quantize": {"en": "QUANTIZE", "jp": "クオンタイズ"},
    "sync": {"en": "SYNC", "jp": "シンク"},
    "sync_tip": {
        "en": "Tempo + beat-phase sync when both beat grids are "
              "confident (tempo only otherwise). Pitch shifts too.",
        "jp": "テンポ＋拍位相同期（両方の拍グリッドが確かな時。"
              "そうでなければテンポのみ）。音程も変わります。",
    },
    # Pass 26: SYNC fell back to tempo-only for this track — the
    # status bar says so plainly instead of faking an alignment.
    "sync_tempo_only": {
        "en": "SYNC: tempo only — a beat grid here is not confident",
        "jp": "シンク：テンポのみ（拍グリッドの確信が低い）",
    },
    "set_bpm_tip": {
        "en": "SET BPM — drag or scroll to move the set tempo "
              "(±6 BPM); every deck rate follows.",
        "jp": "セットBPM ― ドラッグ／スクロールでセットのテンポを"
              "動かせます（±6 BPM）。デッキの速度も追随します。",
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
    # Pass 24 (user report 2026-11-27: 「ファイルを選ぶになった」
    # — the base picker opened unexplained, in the app's own
    # folder, and he stalled): the picker explains itself in
    # plain words, the choice is remembered, and every failure
    # is a message box, not a silent status line.
    "live_dialog_title": {
        "en": "Export for Live",
        "jp": "Live用に書き出し",
    },
    "live_base_explain": {
        "en": "Choose the template set (.als) you saved from Live — "
              "the one with Cue Out set to 3/4, Cue mode on, and one "
              "test clip in it. If you saved it with Live's 'Save as "
              "Template', look in Documents > Ableton > User Library "
              "> Templates.",
        "jp": "Liveで保存したテンプレートのセット（.als）を選んでください。"
              "Cue Out 3/4・Cueモードで、テストクリップが1つ入った物です。"
              "Liveの「テンプレートとして保存」を使った場合は、ドキュメントの "
              "Ableton > User Library > Templates フォルダにあります。",
    },
    "live_base_reason_missing": {
        "en": "That file could not be found.",
        "jp": "そのファイルが見つかりませんでした。",
    },
    "live_base_reason_not_als": {
        "en": "That file could not be read as a Live set (.als).",
        "jp": "Liveのセット（.als）として読めませんでした。",
    },
    "live_base_reason_few_tracks": {
        "en": "That set has fewer than two audio tracks "
              "(two are needed, for decks A and B).",
        "jp": "オーディオトラックが2本ありません"
              "（デッキA/B用に2本必要です）。",
    },
    "live_base_unusable": {
        "en": "That file cannot be used: {reason} Please choose again.",
        "jp": "選んだファイルは使えません： {reason} もう一度選んでください。",
    },
    "live_base_remembered_bad": {
        "en": "The remembered template ({path}) cannot be used: "
              "{reason} Please choose it again.",
        "jp": "前に選んだテンプレート（{path}）が使えませんでした： "
              "{reason} 選び直してください。",
    },
    # Pass 25: a template.als placed beside the app is used
    # automatically; if it is unusable, say so and fall back to
    # the remembered base / picker.
    "live_base_bundled_bad": {
        "en": "The template.als beside the app cannot be used: "
              "{reason} Please choose a base set instead.",
        "jp": "アプリと同じフォルダの template.als が使えません"
              "でした： {reason} 代わりに土台のセットを選んで"
              "ください。",
    },
    "live_export_done_dialog": {
        "en": "Your Live set is written.\n\nSaved to: {path}\n"
              "{clips} clips • {envelopes} envelopes\nTemplate: {base}",
        "jp": "Live用セットを書き出しました。\n\n保存先: {path}\n"
              "クリップ {clips} • エンベロープ {envelopes}\n"
              "テンプレート: {base}",
    },
    "live_export_events_copy": {
        "en": "A copy of the performance record was saved "
              "with it: {path}",
        "jp": "演奏の記録のコピーも一緒に保存しました: {path}",
    },
    "live_export_error_dialog": {
        "en": "The export could not be completed.\n\n{error}",
        "jp": "書き出しできませんでした。\n\n{error}",
    },
    "device_auto": {"en": "AUTOMATIC", "jp": "自動（ASIO優先）"},
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
        "en": "Audio active • {device} • {host} • 48 kHz / 512 frames "
              "• latency {ms} ms (reported) • autosaving actions",
        "jp": "音声アクティブ • {device} • {host} • 48 kHz / 512 frames "
              "• レイテンシ {ms} ms（報告値）• 操作を自動保存中",
    },
    # Pass 23: the chosen output would not open — the app fell
    # back to the system default rather than staying silent.
    "audio_fallback": {
        "en": "Output {device} could not be opened — playing "
              "through the system default instead",
        "jp": "出力 {device} を開けなかったので、システム既定の出力で"
              "鳴らしています",
    },
    "audio_error": {
        "en": "Audio unavailable: {error}",
        "jp": "出力エラー: {error}",
    },
    # Pass 25: the DDJ-RB was visible but would not open — say so
    # plainly, with the likeliest everyday cause named as a
    # possibility, never as a fact.
    "audio_rb_busy_hint": {
        "en": "The DDJ-RB could not be opened (another app may "
              "be using it — Live / rekordbox)",
        "jp": "DDJ-RBを開けませんでした（他のアプリが使用中の可能性"
              " — Live / rekordbox）",
    },
    # Pass 25: the device picker's per-device state words (the
    # ladder outcome also rides on the picker's tooltip and in
    # audio-diagnostics.json beside the settings).
    "audio_state_opened": {"en": "in use", "jp": "使用中"},
    "audio_state_failed": {
        "en": "could not open: {error}",
        "jp": "開けませんでした: {error}",
    },
    "audio_state_absent": {"en": "not found", "jp": "見つかりません"},
    "audio_state_untried": {"en": "not tried yet", "jp": "未試行"},
    "load_both_first": {
        "en": "Load both decks first",
        "jp": "両デッキに音声を読み込んでください",
    },
    "setlist_error": {
        "en": "Setlist error: {error}",
        "jp": "セットリスト読込エラー: {error}",
    },
    # Pass 24: set-list drag & drop and row double-click loading.
    "setlist_drop_added": {
        "en": "Added {count} track(s) to the set list",
        "jp": "セットリストに {count} 曲追加しました",
    },
    "setlist_drop_rejected": {
        "en": "Not audio files, skipped: {names}",
        "jp": "音声ファイルではないので追加できませんでした: {names}",
    },
    # Pass 25: a drop whose URLs carry no local file (e.g. dragged
    # out of a web page) — a drop must never fail silently.
    "setlist_drop_no_local": {
        "en": "That drop had no files this PC can read — drag "
              "audio files from Explorer instead",
        "jp": "そのドロップには読み込めるファイルがありません"
              "でした — エクスプローラーから音声ファイルを"
              "ドラッグしてください",
    },
    "setlist_entry_no_file": {
        "en": "That entry has no local file to load",
        "jp": "その曲には読み込めるファイルがありません",
    },
    "setlist_entry_loaded": {
        "en": "Loading '{title}' to Deck {deck}",
        "jp": "「{title}」をデッキ{deck}に読み込んでいます",
    },
    "setlist_row_tip": {
        "en": "Double-click: load to the standby deck",
        "jp": "ダブルクリックで待機デッキに読込",
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
