"""UI language strings (pass 10): the (en, jp) table and its formatters.

The user asked for an EN ⇄ 日本語 toggle because the bilingual
labels ('Load / 読込', 'NOW / 再生中') looked cluttered. These tests
pin the table's hygiene — every key in both languages, the English
side free of Japanese text, the two sides actually different — and
the dynamic formatters the window renders with (status badges,
summary state text). Pure stdlib; the widget side is covered by
tests/test_language_toggle.py.
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
    assert i18n.tr('load', 'en') == 'Load'
    assert i18n.tr('load', 'jp') == '読込'
    i18n.set_current('en')
    try:
        assert i18n.tr('load') == 'Load'
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


def test_summary_state_text_both_languages():
    assert i18n.summary_state_text(False, -1, 5, 'jp') == '未開始'
    assert i18n.summary_state_text(False, -1, 5, 'en') == 'NOT STARTED'
    # The Japanese form matches setlist.display_summary's state_text,
    # so the summary line reads the same as before the toggle existed.
    assert i18n.summary_state_text(True, 1, 5, 'jp') == '再生中 2 曲目'
    assert i18n.summary_state_text(True, 1, 5, 'en') == 'PLAYING TRACK 2'
    assert i18n.summary_state_text(True, 5, 5, 'jp') == '開始済み'
    assert i18n.summary_state_text(True, 5, 5, 'en') == 'STARTED'


def test_format_templates_render_in_both_languages():
    line = i18n.tr('summary_line', 'en').format(
        bpm='122', count=5, total='24:19', state='PLAYING TRACK 2')
    assert line == 'SET BPM 122  •  5 TRACKS  •  TOTAL 24:19  •  PLAYING TRACK 2'
    line = i18n.tr('summary_line', 'jp').format(
        bpm='122', count=5, total='24:19', state='再生中 2 曲目')
    assert line == 'セットBPM 122  •  5 曲  •  合計 24:19  •  再生中 2 曲目'
    assert i18n.tr('seconds_item', 'en').format(n=8) == '8 seconds'
    assert i18n.tr('seconds_item', 'jp').format(n=8) == '8 秒'
    assert i18n.tr('key_line', 'jp').format(key='A minor', camelot='8A') == 'キー A minor (8A)'
