"""Dot-matrix glyph table tests (pass 11): every character the
console readouts need has a well-formed 5x7 glyph, the width math
matches the renderer's advance, and mixed strings segment into dot
runs and font runs (kanji fall back to the system font)."""

import string

import dotfont


def test_every_readout_character_has_a_glyph():
    needed = string.digits + string.ascii_uppercase + '.:+-/ ≈'
    for ch in needed:
        glyph = dotfont.glyph_for(ch)
        assert glyph is not None, ch
        assert len(glyph) == dotfont.GLYPH_H == 7
        for row in glyph:
            assert len(row) == dotfont.GLYPH_W == 5
            assert set(row) <= {'#', '.'}


def test_digits_are_distinguishable():
    glyphs = {dotfont.glyph_for(d) for d in string.digits}
    assert len(glyphs) == 10


def test_dashes_alias_to_hyphen():
    assert dotfont.glyph_for('−') == dotfont.glyph_for('-')
    assert dotfont.glyph_for('—') == dotfont.glyph_for('-')


def test_kanji_is_not_a_dot_character():
    assert dotfont.glyph_for('残') is None
    assert not dotfont.is_dot_char('り')
    assert dotfont.is_dot_char('3')


def test_text_units_and_width_math():
    assert dotfont.text_units('') == 0
    assert dotfont.text_units('122.00') == 6 * dotfont.ADVANCE - 1
    assert dotfont.text_width_px('122.00', 2.0) == \
        dotfont.text_units('122.00') * 2.0
    # A readout string the MIX TIMER shows.
    assert dotfont.text_units('MIX 23:48') == 9 * dotfont.ADVANCE - 1


def test_segments_split_dot_and_font_runs():
    assert dotfont.segments('MIX 23:48') == [('MIX 23:48', True)]
    assert dotfont.segments('残り 36:12') == \
        [('残り', False), (' 36:12', True)]
    assert dotfont.segments('超過 +02:30') == \
        [('超過', False), (' +02:30', True)]
    assert dotfont.segments('') == []
