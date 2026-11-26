# -*- coding: utf-8 -*-
"""Bundled Python 2.7 compatible copy of the repo-root ``midi_map.py``.

Live 10 ships Python 2.7, which cannot parse the Python 3 syntax used by
the root module (f-strings, ``**`` dict unpacking, ``pathlib``).  This
copy keeps the mapping tables, ``dispatch`` and ``led_feedback`` with
*identical logic*; only the following mechanical changes were made:

- the DDJ-RB pad-note table is built with an explicit loop instead of
  ``**{... for ...}`` dict unpacking;
- ``MidiLearn``, ``save_mapping`` and ``load_mapping`` are omitted —
  they are file-IO / learn-state helpers the Remote Script does not use.

When the root ``midi_map.py`` tables or dispatch logic change, mirror
the change here (and vice versa).  Hardware facts verified on the
user's DDJ-RB (2026-11-25, Live 10.1.43): crossfader ch7 CC31,
PLAY deck1 ch1 Note 11, channel fader deck1 ch1 CC19, FILTER deck1
ch7 CC23, TRIM deck1 ch1 CC4, EQ HI deck1 ch1 CC7 (1-based channels;
the tables below are 0-based, as in the root module).
"""

# (channel, number) -> action.  CC = continuous, NOTE = button.
APC40 = {  # original APC40, clip-launch grid repurposed as hot cues
    'cc': {(0, 48): ('volume', 0), (0, 49): ('volume', 1),
           (0, 55): ('master', None), (0, 56): ('cross', None),
           (0, 16): ('eq_high', 0), (0, 17): ('eq_mid', 0),
           (0, 18): ('eq_low', 0), (0, 19): ('filter', 0),
           (0, 20): ('gain', 0),
           (0, 24): ('eq_high', 1), (0, 25): ('eq_mid', 1),
           (0, 26): ('eq_low', 1), (0, 27): ('filter', 1),
           (0, 28): ('gain', 1)},
    'note': {(0, 50): ('play', 0), (0, 49): ('play', 1),
             (0, 52): ('cue_jump', 0), (0, 51): ('cue_jump', 1),
             (0, 32): ('hotcue_jump', 0), (0, 33): ('hotcue_jump', 0)},
}
APC40_MKII = {
    'cc': {(0, 48): ('volume', 0), (0, 49): ('volume', 1),
           (0, 55): ('master', None), (0, 56): ('cross', None),
           (0, 16): ('eq_high', 0), (0, 17): ('eq_mid', 0),
           (0, 18): ('eq_low', 0), (0, 19): ('filter', 0),
           (0, 20): ('gain', 0),
           (0, 24): ('eq_high', 1), (0, 25): ('eq_mid', 1),
           (0, 26): ('eq_low', 1), (0, 27): ('filter', 1),
           (0, 28): ('gain', 1)},
    'note': {(0, 50): ('play', 0), (0, 49): ('play', 1),
             (0, 52): ('cue_jump', 0), (0, 51): ('cue_jump', 1)},
}


def _build_ddj_rb():
    cc = {(0, 19): ('volume', 0), (1, 19): ('volume', 1),      # CH FADER
          (6, 31): ('cross', None),                            # CROSSFADER
          (6, 8): ('master', None),                            # MASTER LEVEL
          (0, 4): ('gain', 0), (1, 4): ('gain', 1),            # TRIM
          (0, 7): ('eq_high', 0), (1, 7): ('eq_high', 1),
          (0, 11): ('eq_mid', 0), (1, 11): ('eq_mid', 1),
          (0, 15): ('eq_low', 0), (1, 15): ('eq_low', 1),
          (6, 23): ('filter', 0), (6, 24): ('filter', 1),      # Filter Parameter
          (0, 0): ('tempo', 0), (1, 5): ('tempo', 1),          # TEMPO slider
          (0, 34): ('jog', 0), (1, 34): ('jog', 1),            # JOG platter
          (0, 33): ('jog', 0), (1, 33): ('jog', 1),            # JOG wheel side
          # BROWSE knob turn — from AlphaTheta's official DDJ-RB
          # MIDI message list: CC 64 on the Browser/Global channel,
          # difference-count encoding (CW 0x01-0x1E, CCW 0x7F-0x62).
          # Confirmed against the official list 2026-11-26
          # (mirror of the root module's note).
          (6, 64): ('browse_turn', None)}                      # BROWSE turn
    note = {(0, 11): ('play', 0), (1, 11): ('play', 1),        # PLAY/PAUSE
            (0, 12): ('cue_jump', 0), (1, 12): ('cue_jump', 1),
            # HEADPHONES CUE 1/2 = Note 84 on the deck channels
            # (hardware probe via Live's map mode, 2026-11-26).
            (0, 84): ('hp_cue', 0), (1, 84): ('hp_cue', 1),
            (0, 88): ('sync', 0), (1, 88): ('sync', 1),
            (0, 16): ('loop_in', 0), (1, 16): ('loop_in', 1),
            (0, 17): ('loop_out', 0), (1, 17): ('loop_out', 1),
            (6, 70): ('load', 0), (6, 71): ('load', 1),        # LOAD buttons
            # BROWSE knob push — Note 65 on the Browser/Global
            # channel (official list, same as the turn CC above);
            # not deck-specific (LOAD 70/71 are).
            (6, 65): ('browse_push', None)}                     # BROWSE push
    # PERFORMANCE PADs (pad channels 8/9 in the PDF = 7/8 here),
    # notes 0-7 = pads 1-8.  v1.5 layout (mirror of the root module):
    # pads 1-4 = Hot Cue A-D, pad 5 = CUE SET, pad 6 = QUANTIZE,
    # pad 7 = LOOP IN, pad 8 = LOOP OUT.  dispatch does not pass the
    # pad index; the adapter supplies it as cue_index (MixCraft.py).
    for n in range(4):
        note[(7, n)] = ('hotcue_jump', 0)
        note[(8, n)] = ('hotcue_jump', 1)
    note[(7, 4)] = ('cue_set', 0)
    note[(8, 4)] = ('cue_set', 1)
    note[(7, 5)] = ('quantize', 0)
    note[(8, 5)] = ('quantize', 1)
    note[(7, 6)] = ('pad_loop_in', 0)
    note[(8, 6)] = ('pad_loop_in', 1)
    note[(7, 7)] = ('pad_loop_out', 0)
    note[(8, 7)] = ('pad_loop_out', 1)
    return {'cc': cc, 'note': note}


DDJ_RB = _build_ddj_rb()
PROFILES = {'APC40': APC40, 'APC40 mkII': APC40_MKII, 'DDJ-RB': DDJ_RB}


def dispatch(mapping, kind, channel, number, value):
    """Map one incoming message to an engine command tuple, or None."""
    hit = mapping.get(kind, {}).get((channel, number))
    if hit is None:
        return None
    action, deck = hit
    if kind == 'cc':
        return (action, deck, value / 127.0)
    if value == 0:
        return None  # note-off
    return (action, deck, True)


def led_feedback(profile_name, playing):
    """Note-On messages that light the play buttons.

    Same construction as the root module (velocity 1 = on, 0 = off);
    the adapter remaps the velocity for the DDJ-RB, whose LEDs expect
    127 = lit (to be confirmed on hardware, see SETUP.md).
    """
    profile = PROFILES[profile_name]
    out = []
    for (ch, num), (action, deck) in profile['note'].items():
        if action == 'play':
            out.append((0x90 | ch, num, 1 if playing[deck] else 0))
    return out
