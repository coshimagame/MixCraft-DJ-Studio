"""live_bridge conversion rules (stdlib only, no Live API involved).

Events are produced through the real midi_map.dispatch wherever a
profile mapping exists, so the tests pin the dispatch -> plan contract
of both modules, not just live_bridge in isolation.
"""
from library import cues_to_clip_plan
from live_bridge import (LiveAction, LiveBridge, NUDGE_BEATS_PER_TICK,
                         plan_dispatch, plan_event)
from midi_map import DDJ_RB, dispatch


def _ddj_cc(channel, number, raw):
    return dispatch(DDJ_RB, 'cc', channel, number, raw)


def _ddj_note(channel, number):
    return dispatch(DDJ_RB, 'note', channel, number, 127)


# --- transport: PLAY / CUE -------------------------------------------------

def test_play_deck_a_and_b():
    assert plan_dispatch(_ddj_note(0, 11)) == [
        LiveAction('transport', 1, 'clip.toggle_play', True)]
    assert plan_dispatch(_ddj_note(1, 11)) == [
        LiveAction('transport', 2, 'clip.toggle_play', True)]


def test_cue_jump():
    assert plan_dispatch(_ddj_note(0, 12)) == [
        LiveAction('transport', 1, 'clip.cue_jump', True)]


def test_note_off_and_unmapped_produce_nothing():
    assert dispatch(DDJ_RB, 'note', 0, 11, 0) is None  # note-off
    assert plan_dispatch(None) == []
    assert plan_dispatch(dispatch(DDJ_RB, 'cc', 3, 99, 64)) == []


# --- EQ / TRIM / FILTER -----------------------------------------------------

def test_eq_bands_hit_the_right_track_and_target():
    # DDJ-RB: HI = CC 7, MID = CC 11, LOW = CC 15 on the deck channel.
    for number, target in ((7, 'eq_three.high'), (11, 'eq_three.mid'),
                           (15, 'eq_three.low')):
        assert plan_dispatch(_ddj_cc(1, number, 127)) == [
            LiveAction('set_parameter', 2, target, 1.0)]
        assert plan_dispatch(_ddj_cc(0, number, 0)) == [
            LiveAction('set_parameter', 1, target, 0.0)]


def test_eq_centre_is_neutral():
    (action,) = plan_dispatch(_ddj_cc(0, 7, 64))
    assert action.target == 'eq_three.high'
    assert abs(action.value - 0.5) < 0.01  # 64/127 ~= 0.504, centre = neutral


def test_trim_maps_to_input_gain():
    assert plan_dispatch(_ddj_cc(0, 4, 127)) == [
        LiveAction('set_parameter', 1, 'input.gain', 1.0)]


def test_filter_is_bipolar():
    # DDJ-RB filter parameter: ch 6 (global), CC 23 = Deck A, CC 24 = Deck B.
    (left,) = plan_dispatch(_ddj_cc(6, 23, 0))
    (right,) = plan_dispatch(_ddj_cc(6, 24, 127))
    (centre,) = plan_dispatch(_ddj_cc(6, 23, 64))
    assert left == LiveAction('set_parameter', 1, 'filter.bipolar', -1.0)
    assert right == LiveAction('set_parameter', 2, 'filter.bipolar', 1.0)
    assert abs(centre.value) < 0.02  # centre = open


# --- faders -----------------------------------------------------------------

def test_channel_faders():
    assert plan_dispatch(_ddj_cc(0, 19, 127)) == [
        LiveAction('set_parameter', 1, 'track.volume', 1.0)]
    assert plan_dispatch(_ddj_cc(1, 19, 0)) == [
        LiveAction('set_parameter', 2, 'track.volume', 0.0)]


def test_crossfader_and_master_are_global():
    assert plan_dispatch(_ddj_cc(6, 31, 0)) == [
        LiveAction('set_parameter', None, 'mixer.crossfader', 0.0)]
    assert plan_dispatch(_ddj_cc(6, 8, 127)) == [
        LiveAction('set_parameter', None, 'mixer.master_volume', 1.0)]


