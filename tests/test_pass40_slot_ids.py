"""Pass 40(a): session-slot extension must not duplicate Ids.

Reproduced before the fix: a base with ONE slot per track and a
performance with three plays on deck A made the writer extend
the track to three slots by cloning — and all three outer
ClipSlot elements carried the SOURCE slot's Id (50, 50, 50),
while the clips inside them were correctly re-ided. Element
Ids are identities in a Live set; writer-created slot shells
now get fresh Ids (the caller's document allocator when there
is one, else the track's own numbering continued), on BOTH
export paths (performance -> perf_als, setlist plan ->
als_writer).
"""
from collections import Counter

import als_writer
import perf_als
from engine import RATE
from test_als_writer import (_clip as plan_clip, _make_wav as plan_wav,
                             _plan, _write_base as plan_base)
from test_perf_als import _make_wav, _write_base


def _all_ids(root):
    return [el.get('Id') for el in root.iter()
            if el.get('Id') is not None]


def _assert_ids_unique(root):
    ids = _all_ids(root)
    dups = {k: c for k, c in Counter(ids).items() if c > 1}
    assert dups == {}, f'duplicate element Ids: {dups}'


def test_perf_export_extended_slots_have_unique_ids(tmp_path):
    base = _write_base(tmp_path)             # ONE slot per track
    wav = _make_wav(tmp_path / 'Alpha.wav', seconds=30.0)

    def F(s):
        return int(s * RATE)

    def ev(frame, action, deck=None, value=None):
        return dict(frame=frame, sample_rate=RATE, action=action,
                    deck=deck, value=value)

    events = [ev(0, 'load', 0, str(wav))]
    for k in range(3):                       # three separate plays
        events.append(ev(F(1 + k * 4), 'play', 0, True))
        events.append(ev(F(3 + k * 4), 'play', 0, False))
    out = tmp_path / 'perf.als'
    report = perf_als.generate(base, events, out, set_bpm=120.0,
                               bpms={str(wav): 120.0})
    assert report['session_clips']['A'] == 3
    assert report['slots_extended']['A'] == 2

    root = als_writer.load_als(out).getroot()
    track = als_writer._audio_tracks(root)[0]
    slots = als_writer._slots(track)
    assert len(slots) == 3
    # One clip per filled slot, and every slot shell Id distinct.
    for slot in slots:
        value = als_writer._slot_value(slot)
        assert len(value.findall('AudioClip')) == 1
    slot_ids = [s.get('Id') for s in slots]
    assert len(set(slot_ids)) == 3, slot_ids
    _assert_ids_unique(root)


def test_plan_export_extended_slots_have_unique_ids(tmp_path):
    base = plan_base(tmp_path)
    wav = plan_wav(tmp_path / 'one.wav')
    plan = _plan([
        plan_clip(1, 'A', 0, wav, 'One - Tester'),
        plan_clip(2, 'A', 1, wav, 'Two - Tester'),
        plan_clip(3, 'A', 2, wav, 'Three - Tester'),
        plan_clip(4, 'A', 3, wav, 'Four - Tester'),
        plan_clip(5, 'B', 0, wav, 'Five - Tester'),
        plan_clip(6, 'B', 1, wav, 'Six - Tester'),
    ])
    out = tmp_path / 'out.als'
    report = als_writer.generate(base, plan, out)
    assert report['clips_written'] == 6

    # This fixture's BASE already repeats Id="0" across its own
    # hand-written slots — not the writer's doing, and the writer
    # must not renumber what was already there. The contract is
    # over what the writer CREATES: the slot shells beyond the
    # base's own count per track.
    base_root = als_writer.load_als(base).getroot()
    base_counts = [len(als_writer._slots(t))
                   for t in als_writer._audio_tracks(base_root)]
    root = als_writer.load_als(out).getroot()
    created_total = 0
    for track, base_n in zip(als_writer._audio_tracks(root),
                             base_counts):
        slots = als_writer._slots(track)
        originals, created = slots[:base_n], slots[base_n:]
        created_total += len(created)
        created_ids = [s.get('Id') for s in created]
        assert len(set(created_ids)) == len(created_ids)
        assert not (set(created_ids)
                    & {s.get('Id') for s in originals})
    assert created_total >= 1                # extension happened
    # Every session clip in the output was placed by the writer;
    # their Ids are pairwise unique.
    clip_ids = []
    for track in als_writer._audio_tracks(root):
        for slot in als_writer._slots(track):
            value = als_writer._slot_value(slot)
            clip_ids += [c.get('Id')
                         for c in value.findall('AudioClip')]
    assert len(clip_ids) == 6
    assert len(set(clip_ids)) == 6


def test_ensure_slots_without_allocator_continues_numbering(tmp_path):
    base = _write_base(tmp_path)
    root = als_writer.load_als(base).getroot()
    track = als_writer._audio_tracks(root)[0]
    report = {'slots_extended': {}}
    slots = als_writer._ensure_slots(track, 3, report, 'A')
    # Fixture slot Id is 50; clones continue 51, 52 — never 50.
    assert [s.get('Id') for s in slots] == ['50', '51', '52']
    assert report['slots_extended']['A'] == 2
