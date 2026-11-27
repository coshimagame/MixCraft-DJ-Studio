"""Offline two-deck engine. Commands take effect at audio block boundaries.

Phase 2 additions:
- de-zippered (smoothed) volume / cross / master / gain / EQ / filter:
  every parameter ramps linearly over SMOOTH_FRAMES so abrupt moves do not
  click.  Steady-state values are unchanged, so mixes render identically
  once a ramp has settled (~5 ms).
- 3-band EQ (crossover design; neutral EQ is bit-transparent), channel
  GAIN and a resonant-free one-pole FILTER (LP below centre, HP above).
- soft-knee limiter above 0.90 instead of a bare hard clip at 0.98.
- CUE / HOT CUE / LOOP / Quantize (beat-grid snapping), SYNC and tempo
  (rate playback — this pitches the audio; pitch-preserving time-stretch
  is NOT implemented and is not claimed).
- beat grid, key and analysis cache via analysis.py.
"""
from dataclasses import dataclass, field
from pathlib import Path
from queue import SimpleQueue
import json
import math
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, correlate, find_peaks, lfilter
from analysis import BeatGrid, estimate_beat_offset, estimate_key

RATE = 48000
SMOOTH_FRAMES = 240  # 5 ms at 48 kHz

@dataclass
class Track:
    path: str
    audio: np.ndarray
    peaks: np.ndarray
    bpm: float | None
    confidence: float
    beat_offset: int = 0
    beat_confidence: float = 0.0
    key: str | None = None
    camelot: str | None = None
    key_confidence: float = 0.0
    def grid(self):
        return BeatGrid(self.bpm, self.beat_offset) if self.bpm else None

