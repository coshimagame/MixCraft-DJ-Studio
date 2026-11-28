"""DDJ-RB MIDI input for the app (pass 23, v1).

User direction, 2026-11-27: the app is the performance app now, so
the DDJ-RB must drive IT (「まったくRBはんのうしない」 — tonight's
exe had no MIDI layer at all). This module is that layer:

- Transport: a backend (mido + python-rtmidi in production, a fake
  in tests) delivers normalized (kind, channel, number, value)
  messages; `translate` maps them to intents using the DDJ-RB
  numbers from AlphaTheta's official MIDI message list — the same
  numbers encoded in midi_map.DDJ_RB and verified on hardware
  through the Live Remote Script (PLAY/CUE notes, pad channels,
  headphone CUE note 84, BROWSE CC 64 / note 65).
- Execution: the controller performs every gesture on the SAME
  widgets the mouse drives (button .click(), knob/slider
  .setValue()), so a hardware gesture takes the identical command
  path into the engine and lands in the performance journal
  exactly like an on-screen one. Nothing bypasses the journal —
  that capture is the product's core promise.
- Connection: auto-connect to the ports whose name contains
  'DDJ-RB', retried quietly every few seconds from Window.tick;
  the matching OUT port carries the PLAY / monitor LEDs. Any MIDI
  failure (no library, no device, busy port) leaves the app fully
  usable with the mouse — MIDI is an input, never a dependency.

Deliberately UNASSIGNED in v1 (no honest on-screen equivalent):
BROWSE turn/push (the app's set list is display-only), the LOAD
buttons, the RB's own SYNC buttons (SYNC lives on pad 6), the
loop in/out buttons (the surface has no loop controls since
pass 5), and pads 7-8.
"""
import time
from collections import deque

from engine import RATE

SCAN_INTERVAL = 3.0          # seconds between connection retries
JOG_FRAMES_PER_TICK = RATE // 200   # 5 ms of track per jog delta unit
# Pass 26 (user direction 2026-11-28: 「setbpmはスライダーで変えたら
# 追随してほしい」): the TEMPO faders no longer offset their own
# deck's rate — they move the master SET BPM, the way the Live
# Remote Script's v1.7 mapping did: ±6 BPM around the SET BPM
# captured when the fader leaves its detent, in 0.1 BPM steps,
# last-moved fader wins, and the detent restores the captured
# SET BPM exactly.
TEMPO_BPM_RANGE = 6.0
DETENT = range(60, 69)       # centre band of the 0..127 fader travel

# The six app pad functions, in the on-screen row's order.
PAD_ACTIONS = ('cue_set', 'cue', 'hot1', 'hot1_set', 'quantize', 'sync')
_KNOB_CC = {7: 'eq_high', 11: 'eq_mid', 15: 'eq_low'}


def translate(kind, channel, number, value):
    """One normalized MIDI message -> one intent tuple, or None.

    kind is 'note' (value = velocity; a note-off arrives as
    value 0) or 'cc' (value = 0..127). Channels are 0-based,
    matching the official list's conversion in midi_map.DDJ_RB.
    """
    if kind == 'note':
        if value == 0:
            return None
        if number == 11 and channel in (0, 1):
            return ('play', channel)
        if number == 12 and channel in (0, 1):
            return ('cue', channel)
        if number == 84 and channel in (0, 1):
            return ('monitor', channel)
        if channel in (7, 8) and 0 <= number < len(PAD_ACTIONS):
            return ('pad', channel - 7, number + 1)
        return None
    if kind == 'cc':
        if channel in (0, 1):
            if number == 19:
                return ('volume', channel, value)
            if number == 4:
                return ('knob', channel, 'gain', value)
            if number in _KNOB_CC:
                return ('knob', channel, _KNOB_CC[number], value)
            if number in (33, 34):
                delta = value if value < 64 else value - 128
                return ('jog', channel, delta) if delta else None
            if (channel, number) in ((0, 0), (1, 5)):
                return ('tempo', channel, value)
            return None
        if channel == 6:
            if number == 23:
                return ('knob', 0, 'filter', value)
            if number == 24:
                return ('knob', 1, 'filter', value)
            if number == 31:
                return ('cross', value)
            if number == 8:
                return ('master', value)
        return None
    return None


