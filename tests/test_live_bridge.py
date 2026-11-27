"""live_bridge conversion rules (stdlib only, no Live API involved).

Events are produced through the real midi_map.dispatch wherever a
profile mapping exists, so the tests pin the dispatch -> plan contract
of both modules, not just live_bridge in isolation.
"""
from library import cues_to_clip_plan
from live_bridge import (LiveAction, LiveBridge, NUDGE_BEATS_PER_TICK,
                         compute_clip_groups, plan_dispatch, plan_event)
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


def test_hp_cue_buttons_plan_a_cue_toggle():
    # HEADPHONES CUE 1/2 on the DDJ-RB = Note 84 on the deck
    # channels (hardware probe via Live's map mode, 2026-11-26).
    assert plan_dispatch(_ddj_note(0, 84)) == [
        LiveAction('transport', 1, 'track.cue_toggle', True)]
    assert plan_dispatch(_ddj_note(1, 84)) == [
        LiveAction('transport', 2, 'track.cue_toggle', True)]


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


# --- set BPM: SYNC ignored; tempo state lives in LiveBridge -----------------

def test_tempo_plan_event_stays_stateless():
    # The tempo mapping needs the captured reference + last-value
    # state, so plan_event plans nothing for tempo (LiveBridge owns
    # it, like browse and the pad loops — see the v1.7 tests below).
    # DDJ-RB TEMPO: Deck A = CC 0 ch 0, Deck B = CC 5 ch 1 (the MSB
    # of the documented 14-bit pair; the LSB is not dispatched).
    assert plan_dispatch(_ddj_cc(0, 0, 127)) == []
    assert plan_dispatch(_ddj_cc(1, 5, 0)) == []
    assert plan_event('tempo', 0, 1.0) == []


def test_sync_produces_no_action():
    assert plan_dispatch(_ddj_note(0, 88)) == []


# --- jog -> nudge -------------------------------------------------------------

def test_jog_low_raw_bytes_nudge_backward():
    # Sign inverted after the 2026-11-26 hardware check (the nudge
    # ran opposite to the wheel): raw 0x01-0x3F now yields negative.
    (action,) = plan_dispatch(_ddj_cc(0, 34, 3))  # raw 3 -> -3 ticks
    assert action.kind == 'nudge' and action.track == 1
    assert action.target == 'clip.nudge'
    assert action.value == -3 * NUDGE_BEATS_PER_TICK


def test_jog_high_raw_bytes_nudge_forward():
    (action,) = plan_dispatch(_ddj_cc(1, 34, 127))  # raw 127 -> +1 tick
    assert action.track == 2
    assert action.value == NUDGE_BEATS_PER_TICK
    (action,) = plan_dispatch(_ddj_cc(0, 33, 65))   # raw 65 -> +63 ticks
    assert action.value == 63 * NUDGE_BEATS_PER_TICK


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


# --- performance pads (v1.5) -------------------------------------------------
#
# Layout (user decision 2026-11-26): pads 1-4 = Hot Cue A-D, pad 5 =
# CUE SET, pad 6 = QUANTIZE, pad 7 = LOOP IN, pad 8 = LOOP OUT.
# Pad actions that edit a clip need the adapter's observation of the
# playing clip (pad_context, positions in beats).


def _pad_ctx(position, **overrides):
    ctx = {'playing_position': position, 'looping': False,
           'loop_start': 0.0, 'loop_end': 0.0, 'length': 0.0}
    ctx.update(overrides)
    return ctx


def _pad(channel, note):
    return dispatch(DDJ_RB, 'note', channel, note, 127)


def test_pad_dispatch_layout():
    expected = ['hotcue_jump'] * 4 + ['cue_set', 'quantize',
                                      'pad_loop_in', 'pad_loop_out']
    for channel, deck in ((7, 0), (8, 1)):
        for note in range(8):
            assert _pad(channel, note) == (expected[note], deck, True)


