"""MIDI controller mappings (Phase 2, logic only — real hardware NOT verified).

No RtMidi dependency yet: this module owns the mapping tables, MIDI Learn
state machine and LED-feedback message construction so they can be unit
tested without a device.  Wiring a real input (rtmidi / mido) is a thin
adapter that feeds (status, data1, data2) triples into `dispatch`.
実機未検証 / not verified on APC40, APC40 mkII or DDJ-RB hardware.
"""
import json
from pathlib import Path

# (channel, number) -> action.  CC = continuous, NOTE = button.
APC40 = {  # original APC40, clip-launch grid repurposed as hot cues
    # Knob layout (user decision 2026-11-25): the top 8 knobs (Track Control
    # section, CC 16-23) belong to Deck A, the bottom 8 knobs (Device Control
    # section, CC 24-31) belong to Deck B. Per deck: HIGH/MID/LOW/FILTER on
    # the section's top row, GAIN first on its bottom row; CC 21-23 and
    # CC 29-31 stay unassigned as spares for MIDI Learn.
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
DDJ_RB = {  # Pioneer DDJ-RB — numbers from AlphaTheta's official MIDI
    # message list (DDJ-RB_MIDI_Message_List_E.pdf), NOT hardware-verified.
    # Channels below are 0-based; the PDF's 1-based channels are:
    # DECK1=1, DECK2=2, FX DECK1=5, FX DECK2=6, Browser/Global=7,
    # PERFORMANCE PAD DECK1=8, DECK2=9.
    # Knobs/faders send 14-bit CC pairs; only the MSB is mapped here
    # (the LSB is MSB+32). Jog wheels send relative (difference) values,
    # so a 'jog' command's value is not an absolute position.
    'cc': {(0, 19): ('volume', 0), (1, 19): ('volume', 1),      # CH FADER
           (6, 31): ('cross', None),                            # CROSSFADER
           (6, 8): ('master', None),                            # MASTER LEVEL
           (0, 4): ('gain', 0), (1, 4): ('gain', 1),            # TRIM
           (0, 7): ('eq_high', 0), (1, 7): ('eq_high', 1),
           (0, 11): ('eq_mid', 0), (1, 11): ('eq_mid', 1),
           (0, 15): ('eq_low', 0), (1, 15): ('eq_low', 1),
           (6, 23): ('filter', 0), (6, 24): ('filter', 1),      # Filter Parameter (E11)
           (0, 0): ('tempo', 0), (1, 5): ('tempo', 1),          # TEMPO slider
           (0, 34): ('jog', 0), (1, 34): ('jog', 1),            # JOG platter (relative)
           (0, 33): ('jog', 0), (1, 33): ('jog', 1)},           # JOG wheel side (relative)
    'note': {(0, 11): ('play', 0), (1, 11): ('play', 1),        # PLAY/PAUSE = Note 11
             (0, 12): ('cue_jump', 0), (1, 12): ('cue_jump', 1),
             # HEADPHONES CUE 1/2 buttons = Note 84 (C5 in Live's
             # naming) on the deck channels — probed on hardware via
             # Live's MIDI map mode, 2026-11-26.
             (0, 84): ('hp_cue', 0), (1, 84): ('hp_cue', 1),
             (0, 88): ('sync', 0), (1, 88): ('sync', 1),
             (0, 16): ('loop_in', 0), (1, 16): ('loop_in', 1),
             (0, 17): ('loop_out', 0), (1, 17): ('loop_out', 1),
             (6, 70): ('load', 0), (6, 71): ('load', 1),        # LOAD buttons
             # PERFORMANCE PADs (pad channels 8/9 in the PDF = 7/8
             # here), notes 0-7 = pads 1-8.  v1.5 layout (user
             # decision 2026-11-26 — everything on the hardware, no
             # mouse): pads 1-4 = Hot Cue A-D, pad 5 = CUE SET,
             # pad 6 = QUANTIZE toggle, pad 7 = LOOP IN, pad 8 =
             # LOOP OUT.  Like the APC40 profile, dispatch does not
             # pass the pad index; the Remote Script adapter supplies
             # the note number as cue_index (see live_bridge).
             **{(7, n): ('hotcue_jump', 0) for n in range(4)},
             **{(8, n): ('hotcue_jump', 1) for n in range(4)},
             (7, 4): ('cue_set', 0), (8, 4): ('cue_set', 1),
             (7, 5): ('quantize', 0), (8, 5): ('quantize', 1),
             (7, 6): ('pad_loop_in', 0), (8, 6): ('pad_loop_in', 1),
             (7, 7): ('pad_loop_out', 0), (8, 7): ('pad_loop_out', 1)},
}
PROFILES = {'APC40': APC40, 'APC40 mkII': APC40_MKII, 'DDJ-RB': DDJ_RB}

class MidiLearn:
    """Press Learn, move a control, the next message is bound to the action."""
    def __init__(self, mapping=None):
        self.mapping = mapping or {'cc': {}, 'note': {}}
        self.pending = None
    def arm(self, action, deck=None): self.pending = (action, deck)
    def feed(self, kind, channel, number, value):
        if self.pending is None: return None
        action, deck = self.pending
        self.mapping[kind][(channel, number)] = (action, deck)
        self.pending = None
        return (action, deck)

def dispatch(mapping, kind, channel, number, value):
    """Map one incoming message to an engine command tuple, or None."""
    hit = mapping.get(kind, {}).get((channel, number))
    if hit is None: return None
    action, deck = hit
    if kind == 'cc': return (action, deck, value/127.0)
    if value == 0: return None  # note-off
    return (action, deck, True)

def led_feedback(profile_name, playing):
    """Note-On messages that light the play buttons (APC40: velocity=1 green)."""
    profile = PROFILES[profile_name]
    out = []
    for (ch, num), (action, deck) in profile['note'].items():
        if action == 'play':
            out.append((0x90 | ch, num, 1 if playing[deck] else 0))
    return out

def save_mapping(mapping, path):
    data = {kind: {f"{ch},{num}": list(v) for (ch, num), v in table.items()}
            for kind, table in mapping.items()}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def load_mapping(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return {kind: {tuple(int(x) for x in k.split(',')): tuple(v) for k, v in table.items()}
            for kind, table in data.items()}
