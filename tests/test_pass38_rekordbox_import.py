"""Pass 38 (open since pass 26): rekordbox XML import into the
app + cue-preserving set-list save.

- setlist.save_setlist keeps a track's cues (load → save → load
  intact), while a cue-less track's saved form stays exactly
  what it was before this pass (no cues key appears);
- setlist_from_rekordbox_tracks turns library's XML import
  output into the app's normalized set list with path / BPM /
  key / cues on the rows;
- end to end: fixture XML -> import -> the entry's cues anchor
  a loaded track's grid from the first hot cue (the pass-26
  path) -> save -> reload -> cues still present.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

from engine import RATE, Track, apply_cue_anchor, compute_peaks
from library import import_rekordbox_xml
from setlist import (load_setlist, save_setlist,
                     setlist_from_rekordbox_tracks)

XML = """<DJ_PLAYLISTS><COLLECTION>
<TRACK Name="Song X" AverageBpm="124.0" Tonality="Am" Location="file://localhost/music/x.wav">
<POSITION_MARK Name="Intro" Type="0" Start="0.0" Num="0"/>
<POSITION_MARK Name="" Type="0" Start="32.5" Num="1"/>
<POSITION_MARK Name="Break" Type="0" Start="96.0" Num="-1"/>
<POSITION_MARK Name="Loop8" Type="4" Start="128.0" End="143.5" Num="2"/>
</TRACK>
<TRACK Name="Song Y" AverageBpm="122.5" Tonality="G" Location="file://localhost/music/y.wav">
</TRACK></COLLECTION></DJ_PLAYLISTS>"""

CUES = [dict(name='Intro', type='0', start=0.0, end=None, num=0),
        dict(name='', type='0', start=32.5, end=None, num=1),
        dict(name='Break', type='0', start=96.0, end=None, num=-1),
        dict(name='Loop8', type='4', start=128.0, end=143.5, num=2)]


def _setlist_with_cues():
    return {"format": "mixcraft-setlist", "version": 1,
            "set_bpm": 124.0,
            "tracks": [
                {"title": "Song X", "artist": "A", "bpm": 124.0,
                 "key": "Am", "path": "/music/x.wav", "cues": CUES},
                {"title": "Song Y", "artist": "B", "bpm": 122.5,
                 "key": "G", "path": "/music/y.wav"}]}


def test_save_preserves_cues_round_trip(tmp_path):
    out = tmp_path / 'set.json'
    saved = save_setlist(_setlist_with_cues(), out)
    assert saved['tracks'][0]['cues'] == CUES
    reloaded = load_setlist(out)
    assert reloaded['tracks'][0]['cues'] == CUES
    # Saving the reloaded list changes nothing further.
    out2 = tmp_path / 'set2.json'
    assert save_setlist(reloaded, out2)['tracks'][0]['cues'] == CUES


def test_save_without_cues_stays_byte_compatible(tmp_path):
    out = tmp_path / 'set.json'
    saved = save_setlist(_setlist_with_cues(), out)
    # The cue-less track gains no cues key (the phone form).
    assert 'cues' not in saved['tracks'][1]
    import json
    on_disk = json.loads(out.read_text(encoding='utf-8'))
    assert 'cues' not in on_disk['tracks'][1]
    assert on_disk['tracks'][0]['cues'] == CUES


def test_xml_import_builds_normalized_setlist(tmp_path):
    xml = tmp_path / 'rekordbox.xml'
    xml.write_text(XML, encoding='utf-8')
    data = setlist_from_rekordbox_tracks(import_rekordbox_xml(xml))
    first, second = data['tracks']
    assert first['title'] == 'Song X'
    assert first['bpm'] == 124.0 and first['key'] == 'Am'
    assert first['path'].endswith('music/x.wav')
    assert first['cues'] == CUES
    assert second['title'] == 'Song Y' and second['cues'] == []


def test_end_to_end_anchor_and_resave(tmp_path):
    """XML -> set list -> deck-load anchor (pass 26) -> save ->
    reload: the cues survive the whole chain."""
    xml = tmp_path / 'rekordbox.xml'
    xml.write_text(XML, encoding='utf-8')
    data = setlist_from_rekordbox_tracks(import_rekordbox_xml(xml))
    entry = data['tracks'][0]
    # A track loaded from the entry's file anchors its grid at
    # the first hot cue (32.5 s is hot cue B; A is at 0.0 — the
    # LOWEST-numbered hot cue wins, so the anchor is 0 here;
    # use the B-only subset to prove the mechanism reads the
    # entry's cues at all).
    audio = np.zeros((RATE * 4, 2), dtype=np.float32)
    track = Track('x', audio, compute_peaks(audio), 124.0, 0.9,
                  0, 1.0)
    anchor = apply_cue_anchor(track, entry['cues'])
    assert anchor == 0                       # hot cue A at 0.0 s
    b_only = [c for c in entry['cues'] if c['num'] == 1]
    track2 = Track('x', audio, compute_peaks(audio), 124.0, 0.9,
                   0, 1.0)
    assert apply_cue_anchor(track2, b_only) == int(32.5 * RATE)
    # And the set list still carries the cues after a save.
    out = tmp_path / 'round.json'
    save_setlist(data, out)
    assert load_setlist(out)['tracks'][0]['cues'] == CUES