def test_cue_set_snaps_to_the_beat_while_quantize_is_on():
    bridge = LiveBridge()
    assert bridge.handle_dispatch(_pad(7, 4), cue_index=4,
                                  pad_context=_pad_ctx(12.3)) == [
        LiveAction('clip_edit', 1, 'clip.start_marker', 12.0)]
    # Deck B routes to Track 2.
    assert bridge.handle_dispatch(_pad(8, 4), cue_index=4,
                                  pad_context=_pad_ctx(16.6)) == [
        LiveAction('clip_edit', 2, 'clip.start_marker', 17.0)]


def test_cue_set_keeps_the_position_when_quantize_is_off():
    bridge = LiveBridge()
    bridge.handle_dispatch(_pad(7, 5), cue_index=5)  # QUANTIZE off
    assert bridge.handle_dispatch(_pad(7, 4), cue_index=4,
                                  pad_context=_pad_ctx(12.3)) == [
        LiveAction('clip_edit', 1, 'clip.start_marker', 12.3)]


def test_cue_set_needs_a_playing_clip_context():
    bridge = LiveBridge()
    assert bridge.handle_dispatch(_pad(7, 4), cue_index=4) == []
    assert plan_event('cue_set', 0, True, pad_context=None) == []


def test_quantize_pad_toggles_bar_and_none():
    bridge = LiveBridge()
    assert bridge.quantize_on is True  # Live's 1-bar factory default
    assert bridge.handle_dispatch(_pad(7, 5), cue_index=5) == [
        LiveAction('song_edit', None, 'clip_trigger_quantization', 'none')]
    assert bridge.quantize_on is False
    # The other deck's pad 6 flips the same song-level switch back.
    assert bridge.handle_dispatch(_pad(8, 5), cue_index=5) == [
        LiveAction('song_edit', None, 'clip_trigger_quantization', 'bar')]
    assert bridge.quantize_on is True


def test_pad_loop_in_arms_a_provisional_loop():
    bridge = LiveBridge()
    actions = bridge.handle_dispatch(
        _pad(7, 6), cue_index=6, pad_context=_pad_ctx(8.0, length=64.0))
    assert actions == [
        LiveAction('clip_edit', 1, 'clip.loop_start', 8.0),
        LiveAction('clip_edit', 1, 'clip.loop_end', 12.0),  # +4 beats
        LiveAction('clip_edit', 1, 'clip.looping', True)]
    assert bridge.loop_phase(0) == 'open'


def test_pad_loop_out_finalises_then_toggles_off():
    bridge = LiveBridge()
    bridge.handle_dispatch(_pad(7, 6), cue_index=6,
                           pad_context=_pad_ctx(8.0, length=64.0))
    assert bridge.handle_dispatch(_pad(7, 7), cue_index=7,
                                  pad_context=_pad_ctx(16.0)) == [
        LiveAction('clip_edit', 1, 'clip.loop_end', 16.0),
        LiveAction('clip_edit', 1, 'clip.looping', True)]
    assert bridge.loop_phase(0) == 'set'
    # A loop is active and set: the next LOOP OUT turns it off.
    assert bridge.handle_dispatch(_pad(7, 7), cue_index=7,
                                  pad_context=_pad_ctx(20.0)) == [
        LiveAction('clip_edit', 1, 'clip.looping', False)]
    assert bridge.loop_phase(0) == 'idle'


def test_pad_loop_out_from_idle_sets_the_end():
    bridge = LiveBridge()
    assert bridge.handle_dispatch(
        _pad(8, 7), cue_index=7,
        pad_context=_pad_ctx(20.0, loop_start=4.0)) == [
        LiveAction('clip_edit', 2, 'clip.loop_end', 20.0),
        LiveAction('clip_edit', 2, 'clip.looping', True)]
    assert bridge.loop_phase(1) == 'set'


