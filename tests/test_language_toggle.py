"""Language toggle (pass 10): the header EN | 日本語 switch re-labels
the whole window immediately, in both directions.

Offscreen Qt; skipped where a QApplication cannot be created (the
pure string table is covered by tests/test_i18n.py). One shared
window walks jp (default) -> en -> jp and pins the visible texts at
each stop, including the dynamic ones: set-list badges and summary
line, mix-timer captions, the status bar.
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
    assert window.mix_remaining.text().startswith('残り')
    assert window.col_title.text() == 'タイトル / アーティスト'
    assert window.col_heads['status'].text() == '状態'
    assert window.status.text() == '準備完了'
    assert '再生中 2 曲目' in window.setlist_summary.text()
    assert _badge_texts(window) == ['済', '再生中', '次']
    assert window.devices.itemText(0) == '自動（WASAPI優先）'
    assert window.duration.itemText(0) == '8 秒'


def test_switch_to_english_relables_everything(window):
    window.set_language('en')
    assert window.lang == 'en'
    assert window.lang_en.isChecked() and not window.lang_jp.isChecked()
    assert window.decks[0].load.text() == 'Load'
    assert window.decks[0].cue.text() == '■ Reset'
    assert window.decks[0].pad_cue_set.text() == 'Cue Set'
    assert window.decks[1].sync.text() == 'SYNC'
    assert window.set_bpm_label.text() == 'SET BPM'
    assert window.set_dial.caption == 'SET BPM'
    assert window.master.label == 'MASTER'
    assert window.mix_toggle.text() == 'START'
    assert window.mix_remaining.text().startswith('LEFT')
    assert window.col_title.text() == 'TITLE / ARTIST'
    assert window.col_heads['status'].text() == 'STATUS'
    assert window.status.text() == 'Ready'
    assert 'PLAYING TRACK 2' in window.setlist_summary.text()
    assert _badge_texts(window) == ['PLAYED', 'NOW', 'NEXT']
    assert window.devices.itemText(0) == 'Automatic'
    assert window.duration.itemText(0) == '8 seconds'
    # No Japanese left anywhere in the labelled surface.
    surface = [window.mode_label, window.setlist_label,
               window.transition_label, window.output_label,
               window.mixer_label, window.cross_label,
               window.notice_label, window.setlist_summary]
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
    # deck's file name is not language text and must survive, while
    # its Key sub-line re-renders in the new language.
    from engine import load_track
    from make_demo import create
    create(tmp_path)
    path = sorted(tmp_path.glob('*.wav'))[0]
    window.loaded(0, load_track(str(path)), '')
    assert window.decks[0].name.text() == path.name
    window.set_language('en')
    assert window.decks[0].name.text() == path.name
    assert window.decks[0].sub.text().startswith('Key ')
    window.set_language('jp')
    assert window.decks[0].name.text() == path.name
    assert window.decks[0].sub.text().startswith('キー ')