def led_states(playing, monitor_deck):
    """(channel, note, on) for every LED the app drives: PLAY
    (note 11, deck channel) follows the deck's playing state; the
    headphone CUE button (note 84, deck channel) follows the
    app's monitor select."""
    out = []
    for deck in (0, 1):
        out.append((deck, 11, bool(playing[deck])))
        out.append((deck, 84, monitor_deck == deck))
    return out


def _normalize(msg):
    """A mido message -> the (kind, channel, number, value) tuple
    `translate` eats, or None for message types we do not use."""
    if msg.type == 'note_on':
        return ('note', msg.channel, msg.note, msg.velocity)
    if msg.type == 'note_off':
        return ('note', msg.channel, msg.note, 0)
    if msg.type == 'control_change':
        return ('cc', msg.channel, msg.control, msg.value)
    return None


class MidoBackend:
    """Production backend: mido over python-rtmidi. Import or
    device failures surface as 'not available' / empty name lists,
    never as exceptions into the app."""

    def __init__(self):
        self._mido = None
        try:
            import mido
            try:
                mido.set_backend('mido.backends.rtmidi')
            except Exception:
                pass  # mido's default backend still applies
            self._mido = mido
        except Exception:
            self._mido = None

    @property
    def available(self):
        return self._mido is not None

    def input_names(self):
        if self._mido is None:
            return []
        try:
            return list(self._mido.get_input_names())
        except Exception:
            return []

    def output_names(self):
        if self._mido is None:
            return []
        try:
            return list(self._mido.get_output_names())
        except Exception:
            return []

    def open_input(self, name, callback):
        def _cb(msg):
            normalized = _normalize(msg)
            if normalized is not None:
                callback(normalized)
        return self._mido.open_input(name, callback=_cb)

    def open_output(self, name):
        return self._mido.open_output(name)

    def send_note(self, port, channel, note, velocity):
        port.send(self._mido.Message('note_on', channel=channel,
                                     note=note, velocity=velocity))


def _find_ddj(names):
    for name in names:
        if 'ddj-rb' in name.lower():
            return name
    return None