def test_pad_loop_in_clamps_the_provisional_end_to_the_clip():
    bridge = LiveBridge()
    actions = bridge.handle_dispatch(
        _pad(7, 6), cue_index=6, pad_context=_pad_ctx(63.0, length=64.0))
    assert actions[1] == LiveAction('clip_edit', 1, 'clip.loop_end', 64.0)
    # At the very end of the clip there is nothing to loop.
    bridge = LiveBridge()
    assert bridge.handle_dispatch(
        _pad(7, 6), cue_index=6,
        pad_context=_pad_ctx(64.0, length=64.0)) == []


def test_pad_loop_actions_need_a_playing_clip_context():
    bridge = LiveBridge()
    assert bridge.handle_dispatch(_pad(7, 6), cue_index=6) == []
    assert bridge.handle_dispatch(_pad(7, 7), cue_index=7) == []
    # The state machine lives in LiveBridge, not in plan_event.
    assert plan_event('pad_loop_in', 0, True,
                      pad_context=_pad_ctx(1.0)) == []


def _grouped_plan():
    return {'clips': [{'name': 'Song 1 - Artist'},
                      {'name': 'Song 1 – Hot Cue A'},
                      {'name': 'Song 1 – Hot Cue B'},
                      {'name': 'Song 2 - Artist'},
                      {'name': 'Song 2 – Hot Cue A'}],
            'locators': []}


def test_compute_clip_groups_from_names():
    assert compute_clip_groups(_grouped_plan()['clips']) == [[0, 1, 2],
                                                             [3, 4]]
    # Identical names never merge; separator-less names stand alone.
    clips = [{'name': 'Same - A'}, {'name': 'Same - A'}, {'name': 'Plain'}]
    assert compute_clip_groups(clips) == [[0], [1], [2]]


def test_hotcue_pads_fire_the_current_songs_hot_cues():
    bridge = LiveBridge(clip_plans={0: _grouped_plan()})
    bridge.note_clip_fired(0, 0)  # PLAY fired Song 1's main clip
    assert bridge.handle('hotcue_jump', 0, True, cue_index=0) == [
        LiveAction('fire_clip', 1, 'clip.fire', True,
                   clip_index=1, clip_name='Song 1 – Hot Cue A')]
    assert bridge.handle('hotcue_jump', 0, True, cue_index=1) == [
        LiveAction('fire_clip', 1, 'clip.fire', True,
                   clip_index=2, clip_name='Song 1 – Hot Cue B')]
    # Song 1 has no Hot Cue C: firing Song 2's clip would be wrong.
    assert bridge.handle('hotcue_jump', 0, True, cue_index=2) == []
    bridge.note_clip_fired(0, 3)  # Song 2 becomes the current song
    assert bridge.handle('hotcue_jump', 0, True, cue_index=0) == [
        LiveAction('fire_clip', 1, 'clip.fire', True,
                   clip_index=4, clip_name='Song 2 – Hot Cue A')]


def test_hotcue_group_without_a_main_clip_addresses_from_zero():
    # A bare cues_to_clip_plan list: every clip is a hot-cue clip, so
    # pad 1 is the group's first clip (no main clip to skip over).
    plan = {'clips': [{'name': 'Song X – Intro'},
                      {'name': 'Song X – Hot Cue B'}], 'locators': []}
    bridge = LiveBridge(clip_plans={0: plan})
    bridge.note_clip_fired(0, 0)
    assert bridge.handle('hotcue_jump', 0, True, cue_index=0) == [
        LiveAction('fire_clip', 1, 'clip.fire', True,
                   clip_index=0, clip_name='Song X – Intro')]
    assert bridge.handle('hotcue_jump', 0, True, cue_index=1) == [
        LiveAction('fire_clip', 1, 'clip.fire', True,
                   clip_index=1, clip_name='Song X – Hot Cue B')]


