"""Track analysis: beat grid, key estimation and analysis cache (Phase 2).

Beat-grid offset and key are *estimates*.  Both can be corrected manually;
the corrected values are what the engine quantizes / syncs to.
"""
from dataclasses import dataclass, asdict
import hashlib, json, math, sqlite3, threading
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

# Pass 26 (REAL SYNC): the beat grid's trust threshold. At or above
# this confidence the grid may drive phase alignment (engine SYNC)
# and the refined BPM may replace the coarse autocorrelation BPM.
# Below it the honest behaviour is tempo-only sync — never fake an
# alignment the analysis cannot support.
GRID_CONFIDENT = 0.5

_GRID_SECONDS = 60        # analysis window for the grid fold
_GRID_BINS = 64           # phase resolution of the folded profile
_ONSET_HOP = 120          # 2.5 ms envelope step (400 Hz)
_ONSET_WIN = 240          # 5 ms RMS window behind each step

def _onset_envelope(audio, seconds=_GRID_SECONDS):
    """Positive energy-flux envelope at 400 Hz: a 5 ms RMS
    window stepped every 2.5 ms (cumulative-sum squares, so the
    overlap costs nothing). The fine step is what lets the fold
    locate a beat to a few ms — at the 100 Hz envelope
    estimate_bpm uses, the flux peak's block lag alone cost
    ~15 ms of grid error. None when too little audio to fold."""
    mono = audio[:int(RATE*seconds)].mean(axis=1).astype(np.float64)
    if len(mono) < _ONSET_WIN + 64*_ONSET_HOP: return None
    cs = np.concatenate([[0.0], np.cumsum(mono*mono)])
    starts = np.arange(0, len(mono) - _ONSET_WIN + 1, _ONSET_HOP)
    energy = np.sqrt((cs[starts + _ONSET_WIN] - cs[starts])/_ONSET_WIN)
    return np.maximum(0, np.diff(energy))

def _fold_profile(onset, period_hops, bins=_GRID_BINS):
    """Sum the onset envelope into one beat's worth of phase bins:
    bin b accumulates every onset whose time falls at phase
    b/bins of a beat. A steady beat piles its energy into a few
    bins; drifting or absent beats smear it across all of them."""
    idx = np.arange(len(onset))
    phase = (idx % period_hops) / period_hops
    b = np.minimum((phase*bins).astype(np.int64), bins-1)
    return np.bincount(b, weights=onset, minlength=bins)

def _profile_peak(profile):
    """(phase_in_beats, sharpness) of a folded profile. The peak
    bin is refined by parabolic interpolation (sub-bin phase);
    sharpness is 1 - mean/max: ~1 when all the energy sits on one
    phase, ~0 when the profile is flat."""
    peak = int(np.argmax(profile))
    top = float(profile[peak])
    if top <= 0: return 0.0, 0.0
    prev = float(profile[(peak-1) % len(profile)])
    nxt = float(profile[(peak+1) % len(profile)])
    denom = prev - 2*top + nxt
    delta = 0.5*(prev-nxt)/denom if denom else 0.0
    phase = ((peak + float(np.clip(delta, -0.5, 0.5))) / len(profile)) % 1.0
    sharp = 1.0 - float(profile.mean())/top
    return phase, float(np.clip(sharp, 0, 1))

def estimate_beat_grid(audio, bpm):
    """Beat grid v2 (pass 26): (offset_frames, confidence, refined_bpm).

    Method: fold the onset envelope modulo the beat period. The
    coarse BPM (estimate_bpm's autocorrelation lag is quantised to
    10 ms, up to ~0.3% off — enough to drift a quarter beat over a
    minute) is first refined by searching ±1.5% for the period
    whose fold piles the most onset energy onto one phase. The
    phase of that pile is the grid offset.

    Confidence = fold sharpness x segment consistency. The window
    is folded again in three segments; if the segment phases
    disagree by more than a fifth of a beat the tempo is drifting
    (live recordings, rips) and confidence collapses — the engine
    then falls back to tempo-only SYNC instead of aligning to a
    grid that does not stay put.
    """
    if not bpm: return 0, 0.0, bpm
    onset = _onset_envelope(audio)
    if onset is None or float(onset.max()) < 1e-5: return 0, 0.0, bpm
    coarse_period = 60.0*(RATE/_ONSET_HOP)/bpm   # beat period in hops
    if len(onset) < 4*coarse_period: return 0, 0.0, bpm
    best = None
    for scale in np.linspace(0.985, 1.015, 61):
        period = coarse_period/scale
        profile = _fold_profile(onset, period)
        top = float(profile.max())
        if best is None or top > best[0]:
            best = (top, bpm*scale, profile)
    _, refined_bpm, profile = best
    period = 60.0*(RATE/_ONSET_HOP)/refined_bpm
    phase, sharp = _profile_peak(profile)
    # Segment consistency: fold thirds separately at the refined
    # period and compare their phases against the global one.
    consistency = 1.0
    seg_len = len(onset)//3
    if seg_len >= 12*period:
        worst = 0.0
        for s in range(3):
            seg = onset[s*seg_len:(s+1)*seg_len] if s < 2 else onset[2*seg_len:]
            seg_phase, _ = _profile_peak(_fold_profile(seg, period))
            # The segment's fold is relative to its own start;
            # shift it back by the start's beat count so the phase
            # is comparable to the global one.
            seg_phase = (seg_phase + (s*seg_len)/period) % 1.0
            dev = abs((seg_phase - phase + 0.5) % 1.0 - 0.5)
            worst = max(worst, dev)
        consistency = float(np.clip(1.0 - worst/0.20, 0, 1))
    confidence = float(np.clip(sharp*consistency, 0, 1))
    beat_frames = RATE*60.0/refined_bpm
    offset = int(round(phase*beat_frames)) % max(1, int(round(beat_frames)))
    return offset, confidence, float(refined_bpm)