class MidiController:
    """Owns the DDJ-RB connection and turns its messages into the
    window's own widget actions. Driven by Window.tick via poll();
    every entry point is failure-swallowing — a MIDI problem must
    never take the performance down."""

    def __init__(self, window, backend=None):
        self.window = window
        self.backend = backend if backend is not None else MidoBackend()
        self.available = bool(getattr(self.backend, 'available', True))
        self.connected = False
        self._in_port = None
        self._out_port = None
        self._queue = deque()
        self._last_scan = None
        self._announced = False
        self._led_cache = {}
        # Pass 26: per-fader SET BPM state — the baseline captured
        # on leaving the detent, and the last value this fader
        # commanded (an outside move — dial, the other fader —
        # invalidates the baseline; see _tempo_fader).
        self._fader_base = [None, None]
        self._fader_last = [None, None]

    # -- connection -------------------------------------------------
    def poll(self, now=None):
        """Called from Window.tick: retry the connection on the
        scan interval, drain queued messages into the UI, and
        refresh the LEDs from the engine's current state."""
        if now is None:
            now = time.monotonic()
        try:
            if not self.connected and (
                    self._last_scan is None
                    or now - self._last_scan >= SCAN_INTERVAL):
                self._last_scan = now
                self._scan()
            while self._queue:
                self.dispatch(self._queue.popleft())
            if self.connected:
                self._sync_leds()
        except Exception:
            pass  # MIDI must never break the tick / the show

    def _scan(self):
        name = _find_ddj(self.backend.input_names())
        if name is None:
            if not self._announced:
                self._announced = True
                self._announce(False)
            return
        try:
            self._in_port = self.backend.open_input(
                name, self._queue.append)
        except Exception:
            return  # busy (rekordbox?) or gone — retry next scan
        out_name = _find_ddj(self.backend.output_names())
        self._out_port = None
        if out_name is not None:
            try:
                self._out_port = self.backend.open_output(out_name)
            except Exception:
                self._out_port = None
        self.connected = True
        self._led_cache = {}
        self._announced = True
        self._announce(True)

    def _announce(self, connected):
        import i18n
        key = 'midi_connected' if connected else 'midi_disconnected'
        self.window._status_fn = lambda lang, k=key: i18n.tr(k, lang)
        self.window._apply_status()

    def close(self):
        for port in (self._in_port, self._out_port):
            try:
                if port is not None:
                    port.close()
            except Exception:
                pass
        self._in_port = self._out_port = None
        self.connected = False

    # -- LEDs ---------------------------------------------------------
    def _sync_leds(self):
        if self._out_port is None:
            return
        decks = self.window.engine.decks
        states = led_states([d['playing'] for d in decks],
                            self.window.monitor_deck)
        for channel, note, on in states:
            key = (channel, note)
            if self._led_cache.get(key) != on:
                self._led_cache[key] = on
                try:
                    self.backend.send_note(self._out_port, channel,
                                           note, 127 if on else 0)
                except Exception:
                    pass

    # -- dispatch -----------------------------------------------------
    def dispatch(self, message):
        """Execute one normalized message against the window.
        Public so tests (and future input sources) can drive it
        directly."""
        intent = translate(*message)
        if intent is None:
            return
        kind = intent[0]
        window = self.window
        engine = window.engine
        if kind == 'play':
            window.decks[intent[1]].play.click()
        elif kind == 'cue':
            window.decks[intent[1]].pad_cue.click()
        elif kind == 'pad':
            deck = window.decks[intent[1]]
            buttons = (deck.pad_cue_set, deck.pad_cue, deck.pad_hot1,
                       deck.pad_hot1s, deck.quant, deck.sync)
            buttons[intent[2] - 1].click()
        elif kind == 'knob':
            _, deck, action, value = intent
            window.decks[deck].controls[action].setValue(
                round(value * 100 / 127))
        elif kind == 'volume':
            window.decks[intent[1]].volume.setValue(
                round(intent[2] * 100 / 127))
        elif kind == 'cross':
            window.cross.setValue(round(intent[1] * 1000 / 127))
        elif kind == 'master':
            window.master.setValue(round(intent[1] * 100 / 127))
        elif kind == 'jog':
            _, deck, delta = intent
            state = engine.decks[deck]
            if state['track'] is not None:
                # Pass 24 (user hardware report, 2026-11-27: 「スク
                # ラッチ（なっじの方向逆）」): the v1 sign felt
                # backwards at the platter, so the seek runs
                # AGAINST the raw delta. translate() still decodes
                # the wire value faithfully; the inversion lives
                # here, in what the app does with it.
                target = state['position'] - delta * JOG_FRAMES_PER_TICK
                target = min(max(0, target), len(state['track'].audio))
                engine.command('seek', deck, int(target))
        elif kind == 'tempo':
            self._tempo_fader(intent[1], intent[2])
        elif kind == 'monitor':
            window.toggle_monitor(intent[1])

    def _tempo_fader(self, deck, value):
        """TEMPO fader -> master SET BPM, via the window's own
        set_set_bpm (the same door the SET BPM dial's drag uses,
        so hardware and mouse moves journal identically).

        The fader is a pickup control around a captured baseline:
        the first move out of the detent band captures the current
        SET BPM, travel maps to ±TEMPO_BPM_RANGE around it in
        0.1 BPM steps, and returning to the detent restores the
        baseline exactly. Either fader may drive — the last one
        moved wins. If the SET BPM changed behind this fader's
        back (the dial, the other fader), its baseline is stale
        and is re-captured on the next move instead of stomping
        the newer value on detent return. With no SET BPM source
        yet (no set list, no Deck A track) the fader is inert."""
        window = self.window
        current = window.set_bpm_now
        if current is None:
            return
        last = self._fader_last[deck]
        if last is not None and abs(current - last) > 0.051:
            self._fader_base[deck] = None
            self._fader_last[deck] = None
            last = None
        if value in DETENT:
            base = self._fader_base[deck]
            if base is not None and abs(current - base) > 0.051:
                window.set_set_bpm(base)
                self._fader_last[deck] = base
            return
        if self._fader_base[deck] is None:
            self._fader_base[deck] = current
        base = self._fader_base[deck]
        offset = (value - 64) / 63.0 if value > 64 else (value - 64) / 64.0
        bpm = round((base + TEMPO_BPM_RANGE * offset) * 10) / 10
        if last is None or abs(bpm - last) > 0.051:
            window.set_set_bpm(bpm)
            self._fader_last[deck] = bpm