def test_hotcue_fallback_fires_clip_by_pad_when_ungrouped():
    plan = {'clips': [{'name': 'Alpha'}, {'name': 'Beta'},
                      {'name': 'Gamma'}], 'locators': []}
    bridge = LiveBridge(clip_plans={0: plan})
    bridge.note_clip_fired(0, 0)
    # No grouping detectable anywhere: v1.4 behaviour (pad n -> clip #n).
    assert bridge.handle('hotcue_jump', 0, True, cue_index=1) == [
        LiveAction('fire_clip', 1, 'clip.fire', True,
                   clip_index=1, clip_name='Beta')]
    assert bridge.handle('hotcue_jump', 0, True, cue_index=3) == []


def test_pad_leds_reflect_play_quantize_and_loop_state():
    bridge = LiveBridge(clip_plans={0: _grouped_plan()})
    bridge.note_clip_fired(0, 0)
    # Hot Cue A (clip 1) is playing; quantize starts on.
    leds = bridge.pad_leds(0, playing_ordinal=1)
    assert leds == [True, False, False, False, True, True, False, False]
    bridge.handle_dispatch(_pad(7, 6), cue_index=6,
                           pad_context=_pad_ctx(8.0, length=64.0))
    leds = bridge.pad_leds(0, playing_ordinal=0)
    assert leds[6] is True and leds[7] is False   # loop open
    bridge.handle_dispatch(_pad(7, 7), cue_index=7,
                           pad_context=_pad_ctx(16.0))
    leds = bridge.pad_leds(0, playing_ordinal=0)
    assert leds[6] is False and leds[7] is True   # loop set
    # Nothing playing: CUE SET's LED goes dark too.
    assert bridge.pad_leds(0, playing_ordinal=None)[4] is False


# --- browse knob (v1.6) ------------------------------------------------------

def _browse_bridge():
    plan_a = {'clips': [{'name': 'A1'}, {'name': 'A2'}, {'name': 'A3'}],
              'locators': []}
    plan_b = {'clips': [{'name': 'B1'}, {'name': 'B2'}, {'name': 'B3'},
                        {'name': 'B4'}], 'locators': []}
    return LiveBridge(clip_plans={0: plan_a, 1: plan_b})


def _turn(bridge, raw):
    return bridge.handle_dispatch(_ddj_cc(6, 64, raw))


def _push(bridge):
    return bridge.handle_dispatch(_ddj_note(6, 65))


def test_browse_dispatch_bindings():
    # Turn = CC on the browser channel, push = note on the browser
    # channel — numbers from AlphaTheta's official DDJ-RB MIDI
    # message list (turn CC 64, difference-count; push Note 65),
    # confirmed 2026-11-26 (see midi_map.py).  LOAD 1/2 (notes
    # 70/71) are from the same list.
    assert _ddj_cc(6, 64, 1) == ('browse_turn', None, 1 / 127.0)
    assert _ddj_note(6, 65) == ('browse_push', None, True)
    assert dispatch(DDJ_RB, 'note', 6, 65, 0) is None  # push note-off
    assert _ddj_note(6, 70) == ('load', 0, True)
    assert _ddj_note(6, 71) == ('load', 1, True)


def test_plan_event_stays_stateless_for_browse():
    # The cursor/target state is LiveBridge's; the stateless layer
    # must not plan browse actions on its own.
    assert plan_event('browse_turn', None, 1 / 127.0) == []
    assert plan_event('browse_push', None, True) == []


def test_browse_turn_targets_the_standby_deck():
    bridge = _browse_bridge()
    bridge.note_deck_playing(0, 0)  # Deck A is playing Song A1
    # The knob now browses Deck B: cursor 0 -> 1, highlight follows.
    assert _turn(bridge, 1) == [
        LiveAction('highlight', 2, 'view.highlighted_clip_slot', True,
                   clip_index=1, clip_name='B2')]
    assert _turn(bridge, 1) == [
        LiveAction('highlight', 2, 'view.highlighted_clip_slot', True,
                   clip_index=2, clip_name='B3')]
    assert bridge.browse_cursor(1) == 2
    assert bridge.browse_target_deck() == 1


