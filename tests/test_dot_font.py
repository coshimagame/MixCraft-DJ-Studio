"""Bundled dot font (pass 12): the whole window — Japanese labels
included — renders in DotGothic16, a real dot-matrix-style font
bundled under assets/fonts/ (Fontworks, SIL OFL 1.1).

The file checks are pure stdlib (they run anywhere): the TTF must
exist, carry TrueType magic and a real font's size, and its OFL
license must be bundled beside it. The Qt checks follow the
offscreen pattern of tests/test_language_toggle.py and skip where
Qt or a QApplication is unavailable; they pin that the font
registers through app.load_dot_font(), that the family resolves,
and that Japanese text maps to real glyphs (no .notdef tofu) —
QRawFont.glyphIndexesForString is the authority (supportsCharacter
under-reports CJK coverage for this font).
"""
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / 'assets' / 'fonts' / 'DotGothic16-Regular.ttf'
OFL_PATH = ROOT / 'assets' / 'fonts' / 'OFL.txt'


def test_bundled_font_file_is_a_real_ttf():
    assert FONT_PATH.is_file()
    data = FONT_PATH.read_bytes()
    assert len(data) > 500_000          # a CJK font is megabytes, not a stub
    assert data[:4] in (b'\x00\x01\x00\x00', b'true', b'OTTO')


def test_ofl_license_is_bundled_beside_the_font():
    assert OFL_PATH.is_file()
    text = OFL_PATH.read_text(encoding='utf-8', errors='replace')
    assert 'SIL Open Font License' in text
    assert 'DotGothic16' in text


def test_font_attribution_readme_present():
    readme = FONT_PATH.parent / 'README.md'
    assert readme.is_file()
    text = readme.read_text(encoding='utf-8')
    assert 'DotGothic16' in text and 'Fontworks' in text


def test_font_stacks_lead_with_dotgothic16():
    import app as appmod
    assert appmod.FONT_STACK.startswith('"DotGothic16"')
    assert appmod.MONO_STACK.startswith('"DotGothic16"')
    assert appmod.CONSOLE_FAMILIES[0] == 'DotGothic16'


@pytest.fixture(scope='module')
def qapp():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_load_dot_font_registers_family(qapp):
    import app as appmod
    assert appmod.dot_font_path() == FONT_PATH
    assert appmod.load_dot_font() is True
    from PySide6.QtGui import QFontDatabase
    assert 'DotGothic16' in QFontDatabase.families()


def test_font_has_real_japanese_glyphs(qapp):
    import app as appmod
    assert appmod.load_dot_font() is True
    from PySide6.QtGui import QRawFont
    raw = QRawFont(str(FONT_PATH), 16)
    assert raw.isValid()
    assert raw.familyName() == 'DotGothic16'
    # Playback / remaining-time vocabulary the UI actually shows,
    # plus Latin readout characters: every one must map to a real
    # glyph (index 0 is .notdef — the tofu box).
    for ch in '再生曲設定残時間量次待機済過切替表示MIX0123456789.:':
        indexes = raw.glyphIndexesForString(ch)
        assert indexes and indexes[0] != 0, ch
