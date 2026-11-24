"""Offline two-deck engine. Commands take effect at audio block boundaries."""
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
import json
import math
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, correlate, find_peaks

RATE = 48000

@dataclass
class Track:
    path: str
    audio: np.ndarray
    peaks: np.ndarray
    bpm: float | None
    confidence: float

def load_track(path):
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
    bpm, confidence = estimate_bpm(audio)
    return Track(str(Path(path).resolve()), audio, peaks, bpm, confidence)

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

class Engine:
    def __init__(self):
        self.decks = [dict(track=None, position=0, playing=False, volume=0.8) for _ in range(2)]
        self.cross = 0.5
        self.master = 0.8
        self.commands = SimpleQueue()
        self.events = SimpleQueue()
        self.frame = 0
        self.peak = 0.0
        self.overloads = 0
        self.preview = None

    def command(self, action, deck=None, value=None):
        self.commands.put((action, deck, value))

    def _apply(self):
        while not self.commands.empty():
            action, index, value = self.commands.get()
            d = self.decks[index] if index is not None else None
            if action == 'load':
                d.update(track=value, position=0, playing=False)
                value = value.path
            elif action == 'play':
                if d['track'] is not None:
                    if d['position'] >= len(d['track'].audio): d['position'] = 0
                    d['playing'] = bool(value)
            elif action == 'seek':
                if d['track'] is not None:
                    d['position'] = int(np.clip(value, 0, len(d['track'].audio)))
            elif action == 'volume': d['volume'] = float(np.clip(value, 0, 1))
            elif action == 'cross': self.cross = float(np.clip(value, 0, 1))
            elif action == 'master': self.master = float(np.clip(value, 0, 1))
            elif action == 'preview':
                self._restore_preview()
                if all(d['track'] is not None for d in self.decks):
                    duration = min(int(value * RATE), *(len(d['track'].audio) for d in self.decks))
                    self.preview = [0, duration, [(d['position'], d['playing']) for d in self.decks], self.cross]
                    self.decks[0]['position'] = len(self.decks[0]['track'].audio)-duration
                    self.decks[1]['position'] = 0
                    for d in self.decks: d['playing'] = True
            elif action == 'stop_preview': self._restore_preview()
            self.events.put(dict(frame=self.frame, sample_rate=RATE, action=action, deck=index, value=value))

    def _restore_preview(self):
        if self.preview:
            for d, state in zip(self.decks, self.preview[2]):
                d['position'], d['playing'] = state
            self.cross = self.preview[3]
            self.preview = None

    def render(self, frames):
        self._apply()
        out = np.zeros((frames, 2), dtype='float32')
        if self.preview:
            x = np.clip((self.preview[0]+np.arange(frames))/max(1,self.preview[1]-1), 0, 1)
            self.cross = float(x[-1])
            gains = [np.cos(x*np.pi/2)[:,None], np.sin(x*np.pi/2)[:,None]]
            active_frames = min(frames, self.preview[1]-self.preview[0])
        else:
            gains = [math.cos(self.cross*math.pi/2), math.sin(self.cross*math.pi/2)]
            active_frames = frames
        for d, gain in zip(self.decks, gains):
            if d['track'] is None or not d['playing']: continue
            start = d['position']
            n = min(active_frames, len(d['track'].audio)-start)
            if n > 0:
                g = gain[:n] if isinstance(gain, np.ndarray) else gain
                out[:n] += d['track'].audio[start:start+n]*d['volume']*g
            d['position'] += n
            if d['position'] >= len(d['track'].audio): d['playing'] = False
        out *= self.master
        if np.max(np.abs(out), initial=0) > 0.98: self.overloads += 1
        np.clip(out, -0.98, 0.98, out=out)  # safety clamp, not a mastering limiter
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
            self.file.write(json.dumps(event, ensure_ascii=False)+'\n')
        self.file.flush()

    def export(self, path):
        Path(path).write_text(json.dumps(dict(schema=1, sample_rate=RATE, events=self.history), ensure_ascii=False, indent=2), encoding='utf-8')