def test_browse_turn_decodes_the_relative_direction():
    bridge = _browse_bridge()
    bridge.note_deck_playing(0, 0)
    _turn(bridge, 2)  # raw 0x02 = +2 detents
    assert bridge.browse_cursor(1) == 2
    _turn(bridge, 0x7F)  # raw 0x7F = -1 detent
    assert bridge.browse_cursor(1) == 1
    _turn(bridge, 0x41)  # raw 0x41 = -63 detents (fast spin back)
    assert bridge.browse_cursor(1) == 0


def test_browse_turn_clamps_at_the_first_and_last_clip():
    bridge = _browse_bridge()
    bridge.note_deck_playing(0, 0)  # target = Deck B (4 clips)
    assert _turn(bridge, 0x7F) == []  # already at the first clip
    assert bridge.browse_cursor(1) == 0
    _turn(bridge, 10)  # spin past the end: clamps to the last clip
    assert bridge.browse_cursor(1) == 3
    assert _turn(bridge, 1) == []  # no wrap-around
    assert bridge.browse_cursor(1) == 3


def test_browse_initial_target_prefers_the_deck_with_clips():
    only_b = LiveBridge(clip_plans={
        1: {'clips': [{'name': 'B1'}, {'name': 'B2'}], 'locators': []}})
    assert only_b.browse_target_deck() == 1
    both = _browse_bridge()  # neither playing: Deck A (sets start on A)
    assert both.browse_target_deck() == 0


def test_browse_keeps_the_last_target_when_standby_is_undecidable():
    bridge = _browse_bridge()
    bridge.note_deck_playing(0, 0)
    assert bridge.browse_target_deck() == 1
    bridge.note_deck_playing(1, 0)  # both playing now
    assert bridge.browse_target_deck() == 1
    bridge.note_deck_playing(0, None)
    bridge.note_deck_playing(1, None)  # neither playing
    assert bridge.browse_target_deck() == 1


def test_browse_push_fires_the_cursor_clip():
    bridge = _browse_bridge()
    bridge.note_deck_playing(0, 0)  # standby = Deck B
    _turn(bridge, 2)
    assert _push(bridge) == [
        LiveAction('fire_clip', 2, 'clip.fire', True,
                   clip_index=2, clip_name='B3')]


def test_browse_load_overrides_the_target_until_the_deck_fires():
    bridge = _browse_bridge()
    bridge.note_deck_playing(0, 0)  # standby rule would pick Deck B
    # LOAD 1 pins Deck A instead; the plan highlights A's cursor.
    assert bridge.handle_dispatch(_ddj_note(6, 70)) == [
        LiveAction('highlight', 1, 'view.highlighted_clip_slot', True,
                   clip_index=0, clip_name='A1')]
    assert bridge.browse_target_deck() == 0
    assert _turn(bridge, 1) == [
        LiveAction('highlight', 1, 'view.highlighted_clip_slot', True,
                   clip_index=1, clip_name='A2')]
    # Firing a clip on the pinned deck clears the override: the
    # standby rule takes over again (A playing -> browse B).
    bridge.note_clip_fired(0, 1)
    assert bridge.browse_target_deck() == 1


def test_browse_cursor_defaults_to_the_last_fired_clip():
    bridge = _browse_bridge()
    bridge.note_clip_fired(1, 2)  # Deck B last fired its third clip
    bridge.note_deck_playing(0, 0)  # standby = Deck B
    assert bridge.browse_cursor(1) == 2
    assert _turn(bridge, 1) == [
        LiveAction('highlight', 2, 'view.highlighted_clip_slot', True,
                   clip_index=3, clip_name='B4')]


def test_browse_without_clips_plans_nothing():
    bridge = LiveBridge()
    assert _turn(bridge, 1) == []
    assert _push(bridge) == []
    # LOAD still pins the (empty) deck; there is just nothing to
    # highlight or fire there.
    assert bridge.handle_dispatch(_ddj_note(6, 71)) == []
    assert bridge.browse_target_deck() == 1


# --- tempo faders (v1.7): song tempo around the captured reference ----------

