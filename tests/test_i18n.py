"""UI language strings (pass 10): the (en, jp) table and its formatters.

The user asked for an EN ⇄ 日本語 toggle because the bilingual
labels ('Load / 読込', 'NOW / 再生中') looked cluttered. These tests
pin the table's hygiene — every key in both languages, the English
side free of Japanese text, the two sides actually different — and
the dynamic formatters the window renders with (status badges).
Pure stdlib; the widget side is covered by
tests/test_language_toggle.py.

Pass 13 removed the header subtitle, the notice, the summary line
(and its state text) and the utility captions from the table along
with their displays; the key line is Camelot-only now.
"""
import re

import i18n

CJK = re.compile(r'[ぁ-んァ-ヶ一-龥]')


def test_default_language_is_japanese():
    assert i18n.DEFAULT_LANG == 'jp'
    assert i18n.current() == 'jp'


def test_every_key_has_both_languages():
    for key, pair in i18n.STRINGS.items():
        assert set(pair) == {'en', 'jp'}, key
        assert pair['en'].strip(), key
        assert pair['jp'].strip(), key


def test_english_side_has_no_japanese():
    for key, pair in i18n.STRINGS.items():
        assert not CJK.search(pair['en']), (key, pair['en'])


def test_languages_actually_differ():
    # A toggle between identical strings would be a no-op the user
    # can see; every registered label must change with the language.
    for key, pair in i18n.STRINGS.items():
        assert pair['en'] != pair['jp'], key


def test_tr_defaults_to_current_and_accepts_explicit_lang():
    assert i18n.tr('load') == '読込'
    assert i18n.tr('load', 'en') == 'LOAD'
    assert i18n.tr('load', 'jp') == '読込'
    i18n.set_current('en')
    try:
        assert i18n.tr('load') == 'LOAD'
    finally:
        i18n.set_current('jp')


def test_status_badges_both_languages():
    expected = {
        'played': ('PLAYED', '済'),
        'playing': ('NOW', '再生中'),
        'next': ('NEXT', '次'),
        'queued': ('QUEUED', '待機'),
    }
    for status, (en, jp) in expected.items():
        assert i18n.status_badge(status, 'en') == en
        assert i18n.status_badge(status, 'jp') == jp


def test_format_templates_render_in_both_languages():
    assert i18n.tr('seconds_item', 'en').format(n=8) == '8 SECONDS'
    assert i18n.tr('seconds_item', 'jp').format(n=8) == '8 秒'
    # Pass 13: the key line is ONE notation — Camelot only.
    assert i18n.tr('key_line', 'jp').format(camelot='8A') == 'キー 8A'
    assert i18n.tr('key_line', 'en').format(camelot='8A') == 'KEY 8A'


def test_pass13_removed_strings_are_gone():
    # The displays left the window (header subtitle, notice, summary
    # line + state text, utility captions, row LISTEN button), so
    # their strings left the table too — no dead text to drift.
    for key in ('header_mode', 'notice', 'summary_line',
                'state_not_started', 'state_playing', 'state_started',
                'transition', 'output', 'setlist_empty', 'listen'):
        assert key not in i18n.STRINGS, key
    assert not hasattr(i18n, 'summary_state_text')
