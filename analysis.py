"""Track analysis: beat grid, key estimation and analysis cache (Phase 2).

Beat-grid offset and key are *estimates*.  Both can be corrected manually;
the corrected values are what the engine quantizes / syncs to.
"""
from dataclasses import dataclass, asdict
import hashlib, json, math, sqlite3
from pathlib import Path
import numpy as np

RATE = 48000
CAMELOT_MAJOR = {'C':'8B','G':'9B','D':'10B','A':'11B','E':'12B','B':'1B',
                 'F#':'2B','Db':'3B','Ab':'4B','Eb':'5B','Bb':'6B','F':'7B'}
CAMELOT_MINOR = {'A':'8A','E':'9A','B':'10A','F#':'11A','Db':'12A','Ab':'1A',
                 'Eb':'2A','Bb':'3A','F':'4A','C':'5A','G':'6A','D':'7A'}
NOTE_NAMES = ['C','Db','D','Eb','E','F','F#','G','Ab','A','Bb','B']
_MAJOR_PROFILE = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
_MINOR_PROFILE = np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])

@dataclass
class BeatGrid:
    bpm: float
    offset: int = 0          # frame (at RATE) of the first downbeat
    beats_per_bar: int = 4
    @property
    def beat_frames(self): return RATE * 60.0 / self.bpm
    @property
    def bar_frames(self): return self.beat_frames * self.beats_per_bar
    def nearest_beat(self, frame):
        if self.bpm <= 0: return int(frame)
        n = round((frame - self.offset) / self.beat_frames)
        return int(self.offset + n * self.beat_frames)
    def nearest_bar(self, frame):
        if self.bpm <= 0: return int(frame)
        n = round((frame - self.offset) / self.bar_frames)
        return int(self.offset + n * self.bar_frames)
    def beat_index(self, frame): return int((frame - self.offset) // self.beat_frames)
    def nudge(self, frames): self.offset = int(self.offset + frames); return self.offset
    def to_dict(self): return asdict(self)

def estimate_beat_offset(audio, bpm):
    """First strong onset at/after 0, searched in the first two bars.

    This is a downbeat *candidate*, not a meter detector: house/techno
    tracks that start on the kick are usually right, anything else needs
    the manual nudge in the UI.  Returns (offset_frames, confidence).
    """
    if not bpm: return 0, 0.0
    mono = audio[:int(RATE*8)].mean(axis=1)
    hop = 480
    count = len(mono)//hop
    if count < 16: return 0, 0.0
    energy = np.sqrt(np.mean(mono[:count*hop].reshape(count, hop)**2, axis=1))
    onset = np.maximum(0, np.diff(energy))
    beat_hops = (RATE*60.0/bpm)/hop
    window = max(1, int(min(len(onset), beat_hops*8)))
    seg = onset[:window]
    if seg.max() < 1e-5: return 0, 0.0
    # prefer an onset that repeats one beat later (a real downbeat repeats)
    scores = seg.copy()
    step = int(round(beat_hops))
    if step and len(seg) > step: scores[:len(seg)-step] += 0.5*seg[step:]
    idx = int(np.argmax(scores))
    conf = float(np.clip(scores[idx]/(np.mean(seg)+1e-9)/8, 0, 1))
    return int(idx*hop), conf

def estimate_key(audio):
    """Chroma + Krumhansl profile correlation. Returns (key, camelot, conf)."""
    mono = audio[:RATE*120].mean(axis=1).astype(np.float64)
    if len(mono) < RATE or np.max(np.abs(mono)) < 1e-4: return None, None, 0.0
    chroma = np.zeros(12)
    frame, hop = 8192, 4096
    window = np.hanning(frame)
    freqs = np.fft.rfftfreq(frame, 1/RATE)
    valid = (freqs >= 55) & (freqs <= 4000)
    midi = np.zeros(len(freqs))
    midi[valid] = np.round(69 + 12*np.log2(freqs[valid]/440.0)).astype(int) % 12
    for start in range(0, min(len(mono)-frame, RATE*60), hop):
        mag = np.abs(np.fft.rfft(mono[start:start+frame]*window))
        np.add.at(chroma, midi[valid].astype(int), mag[valid])
    if chroma.sum() <= 0: return None, None, 0.0
    chroma /= chroma.sum()
    best = (-2, None, None)
    for root in range(12):
        for profile, mode in ((_MAJOR_PROFILE, 'major'), (_MINOR_PROFILE, 'minor')):
            rolled = np.roll(profile, root)
            corr = np.corrcoef(chroma, rolled)[0, 1]
            if corr > best[0]: best = (corr, root, mode)
    corr, root, mode = best
    name = NOTE_NAMES[root]
    key = f"{name} {mode}"
    camelot = (CAMELOT_MAJOR if mode == 'major' else CAMELOT_MINOR).get(name)
    return key, camelot, float(np.clip(corr, 0, 1))

def keys_compatible(key_a, camelot_a, key_b, camelot_b):
    """Harmonic-mixing rule of thumb used by the auto-mix planner."""
    if not camelot_a or not camelot_b: return None  # unknown, do not block
    if camelot_a == camelot_b: return True
    num_a, let_a = int(camelot_a[:-1]), camelot_a[-1]
    num_b, let_b = int(camelot_b[:-1]), camelot_b[-1]
    if let_a == let_b and abs(num_a-num_b) in (1, 11): return True
    if num_a == num_b and let_a != let_b: return True  # relative major/minor
    return False

def fingerprint(path):
    p = Path(path); st = p.stat()
    h = hashlib.sha256()
    with p.open('rb') as f:
        h.update(f.read(1 << 20))
    return f"{st.st_size}:{st.st_mtime_ns}:{h.hexdigest()[:16]}"

class AnalysisCache:
    """SQLite cache so re-loading a track does not re-run the analysis."""
    def __init__(self, db_path):
        self.db = sqlite3.connect(str(db_path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS analysis(
            path TEXT PRIMARY KEY, fingerprint TEXT, bpm REAL, confidence REAL,
            beat_offset INTEGER, key_name TEXT, camelot TEXT, key_confidence REAL)""")
        self.db.commit()
    def get(self, path):
        fp = fingerprint(path)
        row = self.db.execute("SELECT bpm,confidence,beat_offset,key_name,camelot,key_confidence FROM analysis WHERE path=? AND fingerprint=?",
                              (str(Path(path).resolve()), fp)).fetchone()
        if not row: return None
        return dict(bpm=row[0], confidence=row[1], beat_offset=row[2], key=row[3], camelot=row[4], key_confidence=row[5])
    def put(self, path, result):
        self.db.execute("INSERT OR REPLACE INTO analysis VALUES (?,?,?,?,?,?,?,?)",
                        (str(Path(path).resolve()), fingerprint(path), result.get('bpm'), result.get('confidence'),
                         result.get('beat_offset'), result.get('key'), result.get('camelot'), result.get('key_confidence')))
        self.db.commit()
    def close(self): self.db.close()