def _tempo_bridge(reference=120.0):
    bridge = LiveBridge()
    bridge.set_tempo_reference(reference)
    return bridge


def _tempo_action(bpm):
    return LiveAction('song_edit', None, 'song.tempo', bpm)


def test_tempo_reference_capture():
    bridge = LiveBridge()
    assert bridge.tempo_reference is None
    assert bridge.current_tempo is None
    # No reference captured yet: fader movement plans nothing.
    assert bridge.handle('tempo', 0, 1.0) == []
    bridge.set_tempo_reference(122.0)
    assert bridge.tempo_reference == 122.0
    assert bridge.current_tempo == 122.0
    # A constructor set_bpm seeds the same reference.
    assert LiveBridge(set_bpm=122.0).tempo_reference == 122.0
    # Junk captures are ignored, never fatal.
    bridge.set_tempo_reference(None)
    bridge.set_tempo_reference(-5)
    assert bridge.tempo_reference == 122.0


def test_tempo_centre_is_the_reference_exactly():
    bridge = _tempo_bridge(120.0)
    # Move away, then back to the exact centre: the reference.
    assert bridge.handle('tempo', 0, 1.0) == [_tempo_action(126.0)]
    assert bridge.handle('tempo', 0, 0.5) == [_tempo_action(120.0)]
    # The hardware detent straddles raw 63/64; both quantise to the
    # reference, so a fader parked at its detent emits nothing.
    bridge = _tempo_bridge(120.0)
    assert bridge.handle_dispatch(_ddj_cc(0, 0, 64)) == []
    assert bridge.handle_dispatch(_ddj_cc(0, 0, 63)) == []


def test_tempo_fader_ends_hit_the_range_limits():
    bridge = _tempo_bridge(120.0)
    assert bridge.handle('tempo', 0, 1.0) == [_tempo_action(126.0)]
    assert bridge.handle('tempo', 0, 0.0) == [_tempo_action(114.0)]
    # Deck B's documented slider (CC 5, ch 1) drives the same tempo.
    assert bridge.handle_dispatch(_ddj_cc(1, 5, 127)) == [
        _tempo_action(126.0)]
    assert bridge.handle_dispatch(_ddj_cc(1, 5, 0)) == [
        _tempo_action(114.0)]


def test_tempo_values_clamp_beyond_the_fader_ends():
    bridge = _tempo_bridge(120.0)
    assert bridge.handle('tempo', 0, 1.5) == [_tempo_action(126.0)]
    assert bridge.handle('tempo', 0, -0.5) == [_tempo_action(114.0)]


def test_tempo_quantises_to_a_tenth_and_skips_unchanged_writes():
    bridge = _tempo_bridge(120.0)
    # One MSB count past the detent (raw 65) is ≈0.14 BPM -> 120.1.
    assert bridge.handle_dispatch(_ddj_cc(0, 0, 65)) == [
        _tempo_action(120.1)]
    # Positions inside the same 0.1 step emit nothing further.
    assert bridge.handle('tempo', 0, 0.505) == []
    assert bridge.handle('tempo', 0, 0.51) == []
    assert bridge.current_tempo == 120.1
    # The next step up does emit.
    assert bridge.handle('tempo', 0, 0.52) == [_tempo_action(120.2)]


def test_tempo_last_moved_fader_wins():
    bridge = _tempo_bridge(120.0)
    # Deck A pushes to the top of the range...
    assert bridge.handle('tempo', 0, 1.0) == [_tempo_action(126.0)]
    # ...then Deck B's fader, sitting at its centre, takes over the
    # moment it moves (the documented pickup behaviour).
    assert bridge.handle('tempo', 1, 0.5) == [_tempo_action(120.0)]
    # Deck B keeps control while it is the one moving.
    assert bridge.handle('tempo', 1, 0.75) == [_tempo_action(123.0)]
    # Deck A again: its parked position (still at the top) applies.
    assert bridge.handle('tempo', 0, 1.0) == [_tempo_action(126.0)]
    assert bridge.current_tempo == 126.0