def estimate_beat_offset(audio, bpm):
    """(offset_frames, confidence) — the pass-26 grid estimator
    without its refined BPM. Kept for the original call sites."""
    offset, confidence, _ = estimate_beat_grid(audio, bpm)
    return offset, confidence

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
        # Pass 28: the app loads tracks on worker threads, so the
        # connection is shared (check_same_thread=False) and every
        # access is serialised through a lock.
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS analysis(
            path TEXT PRIMARY KEY, fingerprint TEXT, bpm REAL, confidence REAL,
            beat_offset INTEGER, key_name TEXT, camelot TEXT, key_confidence REAL)""")
        # Pass 26: the grid confidence is cached too — SYNC's
        # phase alignment trusts the grid only when it is stored.
        # Rows written before pass 26 have NULL here; load_track
        # treats that as "measure the grid once and enrich".
        columns = [r[1] for r in self.db.execute("PRAGMA table_info(analysis)")]
        if 'beat_confidence' not in columns:
            self.db.execute("ALTER TABLE analysis ADD COLUMN beat_confidence REAL")
        # Pass 28: the user's per-track bar offset (which beat of
        # the grid starts the bar, 0..3) persists here too. Rows
        # written before pass 28 have NULL; readers treat it as 0.
        if 'bar_offset' not in columns:
            self.db.execute("ALTER TABLE analysis ADD COLUMN bar_offset INTEGER")
        # Pass 35: whether the stored bar offset is INFORMATION
        # (the user stepped it) or merely the analysis default of
        # 0. Analysis cannot hear bars, so a default 0 must never
        # drive AUTO SYNC's bar alignment — only a value the user
        # actually set may. set_bar_offset marks the row known;
        # fresh analysis rows stay unknown.
        if 'bar_offset_known' not in columns:
            self.db.execute("ALTER TABLE analysis ADD COLUMN bar_offset_known INTEGER")
        self.db.commit()
    def get(self, path):
        fp = fingerprint(path)
        with self._lock:
            row = self.db.execute("SELECT bpm,confidence,beat_offset,key_name,camelot,key_confidence,beat_confidence,bar_offset,bar_offset_known FROM analysis WHERE path=? AND fingerprint=?",
                                  (str(Path(path).resolve()), fp)).fetchone()
        if not row: return None
        return dict(bpm=row[0], confidence=row[1], beat_offset=row[2], key=row[3], camelot=row[4], key_confidence=row[5], beat_confidence=row[6], bar_offset=row[7], bar_offset_known=row[8])
    def put(self, path, result):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO analysis VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (str(Path(path).resolve()), fingerprint(path), result.get('bpm'), result.get('confidence'),
                             result.get('beat_offset'), result.get('key'), result.get('camelot'), result.get('key_confidence'),
                             result.get('beat_confidence'), result.get('bar_offset'),
                             1 if result.get('bar_offset_known') else None))
            self.db.commit()
    def set_bar_offset(self, path, bar_offset):
        """Pass 28: update ONLY the bar offset of an existing row
        (the deck stepper's write-through). A missing row is a
        no-op — load_track writes the full row on the next load,
        carrying the Track's in-memory value's next default of 0;
        the stepper only ever runs on a loaded (hence cached)
        track. Pass 35: the write also marks the offset KNOWN —
        the user has now stated where the bar starts, which is
        what AUTO SYNC's bar alignment is allowed to trust."""
        with self._lock:
            self.db.execute("UPDATE analysis SET bar_offset=?, bar_offset_known=1 WHERE path=?",
                            (int(bar_offset), str(Path(path).resolve())))
            self.db.commit()
    def close(self): self.db.close()
