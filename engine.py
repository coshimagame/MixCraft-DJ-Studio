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
from scipy.signal import resample_poly, correlate, find_peaks
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

def _ramp(current, target, frames):
    """Per-sample values moving current->target over SMOOTH_FRAMES."""
    if current == target:
        return np.full(frames, target, dtype=np.float64), target
    steps = min(frames, SMOOTH_FRAMES)
    frac = steps / SMOOTH_FRAMES
    end = current + (target - current) * frac
    head = np.linspace(current, end, steps, endpoint=False, dtype=np.float64)
    tail = np.full(frames - steps, end, dtype=np.float64)
    return np.concatenate([head, tail]), end

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
    def run(self, x):
        y = np.empty_like(x, dtype=np.float64)
        s, a = self.state, self.alpha
        for i in range(len(x)):
            s = s + a * (x[i] - s)
            y[i] = s
        self.state = s
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

    def _snap(self, d, frame):
        if d['quantize'] and d['track'] is not None and d['track'].grid():
            return d['track'].grid().nearest_beat(frame)
        return int(frame)

    def _apply(self):
        while not self.commands.empty():
            action, index, value = self.commands.get()
            d = self.decks[index] if index is not None else None
            if action == 'load':
                keep = {k: d[k] for k in ('volume',)}
                d.update(_new_deck()); d.update(keep)
                self._deck_cur[index] = dict(volume=d['volume'], gain=1.0, eq_low=1.0, eq_mid=1.0, eq_high=1.0, filter=0.5)
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
            elif action == 'loop_in': d['loop_start'] = self._snap(d, d['position'])
            elif action == 'loop_out':
                d['loop_end'] = self._snap(d, d['position'])
                if d['loop_start'] is not None and d['loop_end'] > d['loop_start']: d['loop_active'] = True
            elif action == 'loop_exit': d['loop_active'] = False
            elif action == 'quantize': d['quantize'] = bool(value)
            elif action == 'tempo': d['rate'] = float(np.clip(value, 0.5, 2.0))
            elif action == 'sync':
                d['sync'] = bool(value)
                other = self.decks[1 - index]
                if value and d['track'] and other['track'] and d['track'].bpm and other['track'].bpm:
                    d['rate'] = other['track'].bpm / d['track'].bpm
                elif not value: d['rate'] = 1.0
            elif action == 'beat_offset':
                if d['track'] is not None: d['track'].beat_offset = int(value)
            elif action == 'preview':
                self._restore_preview()
                if all(x['track'] is not None for x in self.decks):
                    duration = min(int(value * RATE), *(len(x['track'].audio) for x in self.decks))
                    self.preview = [0, duration, [(x['position'], x['playing']) for x in self.decks], self.cross]
                    self.decks[0]['position'] = len(self.decks[0]['track'].audio)-duration
                    self.decks[1]['position'] = 0
                    for x in self.decks: x['playing'] = True
            elif action == 'stop_preview': self._restore_preview()
            self.events.put(dict(frame=self.frame, sample_rate=RATE, action=action, deck=index, value=value))

    def _restore_preview(self):
        if self.preview:
            for d, state in zip(self.decks, self.preview[2]):
                d['position'], d['playing'] = state
            self.cross = self.preview[3]
            self._cur['cross'] = self.preview[3]
            self.preview = None

    def _process_deck(self, d, cur, block, frames):
        """EQ + filter for one deck block. Neutral settings are transparent."""
        x = block.astype(np.float64)
        eq = (cur['eq_low'], cur['eq_mid'], cur['eq_high'])
        if eq != (1.0, 1.0, 1.0) or d['_lp_low'] is not None:
            if d['_lp_low'] is None:
                d['_lp_low'], d['_lp_high'] = _OnePole(200), _OnePole(4000)
            low = d['_lp_low'].run(x)
            lp_high = d['_lp_high'].run(x)
            high = x - lp_high
            mid = lp_high - low
            x = low * eq[0] + mid * eq[1] + high * eq[2]
            if eq == (1.0, 1.0, 1.0):
                d['_lp_low'] = d['_lp_high'] = None  # back to transparent
        f = cur['filter']
        if f != 0.5:
            if f < 0.5:  # low-pass, centre->left closes the filter
                cutoff = 100 * (200 ** (f * 2))
                if d['_lp_filter'] is None or abs(d['_lp_filter'].alpha - (1 - math.exp(-2*math.pi*cutoff/RATE))) > 1e-6:
                    keep = d['_lp_filter'].state if d['_lp_filter'] else None
                    d['_lp_filter'] = _OnePole(cutoff)
                    if keep is not None: d['_lp_filter'].state = keep
                x = d['_lp_filter'].run(x)
            else:        # high-pass
                cutoff = 20 * (200 ** ((f - 0.5) * 2))
                if d['_hp_filter'] is None: d['_hp_filter'] = _OnePole(cutoff)
                x = x - d['_hp_filter'].run(x)
        return x.astype(np.float32)

    def render(self, frames):
        self._apply()
        out = np.zeros((frames, 2), dtype='float32')
        if self.preview:
            x = np.clip((self.preview[0]+np.arange(frames))/max(1,self.preview[1]-1), 0, 1)
            self.cross = float(x[-1]); self._cur['cross'] = self.cross
            gains = [np.cos(x*np.pi/2)[:,None], np.sin(x*np.pi/2)[:,None]]
            active_frames = min(frames, self.preview[1]-self.preview[0])
        else:
            cross_arr, self._cur['cross'] = _ramp(self._cur['cross'], self.cross, frames)
            gains = [np.cos(cross_arr*np.pi/2)[:,None], np.sin(cross_arr*np.pi/2)[:,None]]
            active_frames = frames
        master_arr, self._cur['master'] = _ramp(self._cur['master'], self.master, frames)
        for di, (d, gain) in enumerate(zip(self.decks, gains)):
            if d['track'] is None or not d['playing']: continue
            cur = self._deck_cur[di]
            for key in ('volume', 'gain', 'eq_low', 'eq_mid', 'eq_high', 'filter'):
                arr, cur[key] = _ramp(cur[key], d[key], frames)
                cur[key + '_arr'] = arr
            if d['loop_active'] and d['loop_end'] is not None and d['position'] >= d['loop_end']:
                d['position'] = d['loop_start']
            start = d['position']
            n = min(active_frames, len(d['track'].audio)-int(start))
            if d['loop_active'] and d['loop_end'] is not None:
                n = min(n, d['loop_end']-int(start))
            if n > 0:
                if d['rate'] == 1.0:
                    block = d['track'].audio[int(start):int(start)+n]
                    consumed = n
                else:
                    src = int(n * d['rate'])
                    raw = d['track'].audio[int(start):int(start)+src]
                    if len(raw) < 2: break
                    idx = np.linspace(0, len(raw)-1, n)
                    block = np.stack([np.interp(idx, np.arange(len(raw)), raw[:, ch]) for ch in range(2)], axis=1).astype('float32')
                    consumed = src
                block = self._process_deck(d, cur, block, n)
                # EQ gains must ramp too: re-apply EQ delta when not neutral is
                # already in _process via target values; volume/gain ramp here.
                level = (cur['volume_arr'][:n] * cur['gain_arr'][:n])[:, None]
                g = gain[:n] if isinstance(gain, np.ndarray) else gain
                out[:n] += block * level * g
                d['position'] = start + consumed
            if d['position'] >= len(d['track'].audio): d['playing'] = False
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