def load_track(path, cache=None):
    cached = cache.get(path) if cache else None
    info = sf.info(path)
    if info.duration > 1800:
        raise ValueError('Phase 1: maximum track length is 30 minutes')
    audio, rate = sf.read(path, dtype='float32', always_2d=True)
    if not len(audio):
        raise ValueError('Empty audio file')
    audio = np.repeat(audio, 2, axis=1) if audio.shape[1] == 1 else audio[:, :2]
    if rate != RATE:
        gcd = math.gcd(rate, RATE)
        audio = resample_poly(audio, RATE // gcd, rate // gcd).astype('float32')
    audio = np.nan_to_num(audio)
    peaks = np.array([np.max(np.abs(x)) for x in np.array_split(audio, min(1400, len(audio)))])
    if cached:
        return Track(str(Path(path).resolve()), audio, peaks, cached['bpm'], cached['confidence'],
                     cached['beat_offset'] or 0, 0.0, cached['key'], cached['camelot'], cached['key_confidence'] or 0.0)
    bpm, confidence = estimate_bpm(audio)
    offset, beat_conf = estimate_beat_offset(audio, bpm)
    key, camelot, key_conf = estimate_key(audio)
    if cache:
        cache.put(path, dict(bpm=bpm, confidence=confidence, beat_offset=offset,
                             key=key, camelot=camelot, key_confidence=key_conf))
    return Track(str(Path(path).resolve()), audio, peaks, bpm, confidence,
                 offset, beat_conf, key, camelot, key_conf)

# Scrolling-waveform window math (pass 7, user direction 2026-11-26:
# 「波形データが流れるのもだいじ」). The deck waveform shows a zoomed
# window of WAVE_WINDOW_SECONDS centred on the play position, flowing
# past a fixed centre playhead, rekordbox-style. Pure functions, kept
# here (Qt-free) so the tests can pin them like the other engine math.
WAVE_WINDOW_SECONDS = 16.0    # total visible span (±8 s around the playhead)

def scroll_window(position, half_seconds=WAVE_WINDOW_SECONDS / 2, rate=RATE):
    """(start, end) frames of the visible waveform window, centred on
    `position`. Deliberately NOT clamped to the track: the playhead
    stays at the centre and the track's start/end scroll through the
    window, leaving empty space beyond the edges."""
    half = half_seconds * rate
    return position - half, position + half

def frame_to_x(frame, start, end, width):
    """Pixel x of `frame` inside the window [start, end] across `width` px."""
    return (frame - start) * width / max(1.0, end - start)

def peak_span(count, total, start, end):
    """[lo, hi) indices of the `count` peak buckets (evenly split over
    `total` frames) whose buckets intersect the window [start, end]."""
    if count <= 0 or total <= 0:
        return 0, 0
    lo = min(count, max(0, int(start * count / total)))
    hi = min(count, max(0, int(end * count / total) + 1))
    return lo, max(lo, hi)

def seek_fraction(x, y, width, height, overview_height, position, total,
                  rate=RATE):
    """Track fraction (0..1) for a waveform click at pixel (x, y).
    A click in the overview strip seeks by track fraction directly;
    a click in the scrolling area seeks to the time shown at that x
    inside the current window. Result clamped to [0, 1]."""
    if width <= 0 or total <= 0:
        return 0.0
    if y > height - overview_height:
        frac = x / width
    else:
        start, end = scroll_window(position, rate=rate)
        frac = (start + x / width * (end - start)) / total
    return min(1.0, max(0.0, frac))

# Mix timer math (pass 8, user direction 2026-11-27:
# 「今何分ミックスしたとかもわかるといいね」). The window's 40 ms tick
# drives these with time.monotonic(); the functions are pure (the
# caller passes `now` in), so the tests can pin the accumulation,
# start/stop/reset semantics and the remaining/overage formatting
# without a clock. Elapsed only ever accumulates from monotonic
# deltas while running — no wall-clock reads inside.
DEFAULT_TARGET_MINUTES = 60   # set target when no set list is loaded

def new_mix_timer():
    """Fresh mix-timer state: stopped, untouched, zeroed."""
    return dict(running=False, started=False, elapsed=0.0, last=None)

def mix_timer_update(timer, now):
    """Accumulate the monotonic delta since the last update while
    running; returns the elapsed seconds. A stopped timer is frozen:
    its elapsed never moves and `last` stays cleared."""
    if timer['running'] and timer['last'] is not None:
        timer['elapsed'] += max(0.0, now - timer['last'])
        timer['last'] = now
    return timer['elapsed']

def mix_timer_start(timer, now):
    timer['running'] = True
    timer['started'] = True
    timer['last'] = now

def mix_timer_stop(timer, now):
    """Freeze the timer at its current elapsed (accumulating up to
    `now` first). `started` stays set, so a later deck play does not
    silently auto-restart a timer the user stopped on purpose."""
    mix_timer_update(timer, now)
    timer['running'] = False
    timer['last'] = None

def mix_timer_reset(timer):
    timer['running'] = False
    timer['started'] = False
    timer['elapsed'] = 0.0
    timer['last'] = None

def mix_timer_auto_start(timer, any_playing, now):
    """Start on the first deck play, from the untouched state only
    (never started since the last reset). Returns True if it fired."""
    if any_playing and not timer['started']:
        mix_timer_start(timer, now)
        return True
    return False

def format_mix_clock(seconds):
    """mm:ss for the mix readouts; minutes keep counting past 59
    (a 75-minute mix reads 75:00, never 1:15:00)."""
    total = max(0, int(seconds))
    return f'{total // 60:02}:{total % 60:02}'

def format_mix_remaining(elapsed, target_seconds):
    """Time left to the target as mm:ss; once the performance runs
    past the target, the overage with a leading '+' (+02:30) instead
    of a negative remainder."""
    remaining = target_seconds - elapsed
    if remaining >= 0:
        return format_mix_clock(remaining)
    return '+' + format_mix_clock(-remaining)

def estimate_bpm(audio):
    mono = audio[:RATE * 180].mean(axis=1)
    hop = 480
    count = len(mono) // hop
    if count < 400:
        return None, 0.0
    energy = np.sqrt(np.mean(mono[:count*hop].reshape(count, hop)**2, axis=1))
    onset = np.maximum(0, np.diff(energy))
    if np.max(onset) < 1e-5:
        return None, 0.0
    ac = correlate(onset, onset, mode='full', method='fft')[len(onset)-1:]
    lo, hi = 30, 100  # 60–200 BPM, 100 Hz onset envelope
    candidates, _ = find_peaks(ac[lo:hi])
    if not len(candidates):
        return None, 0.0
    lag = int(candidates[np.argmax(ac[lo:hi][candidates])] + lo)
    bpm = 6000 / lag
    if bpm < 90: bpm *= 2  # dance-music prior; not a reliable meter detector
    return round(bpm, 1), float(np.clip(ac[lag] / (ac[0]+1e-12), 0, 1))

def _smooth(state, key, target, frames):
    """A 240-frame linear ramp, independent of callback block size."""
    ramps = state.setdefault('_ramps', {})
    previous, remaining, step = ramps.get(key, (state[key], 0, 0.0))
    if target != previous:
        remaining = SMOOTH_FRAMES
        step = (target - state[key]) / remaining
    n = min(frames, remaining)
    values = np.full(frames, target, dtype=np.float64)
    values[:n] = state[key] + step * np.arange(n)
    state[key] = state[key] + step*n if n < remaining else target
    ramps[key] = (target, remaining-n, step)
    return values

def _soft_limit(out):
    a = np.abs(out)
    over = a > 0.90
    if np.any(over):
        limited = 0.90 + 0.08 * np.tanh((a[over] - 0.90) / 0.08)
        out[over] = np.sign(out[over]) * limited
    return out

class _OnePole:
    """Stateful one-pole low-pass used for EQ crossovers / filter."""
    def __init__(self, cutoff):
        self.alpha = 1 - math.exp(-2 * math.pi * cutoff / RATE)
        self.state = np.zeros(2, dtype=np.float64)
    def run(self, x, alpha=None):
        if not len(x): return x.astype(np.float64)
        if alpha is None:
            alpha = np.full(len(x), self.alpha)
        # Only parameter transitions require sample-wise coefficient changes.
        changed = np.flatnonzero(np.diff(alpha))
        prefix = int(changed[-1]+2) if len(changed) else 0
        y = np.empty_like(x, dtype=np.float64)
        state = self.state
        for i in range(prefix):
            state = state + alpha[i] * (x[i] - state)
            y[i] = state
        if prefix < len(x):
            a = float(alpha[-1])
            y[prefix:], _ = lfilter([a], [1, -(1-a)], x[prefix:], axis=0,
                                    zi=((1-a)*state)[None, :])
        self.state = y[-1].copy()
        return y


def _new_deck():
    return dict(track=None, position=0, playing=False, volume=0.8,
                gain=1.0, eq_low=1.0, eq_mid=1.0, eq_high=1.0, filter=0.5,
                cue=0, hotcues={}, loop_start=None, loop_end=None, loop_active=False,
                quantize=False, rate=1.0, sync=False,
                _lp_low=None, _lp_high=None, _lp_filter=None, _hp_filter=None)

class Engine:
    def __init__(self):
        self.decks = [_new_deck() for _ in range(2)]
        self.cross = 0.5
        self.master = 0.8
        self._cur = dict(cross=0.5, master=0.8)
        self._deck_cur = [dict(volume=0.8, gain=1.0, eq_low=1.0, eq_mid=1.0, eq_high=1.0, filter=0.5) for _ in range(2)]
        self.commands = SimpleQueue()
        self.events = SimpleQueue()
        self.frame = 0
        self.peak = 0.0
        self.overloads = 0
        self.preview = None
        self.master_deck = 0

    def command(self, action, deck=None, value=None):
        self.commands.put((action, deck, value))

    def apply_pending(self):
        """Apply queued commands without rendering audio. The
        journal events are minted in _apply, which the audio
        callback normally drives; pass 21 (performance capture
        robustness): when no audio stream is running (device
        failed / not started), Window.tick pumps this so a
        performance still lands in the journal instead of
        sitting unlogged in the queue."""
        self._apply()

    def _snap(self, d, frame):
        if d['quantize'] and d['track'] is not None and d['track'].grid():
            frame = d['track'].grid().nearest_beat(frame)
        return int(np.clip(frame, 0, len(d['track'].audio))) if d['track'] else 0

    def _apply(self):
        while not self.commands.empty():
            action, index, value = self.commands.get()
            d = self.decks[index] if index is not None else None
            if action == 'load':
                keep = {k: d[k] for k in ('volume','gain','eq_low','eq_mid','eq_high','filter','quantize')}
                d.update(_new_deck()); d.update(keep)
                self._deck_cur[index] = {k: d[k] for k in ('volume','gain','eq_low','eq_mid','eq_high','filter')}
                d['track'] = value
                value = value.path
            elif action == 'play':
                if d['track'] is not None:
                    if d['position'] >= len(d['track'].audio): d['position'] = 0
                    d['playing'] = bool(value)
            elif action == 'seek':
                if d['track'] is not None:
                    d['position'] = self._snap(d, int(np.clip(value, 0, len(d['track'].audio))))
            elif action == 'volume': d['volume'] = float(np.clip(value, 0, 1))
            elif action == 'gain': d['gain'] = float(np.clip(value, 0, 2))
            elif action in ('eq_low', 'eq_mid', 'eq_high'):
                d[action] = float(np.clip(value, 0, 1)) * 2.0  # UI 0..1, 0.5 = neutral
            elif action == 'filter': d['filter'] = float(np.clip(value, 0, 1))
            elif action == 'cross': self.cross = float(np.clip(value, 0, 1))
            elif action == 'master': self.master = float(np.clip(value, 0, 1))
            elif action == 'cue_set': d['cue'] = self._snap(d, d['position'])
            elif action == 'cue_jump':
                if d['track'] is not None: d['position'] = d['cue']
            elif action == 'hotcue_set':
                if d['track'] is not None: d['hotcues'][int(value)] = self._snap(d, d['position'])
            elif action == 'hotcue_jump':
                if int(value) in d['hotcues']: d['position'] = d['hotcues'][int(value)]
            elif action == 'loop_in':
                d['loop_active'] = False
                d['loop_start'] = self._snap(d, d['position'])
            elif action == 'loop_out':
                d['loop_end'] = self._snap(d, d['position'])
                d['loop_active'] = d['loop_start'] is not None and d['loop_end'] > d['loop_start']
            elif action == 'loop_exit': d['loop_active'] = False
            elif action == 'quantize': d['quantize'] = bool(value)
            elif action == 'tempo': d['rate'] = float(np.clip(value, 0.5, 2.0))
            elif action == 'sync':
                d['sync'] = bool(value)
                other = self.decks[1 - index]
                if value and d['track'] and other['track'] and d['track'].bpm and other['track'].bpm:
                    d['rate'] = float(np.clip(other['track'].bpm * other['rate'] / d['track'].bpm, 0.5, 2.0))
                elif not value: d['rate'] = 1.0
            elif action == 'beat_offset':
                if d['track'] is not None: d['track'].beat_offset = int(value)
            elif action == 'preview':
                self._restore_preview()
                if all(x['track'] is not None for x in self.decks):
                    # Pass 15: the command's deck is the audition's
                    # TARGET — the deck the transition runs INTO
                    # (the app's monitored deck). deck=None keeps
                    # the original A -> B direction (target = 1).
                    target = index if index in (0, 1) else 1
                    source = 1 - target
                    duration = min(int(value * RATE), *(len(x['track'].audio) for x in self.decks))
                    self.preview = [0, duration, [{k: x[k] for k in ('position', 'playing', 'rate', 'sync', 'loop_active')} for x in self.decks], self.cross, target]
                    self.decks[source]['position'] = len(self.decks[source]['track'].audio)-duration
                    self.decks[target]['position'] = 0
                    for x in self.decks: x.update(playing=True, rate=1.0, sync=False, loop_active=False)
            elif action == 'stop_preview': self._restore_preview()
            self.events.put(dict(frame=self.frame, sample_rate=RATE, action=action, deck=index, value=value))

    def _restore_preview(self):
        if self.preview:
            for d, state in zip(self.decks, self.preview[2]):
                d.update(state)
            self.cross = self.preview[3]
            self._cur['cross'] = self.preview[3]
            self._cur.get('_ramps', {}).pop('cross', None)
            self.preview = None

    def _process_deck(self, d, cur, block, frames):
        x = block.astype(np.float64)
        eq = [cur[k + '_arr'][:frames, None] for k in ('eq_low','eq_mid','eq_high')]
        if any(np.any(g != 1) for g in eq):
            if d['_lp_low'] is None:
                d['_lp_low'], d['_lp_high'] = _OnePole(200), _OnePole(4000)
            low = d['_lp_low'].run(x)
            upper = d['_lp_high'].run(x)
            x = low*eq[0] + (upper-low)*eq[1] + (x-upper)*eq[2]
        else:
            d['_lp_low'] = d['_lp_high'] = None
        f = cur['filter_arr'][:frames]
        if np.any(f != 0.5):
            if d['_lp_filter'] is None:
                d['_lp_filter'], d['_hp_filter'] = _OnePole(20000), _OnePole(20)
            low_cut = 100 * 200**(np.minimum(f, .5)*2)
            high_cut = 20 * 200**(np.maximum(f-.5, 0)*2)
            low = d['_lp_filter'].run(x, 1-np.exp(-2*np.pi*low_cut/RATE))
            high = x-d['_hp_filter'].run(x, 1-np.exp(-2*np.pi*high_cut/RATE))
            wet = np.minimum(np.abs(f-.5)/.05, 1)[:,None]
            filtered = np.where((f < .5)[:,None], low, high)
            x = x*(1-wet) + filtered*wet
        else:
            d['_lp_filter'] = d['_hp_filter'] = None
        return x.astype('float32')

    def _read_deck(self, d, frames):
        audio = d['track'].audio
        rate = d['rate']
        indices = d['position'] + rate*np.arange(frames)
        loop = d['loop_active'] and d['loop_end'] is not None
        if loop:
            lo, hi = d['loop_start'], d['loop_end']
            indices = np.where(indices >= hi, lo+(indices-lo) % (hi-lo), indices)
        else:
            indices = indices[indices < len(audio)]
        n = len(indices)
        if not n: return np.empty((0,2), dtype='float32')
        left = np.floor(indices).astype(np.int64)
        right = left+1
        if loop: right = np.where(right >= hi, lo, right)
        right = np.minimum(right, len(audio)-1)
        fraction = (indices-left)[:,None]
        block = (audio[left]*(1-fraction)+audio[right]*fraction).astype('float32')
        end = d['position']+n*rate
        if loop and end >= hi: end = lo+(end-lo) % (hi-lo)
        d['position'] = min(end, len(audio))
        if not loop and end >= len(audio): d['playing'] = False
        return block

    def render(self, frames):
        self._apply()
        out = np.zeros((frames, 2), dtype='float32')
        if self.preview:
            x = np.clip((self.preview[0]+np.arange(frames))/max(1,self.preview[1]-1), 0, 1)
            # Pass 15: the sweep runs toward the audition's target
            # deck (preview[4]): cross 0->1 into deck 1, 1->0 into
            # deck 0; the deck gains follow the same sweep.
            sweep = x if self.preview[4] == 1 else 1.0 - x
            self.cross = float(sweep[-1]); self._cur['cross'] = self.cross
            gains = [np.cos(sweep*np.pi/2)[:,None], np.sin(sweep*np.pi/2)[:,None]]
            active_frames = min(frames, self.preview[1]-self.preview[0])
        else:
            cross_arr = _smooth(self._cur, 'cross', self.cross, frames)
            gains = [np.cos(cross_arr*np.pi/2)[:,None], np.sin(cross_arr*np.pi/2)[:,None]]
            active_frames = frames
        master_arr = _smooth(self._cur, 'master', self.master, frames)
        for di, (d, gain) in enumerate(zip(self.decks, gains)):
            if d['track'] is None or not d['playing']: continue
            cur = self._deck_cur[di]
            for key in ('volume', 'gain', 'eq_low', 'eq_mid', 'eq_high', 'filter'):
                cur[key + '_arr'] = _smooth(cur, key, d[key], frames)
            block = self._read_deck(d, active_frames)
            n = len(block)
            if n:
                block = self._process_deck(d, cur, block, n)
                level = (cur['volume_arr'][:n]*cur['gain_arr'][:n])[:,None]
                out[:n] += block*level*gain[:n]
        out = (out * master_arr[:, None]).astype('float32')
        if np.max(np.abs(out), initial=0) > 0.98: self.overloads += 1
        out = _soft_limit(out)
        self.peak = float(np.max(np.abs(out), initial=0))
        self.frame += frames
        if self.preview:
            self.preview[0] += frames
            if self.preview[0] >= self.preview[1]: self._restore_preview()
        return out

class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a', encoding='utf-8')
        self.history = []

    def drain(self, engine):
        while not engine.events.empty():
            event = engine.events.get()
            self.history.append(event)
            self.file.write(json.dumps(event, ensure_ascii=False, default=str)+'\n')
        self.file.flush()

    def export(self, path):
        Path(path).write_text(json.dumps(dict(schema=1, sample_rate=RATE, events=self.history), ensure_ascii=False, indent=2, default=str), encoding='utf-8')
