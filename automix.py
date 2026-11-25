"""Auto-mix planner (Phase 3 core, pure logic — no audio rendering).

Given analysed tracks it proposes *musically* placed transitions instead of
naively overlapping a track tail with the next head:

* mix points snap to phrase boundaries (bars from the beat grid),
* overlap is chosen from 8/16/32/64 bars,
* the bass swap happens at a bar line in the middle of the overlap,
* keys are checked with the Camelot rule, tempo gap is reported,
* several candidates are returned so the user can A/B-preview them.

Vocal-overlap avoidance is a heuristic: a section whose mid-band energy is
high relative to its total energy is treated as "vocal likely" and the
planner avoids overlapping two such sections.  It is not a vocal detector.
"""
from dataclasses import dataclass, asdict
from analysis import BeatGrid, keys_compatible

OVERLAP_BARS = (8, 16, 32, 64)

@dataclass
class TrackInfo:
    path: str
    bpm: float
    duration_frames: int
    beat_offset: int = 0
    key: str | None = None
    camelot: str | None = None
    vocal_likely_frames: tuple = ()   # (start, end) ranges, heuristic
    energy: float = 0.5               # 0..1 overall energy estimate

@dataclass
class Transition:
    from_path: str
    to_path: str
    mix_out_frame: int     # where the outgoing track starts fading
    mix_in_frame: int      # where the incoming track starts (usually phrase start)
    overlap_frames: int
    overlap_bars: int
    bass_swap_frame: int   # position *inside the outgoing timeline*
    fade: str              # equal-power curve name used by the preview
    key_ok: bool | None
    bpm_delta: float
    warnings: tuple
    score: float
    def to_dict(self): return asdict(self)

def _overlaps_vocal(info, start, end):
    return any(s < end and start < e for s, e in info.vocal_likely_frames)

def plan_transition(a: TrackInfo, b: TrackInfo, overlap_bars=32):
    ga, gb = BeatGrid(a.bpm, a.beat_offset), BeatGrid(b.bpm, b.beat_offset)
    overlap = int(ga.bar_frames * overlap_bars)
    # Outgoing: leave from a phrase boundary, keeping the overlap inside the track.
    mix_out = ga.nearest_bar(max(0, a.duration_frames - overlap - int(ga.bar_frames*16)))
    mix_out = max(0, min(mix_out, a.duration_frames - overlap))
    # Incoming: start at a phrase boundary near the head (after a short intro).
    mix_in = gb.nearest_bar(gb.offset + int(gb.bar_frames*8))
    if mix_in + overlap > b.duration_frames: mix_in = gb.nearest_bar(gb.offset)
    warnings = []
    key_ok = keys_compatible(a.key, a.camelot, b.key, b.camelot)
    if key_ok is False: warnings.append('キーが合っていません / key clash')
    bpm_delta = round(b.bpm - a.bpm, 2)
    if abs(bpm_delta) > 6: warnings.append('BPM差が大きいです / large BPM gap')
    if _overlaps_vocal(a, mix_out, mix_out+overlap) and _overlaps_vocal(b, mix_in, mix_in+overlap):
        warnings.append('ボーカル同士が重なる可能性があります / possible vocal overlap')
    score = 1.0
    if key_ok is False: score -= 0.3
    score -= min(0.3, abs(bpm_delta)/20)
    if warnings and 'vocal' in warnings[-1]: score -= 0.2
    return Transition(a.path, b.path, int(mix_out), int(mix_in), overlap, overlap_bars,
                      int(mix_out + ga.bar_frames*(overlap_bars//2)), 'equal-power',
                      key_ok, bpm_delta, tuple(warnings), round(max(0.0, score), 3))

def plan_candidates(a: TrackInfo, b: TrackInfo):
    """All overlap lengths, best first — for A/B preview listening."""
    plans = [plan_transition(a, b, bars) for bars in OVERLAP_BARS]
    return sorted(plans, key=lambda t: t.score, reverse=True)

def plan_set(tracks, overlap_bars=32):
    return [plan_transition(a, b, overlap_bars) for a, b in zip(tracks, tracks[1:])]
