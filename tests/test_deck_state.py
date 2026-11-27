"""Pass 15 (deck state markers): the A=blue / B=red deck colour
coding is retired — both decks wear the same neutral greys — and
two state markers carry the meaning instead: ON AIR (filled ink
chip + heavy LCD frames) following the engine's real per-deck
playing state, and MONITOR (hollow ink chip), an exclusive
per-deck select whose engine job is the transition audition's
target deck.

The engine half (preview target) is Qt-free; the window half is
offscreen Qt, skipped where PySide6 cannot be imported.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

from engine import Engine, Track


def _track(value=0.3, frames=48000):
    return Track('test.wav', np.full((frames, 2), value,
                                     dtype='float32'),
                 np.ones(10), 120, 1)


def _prepared():
    e = Engine()
    for i in range(2):
        e.command('load', i, _track(0.2 if i == 0 else 0.4))
        e.command('play', i, True)
    e.render(10)
    return e


def test_preview_default_target_is_deck_b():
    # deck=None keeps the original A -> B audition: deck 1 is the
    # target (starts at 0), deck 0 the source (starts at its tail).
    e = _prepared()
    e.command('preview', value=0.5)
    e.render(10)
    assert e.preview is not None
    assert e.preview[4] == 1
    assert e.decks[1]['position'] < e.decks[0]['position']


def test_preview_target_deck_a_reverses_the_audition():
    # The monitored deck is the audition's target: with deck 0 as
    # the target, deck 0 starts at 0 and deck 1 at its tail, and
    # the crossfader sweep runs toward deck 0 (cross near 1 at the
    # start, where the source deck 1 sits on the crossfader).
    e = _prepared()
    e.command('preview', deck=0, value=0.5)
    e.render(10)
    assert e.preview is not None
    assert e.preview[4] == 0
    assert e.decks[0]['position'] < e.decks[1]['position']
    assert e.cross > 0.9   # sweep starts at the source deck (1)


def test_preview_target_restores_state_either_way():
    e = _prepared()
    e.command('play', 1, False)
    e.render(50)
    before = [(d['position'], d['playing']) for d in e.decks]
    e.command('preview', deck=0, value=0.01)
    e.render(512)
    assert e.preview is None
    assert [(d['position'], d['playing']) for d in e.decks] == before


try:
    from PySide6.QtWidgets import QApplication, QLabel
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False

qt_only = pytest.mark.skipif(not HAVE_QT, reason='PySide6 unavailable')


@pytest.fixture(scope='module')
def window():
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:  # no audio stack / display in this env
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()
    qapp.processEvents()


@qt_only
def test_no_deck_colours_anywhere(window):
    # The two decks' surface is identical now: same fader styling,
    # same knob colour per position on both decks, and the retired
    # palette constants no longer hold the old blue/red. Pass 20's
    # amber accent is a MASTER-control accent, not deck coding:
    # GAIN / FILTER wear it on BOTH decks alike, the EQ knobs keep
    # the neutral grey, and the MASTER knob wears it too.
    assert window.decks[0].fader_qss == window.decks[1].fader_qss
    for deck in window.decks:
        for action, knob in deck.controls.items():
            expected = (appmod.COLOR_KNOB_ACCENT
                        if action in ('gain', 'filter')
                        else appmod.COLOR_TEXT_SUB)
            assert knob.color.name() == appmod.QColor(expected).name()
    assert window.master.color.name() == \
        appmod.QColor(appmod.COLOR_KNOB_ACCENT).name()
    assert appmod.COLOR_DECK_A == appmod.COLOR_DECK_B
    assert appmod.COLOR_DECK_A not in ('#0e9bff', '#ff4a2d')


@qt_only
def test_onair_follows_engine_playing_state(window):
    engine = window.engine
    engine.decks[0]['playing'] = True
    engine.decks[1]['playing'] = False
    window.tick()
    assert window.decks[0].onair_on is True
    assert window.decks[1].onair_on is False
    assert window.decks[0].onair.text() == '再生中'
    assert window.decks[0].wave.playing is True
    assert window.decks[1].wave.playing is False
    # The heavy ink frame is on the playing deck's info window only.
    assert window.decks[0].info_box.property('heavy') is True
    assert window.decks[1].info_box.property('heavy') in (None, False)
    engine.decks[0]['playing'] = False
    engine.decks[1]['playing'] = True
    window.tick()
    assert window.decks[0].onair_on is False
    assert window.decks[1].onair_on is True
    engine.decks[1]['playing'] = False
    window.tick()


@qt_only
def test_monitor_select_is_exclusive_and_clearable(window):
    window.toggle_monitor(1)
    assert window.monitor_deck == 1
    assert window.decks[1].monitor_on is True
    assert window.decks[0].monitor_on is False
    assert window.decks[1].monitor_btn.text() == 'モニター中'
    window.toggle_monitor(0)   # selecting the other deck moves it
    assert window.monitor_deck == 0
    assert window.decks[0].monitor_on is True
    assert window.decks[1].monitor_on is False
    window.toggle_monitor(0)   # selecting it again clears it
    assert window.monitor_deck is None
    assert window.decks[0].monitor_on is False
    window.set_language('en')
    window.toggle_monitor(1)
    assert window.decks[1].monitor_btn.text() == 'MONITOR'
    window.toggle_monitor(1)
    window.set_language('jp')


@qt_only
def test_monitor_marker_on_setlist_row(window, tmp_path):
    # The monitored deck's loaded track wears the hollow MONITOR
    # marker on its set-list row; other rows keep the empty slot.
    from engine import load_track
    from make_demo import create
    create(tmp_path)
    path = sorted(tmp_path.glob('*.wav'))[0]
    window.loaded(1, load_track(str(path)), '')
    original = window.setlist_data
    try:
        window.setlist_data = {'format': 'mixcraft-setlist',
                               'version': 1, 'set_bpm': 122,
                               'started': False, 'current_index': -1,
                               'tracks': [
                                   {'title': 'Other', 'artist': 'X',
                                    'bpm': 120, 'key': '8A',
                                    'duration_seconds': 200,
                                    'path': '/nowhere/other.wav'},
                                   {'title': 'Demo', 'artist': 'Y',
                                    'bpm': 120, 'key': '8A',
                                    'duration_seconds': 200,
                                    'path': str(path)}]}
        window.toggle_monitor(1)
        markers = []
        for i in range(window.setlist_rows.count()):
            container = window.setlist_rows.itemAt(i).widget()
            texts = [w.text() for w in container.findChildren(QLabel)]
            markers.append('モニター' in texts)
        assert markers == [False, True]
    finally:
        if window.monitor_deck is not None:
            window.toggle_monitor(window.monitor_deck)
        window.setlist_data = original
        window.render_setlist()
