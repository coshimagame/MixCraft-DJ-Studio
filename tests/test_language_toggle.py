"""Language toggle (pass 10): the header EN | 日本語 switch re-labels
the whole window immediately, in both directions.

Offscreen Qt; skipped where a QApplication cannot be created (the
pure string table is covered by tests/test_i18n.py). One shared
window walks jp (default) -> en -> jp and pins the visible texts at
each stop, including the dynamic ones: set-list badges, mix-timer
captions, the status bar.

Pass 13 (declutter): the header subtitle/meta, the bottom notice,
the set-list summary line and the utility captions are gone, and
these tests pin their absence; the deck header title comes from
known metadata (set list / file tags) with the file name last.
"""
import os
import re

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication, QLabel

import app as appmod

CJK = re.compile(r'[ぁ-んァ-ヶ一-龥]')


def _setlist():
    def track(title, artist, seconds, **kw):
        t = {'title': title, 'artist': artist, 'bpm': 120, 'key': '8A',
             'duration_seconds': seconds, 'preview_url': None,
             'preview_source': None, 'source_url': None,
             'local_preview': None}
        t.update(kw)
        return t
    return {'format': 'mixcraft-setlist', 'version': 1, 'set_bpm': 122,
            'started': True, 'current_index': 1, 'target_minutes': 60,
            'tracks': [track('Decaying', 'TAO', 280),
                       track('Violet Horizon', 'Sable Waves', 312,
                             source_url='https://example.com/violet'),
                       track('Tidal Circuit', 'North Pier', 264)]}


@pytest.fixture(scope='module')
def window():
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:  # no audio stack / display in this env
        pytest.skip(f'Window cannot be built here: {exc}')
    win.setlist_data = _setlist()
    win.render_setlist()
    yield win
    win.close()
    qapp.processEvents()


def _badge_texts(win):
    texts = []
    for i in range(win.setlist_rows.count()):
        container = win.setlist_rows.itemAt(i).widget()
        for j in range(container.layout().count()):
            widget = container.layout().itemAt(j).widget()
            if isinstance(widget, QLabel) and widget.width() == 84:
                texts.append(widget.text())
    return texts


def test_window_starts_in_japanese(window):
    assert window.lang == 'jp'
    assert window.lang_jp.isChecked() and not window.lang_en.isChecked()
    assert window.decks[0].load.text() == '読込'
    assert window.decks[0].cue.text() == '■ 先頭'
    assert window.decks[0].pad_cue_set.text() == 'キュー設定'
    assert window.decks[1].sync.text() == 'シンク'
    assert window.set_bpm_label.text() == 'セットBPM'
    assert window.set_dial.caption == 'セットBPM'
    assert window.master.label == 'マスター'
    assert window.mix_toggle.text() == '開始'
    assert window.col_title.text() == 'タイトル / アーティスト'
    assert window.col_heads['status'].text() == '状態'
    assert window.status.text() == '準備完了'
    # Pass 13: the decluttered displays are gone, not just reworded.
    # Pass 16: the mix timer's remaining readout joins them.
    for gone in ('mode_label', 'notice_label', 'setlist_summary',
                 'transition_label', 'output_label', 'mix_remaining'):
        assert not hasattr(window, gone), gone
    assert _badge_texts(window) == ['済', '再生中', '次']
    assert window.devices.itemText(0) == '自動（WASAPI優先）'
    assert window.duration.itemText(0) == '8 秒'


def test_switch_to_english_relables_everything(window):
    window.set_language('en')
    assert window.lang == 'en'
    assert window.lang_en.isChecked() and not window.lang_jp.isChecked()
    assert window.decks[0].load.text() == 'LOAD'
    assert window.decks[0].cue.text() == '■ RESET'
    assert window.decks[0].pad_cue_set.text() == 'CUE SET'
    assert window.decks[1].sync.text() == 'SYNC'
    assert window.set_bpm_label.text() == 'SET BPM'
    assert window.set_dial.caption == 'SET BPM'
    assert window.master.label == 'MASTER'
    assert window.mix_toggle.text() == 'START'
    assert window.col_title.text() == 'TITLE / ARTIST'
    assert window.col_heads['status'].text() == 'STATUS'
    assert window.status.text() == 'Ready'
    assert _badge_texts(window) == ['PLAYED', 'NOW', 'NEXT']
    assert window.devices.itemText(0) == 'AUTOMATIC'
    assert window.duration.itemText(0) == '8 SECONDS'
    # No Japanese left anywhere in the labelled surface.
    surface = [window.setlist_label,
               window.mixer_label, window.cross_label]
    for deck in window.decks:
        surface += [deck.load, deck.cue, deck.pad_cue_set, deck.pad_cue,
                    deck.pad_hot1, deck.pad_hot1s, deck.quant, deck.sync,
                    deck.volume_label, deck.name]
    for label in surface:
        assert not CJK.search(label.text()), label.text()


def test_switch_back_to_japanese_and_idempotent(window):
    window.set_language('jp')
    assert window.decks[0].load.text() == '読込'
    assert _badge_texts(window) == ['済', '再生中', '次']
    window.set_language('jp')   # same language again: no churn, no error
    assert window.decks[0].load.text() == '読込'
    window.set_language('fr')   # unknown language: ignored
    assert window.lang == 'jp'


def test_loaded_deck_keeps_filename_across_switch(window, tmp_path):
    # Regression: the placeholder refresh must read the widget-side
    # track (the engine applies its command queue lazily) — a loaded
    # deck's title is not language text and must survive, while its
    # Key sub-line re-renders in the new language. The demo file has
    # no tags and no set-list path match, so the title falls back to
    # the file name (pass 13's last resort).
    from engine import load_track
    from make_demo import create
    create(tmp_path)
    path = sorted(tmp_path.glob('*.wav'))[0]
    window.loaded(0, load_track(str(path)), '')
    assert window.decks[0].name.text() == path.name
    # Pass 13: no '≈' estimate mark on the BPM readout, and the key
    # sub-line is Camelot only — no '(key name)' second notation.
    assert '≈' not in window.decks[0].bpm.text()
    assert window.decks[0].bpm.text().endswith(' BPM')
    assert '(' not in window.decks[0].sub.text()
    window.set_language('en')
    assert window.decks[0].name.text() == path.name
    assert window.decks[0].sub.text().startswith('KEY ')
    window.set_language('jp')
    assert window.decks[0].name.text() == path.name
    assert window.decks[0].sub.text().startswith('キー ')


def test_deck_title_prefers_known_metadata(window, tmp_path):
    # Pass 13: the deck header shows 'Title — Artist' from known
    # metadata — the file's embedded tags first here, then the
    # loaded set list's entry for the same file wins once known.
    import numpy as np
    import soundfile as sf
    from engine import RATE, load_track
    path = tmp_path / 'B - Raw Name.wav'
    with sf.SoundFile(str(path), 'w', samplerate=RATE, channels=2) as handle:
        handle.title = 'Tagged Title'
        handle.artist = 'Tagged Artist'
        handle.write(np.zeros((RATE, 2), dtype='float32'))
    window.loaded(1, load_track(str(path)), '')
    assert window.decks[1].name.text() == 'Tagged Title — Tagged Artist'
    original = window.setlist_data
    try:
        window.setlist_data = {'tracks': [
            {'title': 'Setlist Title', 'artist': 'Setlist Artist',
             'path': str(path)}]}
        window._refresh_deck_placeholders()
        assert window.decks[1].name.text() == 'Setlist Title — Setlist Artist'
    finally:
        window.setlist_data = original
        window._refresh_deck_placeholders()
