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
  (rate playback).
- Pass 33 (user request 2026-11-28: 「bpm変えても音変わらないように
  できる？」): KEY LOCK — a per-deck WSOLA time-stretch stage in the
  read path, ON by default. With it on, a deck's rate changes its
  tempo only; the pitch stays at the song's own. With it off, the
  deck is the original resampler (pitch follows rate, vinyl-style).
- beat grid, key and analysis cache via analysis.py.
- Pass 26: SYNC is tempo match + BEAT-PHASE alignment when both
  tracks' beat grids are confident (analysis.GRID_CONFIDENT) — a
  one-shot position shift on engage and on a synced deck's start,
  held by a render-loop phase lock; untrusted grids fall back to
  tempo-only and the deck's sync_mode says which happened. Master
  SET BPM moves ('set_bpm') scale all deck rates together.
- Pass 35 (user request 2026-11-28: 「オートシンク」「拍子を
  あわせるのとかできないの？」): AUTO SYNC — while the mode is
  on (Engine.auto_sync, default on), a deck that STARTS playing
  against a playing deck engages sync by itself: tempo match,
  beat-phase align, and — when BOTH tracks carry bar
  information (a user-stepped bar offset, or a rekordbox anchor
  cue) — a whole-beat shift on top so the bar lines coincide
  too. Bar correction happens only at that engage moment; the
  ongoing hold is still the pass-26 beat-level phase lock. A
  trusted grid without bar information aligns the beat phase
  only and the deck's sync_bar says so (False) — the app posts
  the 「小節情報なし」 note; an untrusted grid stays tempo-only.
  With the mode off, SYNC is exactly the pass-26 manual pad.