# --- fixed set BPM: tempo and sync are ignored ------------------------------

def test_tempo_sliders_produce_no_action():
    # DDJ-RB TEMPO: Deck A = CC 0 ch 0, Deck B = CC 5 ch 1.
    assert plan_dispatch(_ddj_cc(0, 0, 127)) == []
    assert plan_dispatch(_ddj_cc(1, 5, 0)) == []
    assert plan_event('tempo', 0, 1.0) == []


def test_sync_produces_no_action():
    assert plan_dispatch(_ddj_note(0, 88)) == []


# --- jog -> nudge -------------------------------------------------------------

def test_jog_clockwise_nudges_forward():
    (action,) = plan_dispatch(_ddj_cc(0, 34, 3))  # +3 ticks
    assert action.kind == 'nudge' and action.track == 1
    assert action.target == 'clip.nudge'
    assert action.value == 3 * NUDGE_BEATS_PER_TICK


def test_jog_counter_clockwise_nudges_backward():
    (action,) = plan_dispatch(_ddj_cc(1, 34, 127))  # raw 127 = -1 tick
    assert action.track == 2
    assert action.value == -NUDGE_BEATS_PER_TICK
    (action,) = plan_dispatch(_ddj_cc(0, 33, 65))   # raw 65 = -63 ticks
    assert action.value == -63 * NUDGE_BEATS_PER_TICK


def test_jog_zero_delta_is_noop():
    assert plan_event('jog', 0, 0.0) == []


# --- hot cues -> clip firing --------------------------------------------------

def _clip_plan():
    track = dict(title='Song X', cues=[
        dict(name='Intro', type='0', start=0.0, end=None, num=0),
        dict(name='', type='0', start=32.5, end=None, num=1),
        dict(name='Break', type='0', start=96.0, end=None, num=-1),
    ])
    return cues_to_clip_plan(track)


def test_hotcue_fires_the_planned_clip():
    plan = _clip_plan()
    assert plan_event('hotcue_jump', 1, True, clip_plan=plan, cue_index=1) == [
        LiveAction('fire_clip', 2, 'clip.fire', True,
                   clip_index=1, clip_name='Song X – Hot Cue B')]


def test_hotcue_needs_index_and_plan():
    plan = _clip_plan()
    # dispatch drops the pad index, so without cue_index nothing may fire.
    cmd = dispatch(DDJ_RB, 'note', 7, 3, 127)
    assert cmd == ('hotcue_jump', 0, True)
    assert plan_dispatch(cmd, clip_plan=plan) == []
    assert plan_dispatch(cmd, cue_index=0) == []          # no plan
    assert plan_event('hotcue_jump', 0, True, clip_plan=plan, cue_index=9) == []


def test_bridge_wrapper_uses_per_deck_plans():
    bridge = LiveBridge(clip_plans={0: _clip_plan()}, set_bpm=122.0)
    (action,) = bridge.handle('hotcue_jump', 0, True, cue_index=0)
    assert action.kind == 'fire_clip' and action.clip_name == 'Song X – Intro'
    assert bridge.handle_dispatch(_ddj_note(0, 11)) == [
        LiveAction('transport', 1, 'clip.toggle_play', True)]
    assert bridge.handle_dispatch(None) == []


# --- misc ---------------------------------------------------------------------

def test_loop_and_load_are_transport_plans():
    assert plan_dispatch(_ddj_note(0, 16)) == [
        LiveAction('transport', 1, 'clip.loop_in', True)]
    assert plan_dispatch(_ddj_note(1, 17)) == [
        LiveAction('transport', 2, 'clip.loop_out', True)]
    assert plan_dispatch(dispatch(DDJ_RB, 'note', 6, 70, 127)) == [
        LiveAction('transport', 1, 'track.load', True)]


def test_unknown_action_and_deckless_deck_action():
    assert plan_event('bogus', 0, 1.0) == []
    assert plan_event('volume', None, 0.5) == []
    assert plan_event('volume', 7, 0.5) == []  # no such deck in the template