"""
from dataclasses import dataclass, field
from pathlib import Path
from queue import SimpleQueue
import json
import math
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly, correlate, find_peaks, lfilter
from analysis import BeatGrid, GRID_CONFIDENT, estimate_beat_grid, estimate_key

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
    # Pass 26 (rekordbox anchor): frame of the track's first
    # rekordbox hot cue, when cue data was attached to the track
    # (see apply_cue_anchor). The user's own cue outranks the
    # analysed grid phase — see _anchored_offset.
    anchor_cue: int | None = None
    # Pass 28 (user report 2026-11-28: 「小節があってない」):
    # which beat of the analysed grid starts the bar (0..3).
    # Analysis finds the BEAT phase only, so the downbeat is
    # ambiguous by whole beats; the user sets this per track
    # (deck stepper), it persists in the analysis cache, moves
    # the waveform's bar ticks, and the Live export aligns the
    # first track's downbeat to a bar line with it. Beat phase
    # (sync, quantize) never depends on it. (Declared last so
    # the existing positional constructions keep their meaning.)
    bar_offset: int = 0
    # Pass 35: whether bar_offset is INFORMATION — the user
    # stepped it (the cache row is marked known) — as opposed to
    # the analysis default of 0, which says nothing about bars.
    # AUTO SYNC's bar alignment trusts only this flag (or a
    # rekordbox anchor cue, whose beat is a downbeat by the
    # user's own marking). Declared last, like bar_offset.
    bar_offset_known: bool = False
    def grid(self):
        return BeatGrid(self.bpm, self.beat_offset) if self.bpm else None

def apply_cue_anchor(track, cues):
    """Anchor a track's beat grid to its first rekordbox hot cue.

    `cues` is the rekordbox-style cue list (library.py's
    import_rekordbox_xml output, carried on set-list entries):
    dicts with 'start' in seconds and 'num' (-1 = memory cue,
    0.. = hot cue A, B, C...). The lowest-numbered hot cue wins —
    Hot Cue A is where DJs mark the first beat. Memory cues and
    cue-less tracks leave the track unanchored. Returns the
    anchor frame, or None."""
    hot = [c for c in (cues or [])
           if isinstance(c, dict)
           and isinstance(c.get('num'), int) and c['num'] >= 0
           and isinstance(c.get('start'), (int, float))
           and c['start'] >= 0]
    if not hot:
        return None
    first = min(hot, key=lambda c: c['num'])
    track.anchor_cue = int(round(first['start'] * RATE))
    return track.anchor_cue

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
    peaks = compute_peaks(audio)
    if cached:
        beat_conf = cached.get('beat_confidence')
        bpm, offset = cached['bpm'], cached['beat_offset'] or 0
        bar_offset = cached.get('bar_offset') or 0
        # Pass 35: a bar offset the user stepped (cache-marked)
        # is information AUTO SYNC may align bars with; the
        # analysis default of 0 is not.
        bar_known = bool(cached.get('bar_offset_known'))
        if beat_conf is None:
            # Pass 26: a pre-pass-26 cache row has no grid
            # confidence. Measure the grid once (adopting the
            # refined BPM when the grid is confident, exactly as
            # a fresh analysis would) and enrich the row, so
            # later loads stay instant.
            offset, beat_conf, refined = estimate_beat_grid(audio, bpm)
            if beat_conf >= GRID_CONFIDENT and refined:
                bpm = refined
            cache.put(path, dict(bpm=bpm, confidence=cached['confidence'],
                                 beat_offset=offset, key=cached['key'],
                                 camelot=cached['camelot'],
                                 key_confidence=cached['key_confidence'],
                                 beat_confidence=beat_conf,
                                 bar_offset=bar_offset,
                                 bar_offset_known=bar_known))
        return Track(str(Path(path).resolve()), audio, peaks, bpm, cached['confidence'],
                     offset, beat_conf or 0.0, cached['key'], cached['camelot'],
                     cached['key_confidence'] or 0.0,
                     bar_offset=bar_offset, bar_offset_known=bar_known)
    bpm, confidence = estimate_bpm(audio)
    offset, beat_conf, refined = estimate_beat_grid(audio, bpm)
    if beat_conf >= GRID_CONFIDENT and refined:
        # The fold's refined BPM is the better tempo estimate
        # (the autocorrelation lag alone is up to ~0.3% off);
        # adopt it as THE track BPM so tempo sync, the dial and
        # the export all share one number. Only when confident —
        # a shaky grid must not corrupt a sound coarse BPM.
        bpm = refined
    key, camelot, key_conf = estimate_key(audio)
    if cache:
        cache.put(path, dict(bpm=bpm, confidence=confidence, beat_offset=offset,
                             key=key, camelot=camelot, key_confidence=key_conf,
                             beat_confidence=beat_conf, bar_offset=0))
    return Track(str(Path(path).resolve()), audio, peaks, bpm, confidence,
                 offset, beat_conf, key, camelot, key_conf)

# Peak data (pass 24, user report 2026-11-27: 「波形がまだガタガタの
# まま」). Passes up to 23 split the whole track into at most 1400
# buckets — for a 5-minute track each bucket spanned ~0.2 s, so the
# ±8 s window held ~75 fat bars and the waveform read as a staircase.
# Peaks are now computed at a FIXED hop (256 frames ≈ 5 ms at
# 48 kHz, ~188 buckets per second), independent of track length:
# the same window holds ~3000 buckets, several per pixel column.
# Standing rule (pass 23): this resolution may only ever go UP.
PEAK_HOP = 256    # frames per peak bucket

def compute_peaks(audio, hop=PEAK_HOP):
    """Per-bucket peak (max |sample| over channels) at a fixed hop.

    Vectorized: a per-frame channel max, zero-padded to a whole
    number of buckets, reshaped and reduced. Zero padding cannot
    raise a max, so the tail bucket stays honest."""
    frames = len(audio)
    if frames == 0:
        return np.zeros(0, dtype=np.float32)
    mono = np.abs(audio).max(axis=1) if audio.ndim == 2 else np.abs(audio)
    count = (frames + hop - 1) // hop
    if count * hop != frames:
        mono = np.pad(mono, (0, count * hop - frames))
    return mono.reshape(count, hop).max(axis=1).astype(np.float32)

def window_columns(peaks, total, start, end, width):
    """Per-pixel-column peak values for the frame window [start, end].

    Column x covers the window's x-th slice; its value is the TRUE
    max of the peak buckets intersecting that slice (never a sampled
    single bucket, so transients cannot fall between columns).
    Columns whose slice holds no bucket (window narrower than one
    bucket per column) take the nearest bucket's value; columns
    outside the track (the window is deliberately unclamped) stay 0.
    Qt-free and unit-tested; the paint code draws one 1px line per
    column from this array."""
    count = len(peaks)
    width = int(width)
    cols = np.zeros(max(0, width), dtype=np.float32)
    if count == 0 or total <= 0 or width <= 0:
        return cols
    if (end - start) * count / total < width:
        # Fewer buckets than columns (a very short track, or the
        # whole-track overview of one): each column takes the
        # bucket it sits in — upsampling, so no holes and nothing
        # to alias. Columns centred outside the track stay 0.
        centres = start + (np.arange(width) + 0.5) * (end - start) / width
        inside = (centres >= 0) & (centres < total)
        idx = np.floor(centres * count / total).astype(np.int64)
        cols[inside] = peaks[np.clip(idx[inside], 0, count - 1)]
        return cols
    # One or more buckets per column (constant advance >= 1 bucket,
    # so every column's range is non-empty and consecutive starts
    # strictly increase). Column i's true range is [floor(b_i),
    # ceil(b_{i+1})) in bucket space: it may reach ONE bucket past
    # the next column's start (the bucket the boundary falls in),
    # which the tail fix below adds back.
    bounds = (start + np.arange(width + 1) * (end - start) / width) \
        * count / total
    starts = np.clip(np.floor(bounds[:-1]).astype(np.int64), 0, count)
    stops = np.clip(np.ceil(bounds[1:]).astype(np.int64), 0, count)
    nonempty = stops > starts
    if not nonempty.any():
        return cols
    nstarts, nstops = starts[nonempty], stops[nonempty]
    # reduceat reduces [idx[i], idx[i+1]); appending the last
    # column's stop (into a zero-padded array, so an index equal
    # to count is valid) keeps its range from running to the
    # end of the track.
    idx = np.concatenate([nstarts, [nstops[-1]]])
    padded = np.concatenate([peaks, np.zeros(1, dtype=peaks.dtype)])
    vals = np.maximum.reduceat(padded, idx)[:-1]
    vals = np.maximum(vals, peaks[np.clip(nstops - 1, 0, count - 1)])
    cols[nonempty] = vals
    return cols

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


# Pass 33: KEY LOCK time-stretch (WSOLA). A window of
# STRETCH_WINDOW frames is overlap-added every STRETCH_HOP output
# frames; the similarity search may move a window's source start by
# at most ±STRETCH_TOL frames from its nominal position. The
# nominal analysis cursor advances by exactly STRETCH_HOP * rate
# per window regardless of the offset chosen, so the
# source->output time mapping is anchored and cannot drift:
# output frame t renders the source around start + t * rate, and
# the deck's reported position advances exactly as it does in the
# resample path — sync, phase lock and the journal keep their
# existing source-frame semantics untouched. Both decks run the
# identical stage, so the read-ahead (~STRETCH_WINDOW frames,
# ≈43 ms) is the same on both and no position compensation is
# needed anywhere. Cleanest in the 0.85–1.18 rate range (SET BPM
# ±6 plus track-vs-set differences); outside it the stage still
# runs — quality degrades, nothing drops or crashes.
STRETCH_WINDOW = 2048
STRETCH_HOP = 512
STRETCH_TOL = 512

class _TimeStretcher:
    """Incremental WSOLA state for one deck (pass 33).

    `reset(position)` primes the stage at a source position; any
    discontinuity (seek / cue / hotcue jump, sync_align nudge,
    loop-state change of position, track load, key-lock toggle)
    is detected by the deck read comparing the live position
    against `expected` — the position the previous read left —
    and resets here, so no audio is ever smeared across a jump.
    """

    def __init__(self):
        self.win = np.hanning(STRETCH_WINDOW).astype(np.float64)
        self.expected = None
        self.reset(0.0)

    def reset(self, position):
        self.ana = float(position)   # nominal source start of the next window
        self.out = np.zeros((0, 2), dtype=np.float64)
        self.env = np.zeros(0, dtype=np.float64)
        self.head = 0                # frames of `out` already emitted
        self.synth = 0               # output index the next window lands on
        self.primed = False
        # `expected` is deliberately NOT set here: the deck read
        # owns it (it knows the position it last left behind).

    @staticmethod
    def _source(audio, start, count, loop):
        """`count` source frames from integer `start`, as float64.
        Loop-active reads wrap at the loop end exactly like the
        resample path's index mapping; non-loop reads past the
        track end (and before 0) come back as silence."""
        total = len(audio)
        idx = start + np.arange(count)
        if loop is not None:
            lo, hi = loop
            idx = np.where(idx >= hi, lo + (idx - lo) % (hi - lo), idx)
            return audio[np.clip(idx, 0, total - 1)].astype(np.float64)
        out = np.zeros((count, 2), dtype=np.float64)
        valid = (idx >= 0) & (idx < total)
        if np.any(valid):
            out[valid] = audio[idx[valid]]
        return out

    def _place_window(self, audio, rate, loop):
        W, HOP, TOL = STRETCH_WINDOW, STRETCH_HOP, STRETCH_TOL
        overlap = W - HOP
        nominal = self.ana
        chosen = int(round(nominal))
        if self.primed:
            # Similarity search: the candidate window whose head
            # best continues the already-synthesised overlap wins.
            # Correlation is normalised by candidate energy so a
            # loud passage cannot win on level alone.
            ref = np.zeros(overlap, dtype=np.float64)
            env = self.env[self.synth:self.synth + overlap]
            seg = self.out[self.synth:self.synth + overlap]
            ok = env > 1e-4
            if np.any(ok):
                ref[ok] = seg.mean(axis=1)[ok] / env[ok]
            if np.max(np.abs(ref)) > 1e-9:
                cand = self._source(audio, int(round(nominal)) - TOL,
                                    overlap + 2 * TOL, loop).mean(axis=1)
                scores = np.correlate(cand, ref, mode='valid')
                energy = np.sqrt(np.convolve(
                    cand * cand, np.ones(overlap), mode='valid'))
                scores = scores / (energy + 1e-9)
                chosen += int(np.argmax(scores)) - TOL
        src = self._source(audio, chosen, W, loop)
        end = self.synth + W
        if len(self.out) < end:
            pad = end - len(self.out)
            self.out = np.concatenate(
                [self.out, np.zeros((pad, 2), dtype=np.float64)])
            self.env = np.concatenate(
                [self.env, np.zeros(pad, dtype=np.float64)])
        self.out[self.synth:end] += src * self.win[:, None]
        self.env[self.synth:end] += self.win
        self.ana = nominal + HOP * rate
        self.synth += HOP
        self.primed = True

    def read(self, audio, rate, frames, loop):
        """`frames` pitch-preserved output frames. Source is
        consumed at `rate` source frames per output frame (the
        caller advances the deck position by frames * rate, the
        same accounting as the resample path)."""
        while self.synth < self.head + frames:
            self._place_window(audio, rate, loop)
        seg = self.out[self.head:self.head + frames]
        env = self.env[self.head:self.head + frames]
        out = np.zeros((frames, 2), dtype=np.float64)
        ok = env > 1e-4
        if np.any(ok):
            out[ok] = seg[ok] / env[ok, None]
        self.head += frames
        if self.head >= 16384:   # compact the consumed prefix
            self.out = self.out[self.head:].copy()
            self.env = self.env[self.head:].copy()
            self.synth -= self.head
            self.head = 0
        return out.astype(np.float32)


def _new_deck():
    # Pass 28: volume starts at unity (1.0). The old 0.8 start,
    # stacked on the master's 0.8 start, cost ~3.9 dB before the
    # crossfader — part of the user's 「音が小さい」 report
    # (measured in tests/test_pass28_gain_chain.py).
    # Pass 33: key_lock defaults ON (the user asked for the
    # behaviour itself) and persists across loads (see _apply).
    return dict(track=None, position=0, playing=False, volume=1.0,
                gain=1.0, eq_low=1.0, eq_mid=1.0, eq_high=1.0, filter=0.5,
                cue=0, hotcues={}, loop_start=None, loop_end=None, loop_active=False,
                quantize=False, rate=1.0, sync=False, sync_mode=None,
                sync_bar=None,
                key_lock=True, _stretch=None,
                _lp_low=None, _lp_high=None, _lp_filter=None, _hp_filter=None)

# Pass 26 (REAL SYNC): phase alignment constants. The lock corrects
# only when the phase error exceeds 0.03 beats (~15 ms at 120 BPM)
# and always corrects fully, so corrections are rare, small seeks —
# never a continuous rate wobble. A cue point anchors the grid only
# when it already sits within 0.15 beats of a grid line: a cue the
# user placed ON a beat then repairs a slightly-off grid phase,
# while a cue placed on a vocal or a pickup (far from any beat)
# is left alone.
PHASE_LOCK_THRESHOLD_BEATS = 0.03
CUE_ANCHOR_MAX_SHIFT_BEATS = 0.15

def grid_offset_through(grid, point, max_shift_beats=None):
    """The grid offset shifted (by at most half a beat) so a beat
    line passes exactly through `point` (a frame). With
    max_shift_beats, a point farther than that from every beat
    line leaves the offset untouched — the gate that keeps an
    off-beat cue from dragging a good grid away."""
    beat = grid.beat_frames
    shift = (point - grid.offset) % beat
    if shift > beat / 2:
        shift -= beat
    if max_shift_beats is not None \
            and abs(shift) > max_shift_beats * beat:
        return grid.offset
    return grid.offset + shift

def _anchored_offset(deck, grid):
    """The grid offset a deck's phase is judged against, in
    priority order:
    1. the track's rekordbox hot-cue anchor (apply_cue_anchor) —
       the user's own cue, trusted FULLY: the grid is re-phased
       through it however far the analysis was off;
    2. the deck's live cue point, but only as a hint — it
       corrects the phase when it already sits near a grid beat
       (CUE_ANCHOR_MAX_SHIFT_BEATS);
    3. the analysed offset itself."""
    track = deck['track']
    if track is not None and track.anchor_cue is not None:
        return grid_offset_through(grid, track.anchor_cue)
    if deck['cue']:
        return grid_offset_through(
            grid, deck['cue'], CUE_ANCHOR_MAX_SHIFT_BEATS)
    return grid.offset

class Engine:
    def __init__(self):
        self.decks = [_new_deck() for _ in range(2)]
        self.cross = 0.5
        self.master = 1.0   # pass 28: unity start (was 0.8)
        self._cur = dict(cross=0.5, master=1.0)
        self._deck_cur = [dict(volume=1.0, gain=1.0, eq_low=1.0, eq_mid=1.0, eq_high=1.0, filter=0.5) for _ in range(2)]
        self.commands = SimpleQueue()
        self.events = SimpleQueue()
        self.frame = 0
        self.peak = 0.0
        self.overloads = 0
        self.preview = None
        self.master_deck = 0
        # Pass 26 (SET BPM follow): the set's master tempo. None
        # until the window anchors it (set list / Deck A BPM);
        # 'set_bpm' moves scale every deck rate proportionally.
        self.set_bpm = None
        # Pass 35 (AUTO SYNC): while on (the default), a deck
        # starting against a playing deck engages sync by itself
        # — tempo, beat phase and, with bar information on both
        # tracks, the bar line too (see _engage_sync). The
        # 'auto_sync' command toggles it and is journalled, so a
        # replay knows the mode the performance ran under.
        self.auto_sync = True
        # Pass 37: the headphone cue bus's monitor set — deck
        # indices whose post-EQ / post-gain, pre-crossfader
        # signal render() also writes into a cue buffer when the
        # caller provides one. The window mirrors its per-deck
        # monitor state (pass 28) into this set; with no cue
        # buffer requested, rendering costs nothing extra.
        self.cue_decks = set()

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

    # -- pass 26: beat-phase sync ------------------------------------
    @staticmethod
    def _track_trusted(track):
        """A track's grid may drive phase alignment when the
        analysis is confident — OR when the user anchored it
        with a rekordbox hot cue: the anchor fixes the phase by
        hand, so the anchored grid is treated as confident even
        when the estimator was not (the tempo spacing still
        comes from the analysis BPM)."""
        return (track is not None and track.grid() is not None
                and (track.beat_confidence >= GRID_CONFIDENT
                     or track.anchor_cue is not None))

    def _grids_trusted(self, d, other):
        """Phase alignment is offered only when BOTH tracks'
        grids are trusted (see _track_trusted). Anything less —
        a beatless intro, a drifting live recording — and SYNC
        stays tempo-only rather than faking an alignment."""
        return (self._track_trusted(d['track'])
                and self._track_trusted(other['track']))

    def _phase_shift(self, d, other):
        """Frames to ADD to d's position so d's beats coincide
        with the other deck's, in (-beat/2, beat/2] of d's grid.
        The phases are compared in track-frame space; once the
        tempo match has equalised the two decks' beat rates the
        shift stays correct as both play. Caller checks trust."""
        gs, gm = d['track'].grid(), other['track'].grid()
        ph_s = ((d['position'] - _anchored_offset(d, gs))
                / gs.beat_frames) % 1.0
        ph_m = ((other['position'] - _anchored_offset(other, gm))
                / gm.beat_frames) % 1.0
        err = (ph_m - ph_s) % 1.0
        if err > 0.5:
            err -= 1.0
        return err * gs.beat_frames

    # -- pass 35: bar (downbeat) alignment ---------------------------
    @staticmethod
    def _bar_phase(deck, pos):
        """(phase, known): the deck's position inside its bar
        cycle, in beats [0, 4), and whether the track carries
        bar information at all. Two sources, in priority order:
        a bar offset the user stepped (bar_offset_known) counts
        beats from the anchored grid offset, exactly as the
        waveform's bar ticks do; a rekordbox anchor cue counts
        beats from the cue itself — Hot Cue A is the user's own
        downbeat marking. Anything else (the analysis default)
        is NOT information: known=False, and AUTO SYNC must not
        pretend to align bars with it."""
        track = deck['track']
        grid = track.grid() if track is not None else None
        if grid is None:
            return 0.0, False
        beat = grid.beat_frames
        if track.bar_offset_known:
            return ((pos - _anchored_offset(deck, grid)) / beat
                    - track.bar_offset) % 4.0, True
        if track.anchor_cue is not None:
            return ((pos - track.anchor_cue) / beat) % 4.0, True
        return 0.0, False

    def _bar_shift_frames(self, d, other, slave_pos, floor_pos):
        """Whole-beat frames to ADD to the slave's (already
        beat-phase-aligned) position so its bar lines coincide
        with the master's, plus whether the shift could be
        computed at all. The forward shift is 0–3 beats; the
        equivalent backward shift (forward − 4) wins only when
        it is strictly smaller AND would not cross floor_pos —
        the position the slave started from. A start must never
        be dragged back before its own cue: there the next bar
        forward is taken instead."""
        ph_s, known_s = self._bar_phase(d, slave_pos)
        ph_m, known_m = self._bar_phase(other, other['position'])
        if not (known_s and known_m):
            return 0.0, False
        beat = d['track'].grid().beat_frames
        fwd = int(round((ph_m - ph_s) % 4.0)) % 4
        if fwd == 0:
            return 0.0, True
        beats = fwd if fwd <= 2 else fwd - 4
        if beats < 0 and slave_pos + beats * beat < floor_pos:
            beats = fwd
        return beats * beat, True

    def _align_phase(self, index, extra_events, bar_align=False):
        """Shift the deck onto the other deck's beat phase and
        record the result as an explicit 'sync_align' journal
        event (absolute position), so performance.py's replay
        reproduces the aligned performance exactly instead of
        having to re-derive it. Sets sync_mode: 'phase' when the
        grids carried it, 'tempo' when SYNC could only match
        tempo (the UI says so in the status bar).

        Pass 35: with bar_align (an AUTO SYNC engage), a whole-
        beat shift rides on top of the phase shift so the bar
        lines coincide too, and sync_bar records the outcome:
        True (bars aligned), False (beat phase only — a track
        has no bar information; the UI posts the note), None
        (not an auto engage / nothing to say)."""
        d = self.decks[index]
        other = self.decks[1 - index]
        if not self._grids_trusted(d, other):
            d['sync_mode'] = 'tempo' if (
                d['track'] and other['track']
                and d['track'].bpm and other['track'].bpm) else None
            d['sync_bar'] = None
            return
        start_pos = d['position']
        shift = self._phase_shift(d, other)
        new_pos = start_pos + shift
        d['sync_bar'] = None
        if bar_align:
            bar_shift, known = self._bar_shift_frames(
                d, other, new_pos, start_pos)
            new_pos += bar_shift
            d['sync_bar'] = known
        new_pos = float(np.clip(new_pos, 0, len(d['track'].audio)))
        if new_pos != d['position']:
            d['position'] = new_pos
            extra_events.append(('sync_align', index, int(round(new_pos))))
        d['sync_mode'] = 'phase'

    def _engage_sync(self, index, extra_events, bar_align=False,
                     journal_sync=False):
        """The SYNC engagement itself: flag the deck synced,
        match its tempo to the other deck's effective tempo
        (which is the SET BPM whenever the master follows it),
        then align. Shared by the manual 'sync' command (pass
        26: beat phase only, the command's own journal event
        already records the engagement) and by AUTO SYNC's
        play-start engage (pass 35: bar alignment on top, and a
        'sync' journal event is minted as an extra — the replay
        recomputes the tempo match from it, exactly as it does
        for a manual engagement)."""
        d = self.decks[index]
        other = self.decks[1 - index]
        d['sync'] = True
        if journal_sync:
            extra_events.append(('sync', index, True))
        if d['track'] and other['track'] \
                and d['track'].bpm and other['track'].bpm:
            d['rate'] = float(np.clip(
                other['track'].bpm * other['rate'] / d['track'].bpm,
                0.5, 2.0))
            self._align_phase(index, extra_events,
                              bar_align=bar_align)
        else:
            d['sync_mode'] = None
            d['sync_bar'] = None

    def _phase_lock(self):
        """Ongoing drift correction, run once per render block:
        a synced, playing deck whose phase has drifted past the
        threshold against the playing other deck is nudged back
        onto the beat with one small seek (journalled like any
        other sync_align). Rate ratios never change here, so the
        lock cannot wobble or fight the tempo match — and a
        master SET BPM move scales both decks together, leaving
        the phase error untouched."""
        if self.preview is not None:
            return
        for index, d in enumerate(self.decks):
            if not (d['sync'] and d['playing']) or d['loop_active']:
                continue
            other = self.decks[1 - index]
            if not other['playing'] or not self._grids_trusted(d, other):
                continue
            if other['sync']:
                # Pass 27: BOTH decks SYNC'd — each lock pass would
                # nudge each deck against the other's just-moved
                # position, and the pair chases its own tail (the
                # real-machine journal showed bursts of sync_align
                # events ~50 ms apart on BOTH decks at once, which
                # the export then sliced into sliver clips). With
                # mutual sync the engage-time alignment is the
                # reference; the lock only ever corrects a deck
                # whose partner is NOT itself sync-locked.
                continue
            shift = self._phase_shift(d, other)
            beat = d['track'].grid().beat_frames
            if abs(shift) > PHASE_LOCK_THRESHOLD_BEATS * beat:
                new_pos = float(np.clip(d['position'] + shift, 0,
                                        len(d['track'].audio)))
                if new_pos != d['position']:
                    d['position'] = new_pos
                    self.events.put(dict(
                        frame=self.frame, sample_rate=RATE,
                        action='sync_align', deck=index,
                        value=int(round(new_pos))))

    def _apply(self):
        while not self.commands.empty():
            action, index, value = self.commands.get()
            d = self.decks[index] if index is not None else None
            # Pass 26: side-effect journal events a handler mints
            # (sync_align positions, per-deck tempo events of a
            # master SET BPM move). They are emitted right after
            # the command's own event, at the same frame, in order.
            extra_events = []
            if action == 'load':
                keep = {k: d[k] for k in ('volume','gain','eq_low','eq_mid','eq_high','filter','quantize','key_lock')}
                d.update(_new_deck()); d.update(keep)
                self._deck_cur[index] = {k: d[k] for k in ('volume','gain','eq_low','eq_mid','eq_high','filter')}
                d['track'] = value
                value = value.path
            elif action == 'play':
                if d['track'] is not None:
                    if d['position'] >= len(d['track'].audio): d['position'] = 0
                    d['playing'] = bool(value)
                    if value and self.decks[1 - index]['playing']:
                        if self.auto_sync:
                            # Pass 35 (AUTO SYNC): a deck starting
                            # against a playing deck engages sync
                            # by itself — tempo, beat phase and
                            # the bar line — at this exact moment.
                            self._engage_sync(index, extra_events,
                                              bar_align=True,
                                              journal_sync=True)
                        elif d['sync']:
                            # Pass 26: a synced deck STARTING against a
                            # playing master is aligned at this exact
                            # moment — its beats land on the master's
                            # current phase (the start-quantize). An
                            # alignment computed earlier, while the
                            # deck sat stopped, would have gone stale.
                            self._align_phase(index, extra_events)
            elif action == 'seek':
                if d['track'] is not None:
                    d['position'] = self._snap(d, int(np.clip(value, 0, len(d['track'].audio))))
            elif action == 'jog':
                # Pass 28 (user report 2026-11-28: 「タンテの解像度を
                # もっとあげて」): the jog sends a signed DELTA in
                # frames, applied to the live position. The delta
                # form is the fix for the real resolution limit:
                # pass 23-27 computed each seek target in the MIDI
                # layer from the engine position of that instant,
                # so every message drained in one poll burst aimed
                # at nearly the same place and only the last one's
                # step survived — a fast spin lost most of its
                # travel and a slow one stepped coarsely. Deltas
                # accumulate in the command queue itself. NO
                # quantize snap: snapping a continuous control to
                # beats would destroy the resolution outright
                # (seek / cue_set keep their snap — those are
                # deliberate jumps).
                if d['track'] is not None:
                    d['position'] = float(np.clip(
                        d['position'] + float(value), 0,
                        len(d['track'].audio)))
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
            elif action == 'key_lock':
                # Pass 33: pitch-preserving stretch on/off for
                # this deck. The stretcher is dropped so the next
                # read primes in the new mode at the live position
                # — no smeared transition between the two paths.
                d['key_lock'] = bool(value)
                d['_stretch'] = None
            elif action == 'tempo': d['rate'] = float(np.clip(value, 0.5, 2.0))
            elif action == 'sync':
                if value:
                    # Pass 26: tempo match PLUS beat-phase align
                    # (one-shot shift of at most half a beat; the
                    # render loop's phase lock holds it from
                    # here). Untrusted grids degrade this to the
                    # tempo match alone — see _align_phase. The
                    # manual pad never bar-aligns (pass 35: bars
                    # are AUTO SYNC's job).
                    self._engage_sync(index, extra_events)
                else:
                    d['sync'] = False
                    d['rate'] = 1.0
                    d['sync_mode'] = None
                    d['sync_bar'] = None
            elif action == 'auto_sync':
                # Pass 35: the AUTO SYNC mode toggle (set-level,
                # deck is None). Bookkeeping only — engagements
                # happen on later 'play' commands; decks already
                # synced keep their state either way.
                self.auto_sync = bool(value)
            elif action == 'set_bpm_anchor':
                # Pass 26: the window names the set's base tempo
                # (set list / Deck A). Bookkeeping only — no deck
                # moves, nothing scales.
                if value: self.set_bpm = float(value)
            elif action == 'set_bpm':
                # Pass 26: a master tempo move (DDJ-RB TEMPO
                # fader / SET BPM dial). Every deck rate scales
                # by the same ratio — sync ratios and phase
                # alignment are preserved — and each deck's new
                # rate is journalled as an ordinary 'tempo' event
                # so the replay follows exactly.
                new = float(value) if value else None
                old = self.set_bpm
                if new and old:
                    ratio = new / old
                    for j, dd in enumerate(self.decks):
                        rate = float(np.clip(dd['rate'] * ratio, 0.5, 2.0))
                        if rate != dd['rate']:
                            dd['rate'] = rate
                            extra_events.append(('tempo', j, rate))
                if new: self.set_bpm = new
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
            for e_action, e_deck, e_value in extra_events:
                self.events.put(dict(frame=self.frame, sample_rate=RATE,
                                     action=e_action, deck=e_deck,
                                     value=e_value))

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

    def _read_deck_stretched(self, d, frames):
        """Pass 33: the key-locked read. The WSOLA stage renders
        `frames` output frames while the deck position advances by
        exactly frames * rate — the same accounting as the
        resample path, including its loop wrap and end-of-track
        stop — so position reporting, sync and the journal are
        untouched by the stretch. Any position discontinuity
        since the last read (seek / cue / hotcue / sync_align /
        jog landing between blocks) resets the stage at the live
        position instead of smearing audio across the jump."""
        audio = d['track'].audio
        rate = d['rate']
        loop = None
        if d['loop_active'] and d['loop_start'] is not None \
                and d['loop_end'] is not None \
                and d['loop_end'] > d['loop_start']:
            loop = (d['loop_start'], d['loop_end'])
        st = d['_stretch']
        if st is None:
            st = d['_stretch'] = _TimeStretcher()
            st.reset(d['position'])
        elif st.expected is not None \
                and abs(float(d['position']) - st.expected) > 1e-6:
            st.reset(d['position'])
        block = st.read(audio, rate, frames, loop)
        end = d['position'] + frames * rate
        if loop is not None and end >= loop[1]:
            lo, hi = loop
            end = lo + (end - lo) % (hi - lo)
        d['position'] = min(end, len(audio))
        if loop is None and end >= len(audio):
            d['playing'] = False
        st.expected = float(d['position'])
        return block

    def _read_deck(self, d, frames):
        if d['key_lock']:
            return self._read_deck_stretched(d, frames)
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

    def render(self, frames, cue=None):
        """Render one block of the master mix. Pass 37: when
        `cue` is a (frames, 2) buffer, it is filled with the
        headphone cue mix — every deck in self.cue_decks, post
        EQ/filter and post volume*gain, PRE crossfader and pre
        master gain (what a DJ hears in the headphones while
        the room hears the master). The cue mix is soft-limited
        like the master; decks that are not playing contribute
        silence, as they do to the master."""
        self._apply()
        self._phase_lock()
        out = np.zeros((frames, 2), dtype='float32')
        if cue is not None:
            cue[:] = 0
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
                deck_mix = block*level
                if cue is not None and di in self.cue_decks:
                    cue[:n] += deck_mix
                out[:n] += deck_mix*gain[:n]
        if cue is not None:
            cue[:] = _soft_limit(cue.astype('float32'))
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
