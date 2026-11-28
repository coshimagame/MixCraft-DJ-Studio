"""Pass 31 — the return path: a Live set comes back as a
set list (user vision 2026-11-28: MixAble ⇄ Live mutual
exchange; pass 30 built MixAble → Live).

What is pinned here:
  - Session first: clips in the first two AudioTracks'
    ClipSlotLists, interleaved by slot (slot i: deck A then
    deck B) — the DJ order a pass-30 export lays down. Deck
    is the track (1 = A, 2 = B), carried on each row.
  - Session empty on both tracks -> the Arrangement
    (Sample/ArrangerAutomation, clips sorted by their Time)
    is the fallback; when both exist and disagree, Session
    wins.
  - File references resolve honestly: absolute <Path>, then
    the <Data> blob (UTF-16LE absolute path), then a relative
    reference that actually lands on a file next to the .als.
    A song with none of those still appears as a row (never
    silently dropped) and is counted in no_file.
  - SET BPM comes from the master Tempo; per-song BPM from
    the warp markers; duration from the file / DefaultDuration
    / warp end, in that order.
  - The app opens an .als through the same set-list door as
    a JSON: rows keep their paths (double-click loads work)
    and their real deck letters, and the status line states
    the read count and the file-less count.
  - THE round trip: the user's real journal -> pass-30
    export (base = his real pass-27 export file) -> import
    -> Heya (A) then Signals (B), paths resolvable, SET
    BPM 125. Skipped where his files are not on disk (CI).
"""
import gzip
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

import als_import
import als_writer


# ---------------------------------------------------------------------------
# Fixture builder (the genuine Live 10 shapes, as in test_pass30)
# ---------------------------------------------------------------------------

def _clip(clip_id, name, path=None, seconds=300.0, bpm=120.0,
          time=None, data_blob=None, rel_parts=None):
    ref = '<RelativePathType Value="3"/><RelativePath Value=""/>'
    if rel_parts:
        ref = ('<RelativePathType Value="1"/><RelativePath>'
               + ''.join(f'<RelativePathElement Value="{p}"/>'
                         for p in rel_parts)
               + '</RelativePath>')
    ref += f'<Path Value="{path or ""}"/>'
    if data_blob is not None:
        ref += f'<Data>{data_blob}</Data>'
    ref += ('<LivePackName Value=""/><LivePackId Value=""/>'
            '<OriginalFileSize Value="111"/><OriginalCrc Value="0"/>')
    frames = int(seconds * 44100)
    beats = seconds * bpm / 60.0
    time_attr = f' Time="{time}"' if time is not None else ''
    return (
        f'<AudioClip Id="{clip_id}"{time_attr}>'
        '<LomId Value="0"/><CurrentStart Value="0"/>'
        f'<CurrentEnd Value="{beats}"/>'
        f'<Name Value="{name}"/><IsWarped Value="true"/>'
        '<SampleRef><FileRef>' + ref + '</FileRef>'
        '<LastModDate Value="333"/>'
        f'<DefaultDuration Value="{frames}"/>'
        '<DefaultSampleRate Value="44100"/></SampleRef>'
        '<WarpMode Value="0"/><WarpMarkers>'
        '<WarpMarker Id="0" SecTime="0" BeatTime="0"/>'
        f'<WarpMarker Id="1" SecTime="{seconds}" '
        f'BeatTime="{beats}"/></WarpMarkers>'
        '<Envelopes/></AudioClip>')


def _track(name, session_clips, arrangement_clips=()):
    slots = ''
    for i in range(max(2, len(session_clips))):
        clip = session_clips[i] if i < len(session_clips) else None
        slots += ('<ClipSlot><ClipSlot><Value>'
                  + (clip or '') + '</Value></ClipSlot></ClipSlot>')
    events = ''.join(arrangement_clips)
    return (f'<AudioTrack><Name><EffectiveName Value="{name}"/>'
            '</Name><DeviceChain><MainSequencer><ClipSlotList>'
            + slots + '</ClipSlotList>'
            '<Sample><ArrangerAutomation><Events>' + events +
            '</Events><Envelopes/></ArrangerAutomation></Sample>'
            '</MainSequencer></DeviceChain></AudioTrack>')


def _write_als(tmp_path, tracks_xml, tempo=125.0, name='set.als'):
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<Ableton MajorVersion="5" MinorVersion="10.0_377" '
           'Creator="Ableton Live 10.1.43"><LiveSet><Tracks>'
           + tracks_xml +
           '</Tracks><MasterTrack><DeviceChain><Mixer>'
           f'<Tempo><Manual Value="{tempo}"/></Tempo>'
           '</Mixer></DeviceChain></MasterTrack>'
           '</LiveSet></Ableton>')
    path = tmp_path / name
    path.write_bytes(gzip.compress(xml.encode('utf-8')))
    return path


def _two_track_set(tmp_path, a_session, b_session,
                   a_arr=(), b_arr=(), tempo=125.0):
    return _write_als(
        tmp_path,
        _track('1-A', a_session, a_arr) + _track('2-B', b_session,
                                                 b_arr),
        tempo=tempo)


# ---------------------------------------------------------------------------
# Session reading
# ---------------------------------------------------------------------------

def test_session_interleaves_by_slot_with_track_decks(tmp_path):
    als = _two_track_set(
        tmp_path,
        a_session=[_clip(1, 'Alpha Artist - Alpha Song',
                         'C:\\music\\alpha.wav'),
                   _clip(2, 'Gamma Artist - Gamma Song',
                         'C:\\music\\gamma.wav')],
        b_session=[_clip(3, 'Beta Artist - Beta Song',
                         'C:\\music\\beta.wav')])
    result = als_import.read_als_set(als)
    assert result['source'] == 'session'
    assert result['count'] == 3
    assert result['no_file'] == 0
    assert result['set_bpm'] == 125.0
    rows = result['setlist']['tracks']
    # Slot-major interleave: A0, B0, A1 — the DJ order.
    assert [(r['title'], r['deck']) for r in rows] == [
        ('Alpha Song', 0), ('Beta Song', 1), ('Gamma Song', 0)]
    assert rows[0]['artist'] == 'Alpha Artist'
    assert rows[0]['path'] == 'C:\\music\\alpha.wav'
    # Duration from DefaultDuration (300 s @ 44.1 kHz) and
    # BPM from the warp markers' slope (120).
    assert rows[0]['duration_seconds'] == 300
    assert rows[0]['bpm'] == 120.0
    # The setlist dict is the app's own shape: set_bpm landed.
    assert result['setlist']['set_bpm'] == 125.0


def test_session_wins_when_arrangement_disagrees(tmp_path):
    als = _two_track_set(
        tmp_path,
        a_session=[_clip(1, 'Session Artist - Session Song',
                         'C:\\music\\session.wav')],
        b_session=[],
        a_arr=[_clip(2, 'Arr Artist - Arr Song',
                     'C:\\music\\arr.wav', time=0)],
        b_arr=[_clip(3, 'Arr Artist - Other Arr Song',
                     'C:\\music\\arr2.wav', time=10)])
    result = als_import.read_als_set(als)
    assert result['source'] == 'session'
    assert [r['title'] for r in result['setlist']['tracks']] == [
        'Session Song']


def test_arrangement_fallback_sorted_by_time(tmp_path):
    als = _two_track_set(
        tmp_path,
        a_session=[], b_session=[],
        a_arr=[_clip(1, 'A Artist - Late A', 'C:\\m\\la.wav',
                     time=64),
               _clip(2, 'A Artist - Early A', 'C:\\m\\ea.wav',
                     time=0)],
        b_arr=[_clip(3, 'B Artist - Mid B', 'C:\\m\\mb.wav',
                     time=32)])
    result = als_import.read_als_set(als)
    assert result['source'] == 'arrangement'
    rows = result['setlist']['tracks']
    assert [(r['title'], r['deck']) for r in rows] == [
        ('Early A', 0), ('Mid B', 1), ('Late A', 0)]


# ---------------------------------------------------------------------------
# File reference resolution
# ---------------------------------------------------------------------------

def test_pathless_song_stays_a_row_and_is_counted(tmp_path):
    als = _two_track_set(
        tmp_path,
        a_session=[_clip(1, 'Lost Artist - Lost Song', None)],
        b_session=[_clip(2, 'Kept Artist - Kept Song',
                         'C:\\music\\kept.wav')])
    result = als_import.read_als_set(als)
    assert result['count'] == 2
    assert result['no_file'] == 1
    rows = result['setlist']['tracks']
    assert rows[0]['title'] == 'Lost Song'
    assert rows[0]['path'] is None
    assert rows[1]['path'] == 'C:\\music\\kept.wav'


def test_data_blob_recovers_the_path(tmp_path):
    blob = ('D:\\tunes\\Blob Song.wav' + '\0') \
        .encode('utf-16-le').hex().upper()
    als = _two_track_set(
        tmp_path,
        a_session=[_clip(1, 'Blob Artist - Blob Song', None,
                         data_blob=blob)],
        b_session=[])
    result = als_import.read_als_set(als)
    assert result['no_file'] == 0
    assert result['setlist']['tracks'][0]['path'] == \
        'D:\\tunes\\Blob Song.wav'


def test_relative_reference_resolves_next_to_the_set(tmp_path):
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'Rel Song.wav').write_bytes(b'RIFF')
    als = _two_track_set(
        tmp_path,
        a_session=[_clip(1, 'Rel Artist - Rel Song', None,
                         rel_parts=['sub', 'Rel Song.wav'])],
        b_session=[])
    result = als_import.read_als_set(als)
    row = result['setlist']['tracks'][0]
    assert row['path'] is not None
    assert row['path'].endswith('Rel Song.wav')
    assert result['no_file'] == 0


def test_empty_set_is_refused_plainly(tmp_path):
    als = _two_track_set(tmp_path, a_session=[], b_session=[])
    with pytest.raises(als_import.AlsImportError):
        als_import.read_als_set(als)


def test_non_als_file_is_refused(tmp_path):
    bogus = tmp_path / 'nope.als'
    bogus.write_text('this is not a set', encoding='utf-8')
    with pytest.raises(als_writer.AlsError):
        als_import.read_als_set(bogus)


# ---------------------------------------------------------------------------
# The round trip: his real journal -> pass-30 export -> import
# ---------------------------------------------------------------------------

_USER_FILES = Path(__file__).resolve().parents[2] / 'user' / 'files'
_HEYA = r'C:\Users\wa-ma\Desktop\260828bandcamp' \
        r'\Romain Garcia - Heya (Extended Mix).wav'
_SIGNALS = r'C:\Users\wa-ma\Desktop\260828bandcamp' \
           r'\Hidden Empire - Signals.wav'


@pytest.mark.skipif(
    not (_USER_FILES / 'mixcraft-performance-events.json').is_file()
    or not (_USER_FILES
            / 'mixcraft-performance-75ef3791.als').is_file(),
    reason="the user's real journal / base are not on this machine")
def test_round_trip_real_journal_export_import(tmp_path):
    import perf_als
    out = tmp_path / 'roundtrip.als'
    perf_als.generate(
        _USER_FILES / 'mixcraft-performance-75ef3791.als',
        _USER_FILES / 'mixcraft-performance-events.json',
        out, set_bpm=125.0,
        durations={_HEYA: 324.1, _SIGNALS: 379.3},
        bpms={_HEYA: 121.97, _SIGNALS: 122.0})
    result = als_import.read_als_set(out)
    assert result['source'] == 'session'
    assert result['set_bpm'] == 125.0
    assert result['count'] == 2
    assert result['no_file'] == 0
    heya, signals = result['setlist']['tracks']
    assert (heya['deck'], heya['artist'], heya['title']) == \
        (0, 'Romain Garcia', 'Heya (Extended Mix)')
    assert heya['path'] == _HEYA
    assert heya['bpm'] == pytest.approx(121.97, abs=0.1)
    assert heya['duration_seconds'] == pytest.approx(324, abs=2)
    assert (signals['deck'], signals['artist'],
            signals['title']) == (1, 'Hidden Empire', 'Signals')
    assert signals['path'] == _SIGNALS


# ---------------------------------------------------------------------------
# App integration: the .als opens through the set-list door
# ---------------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QApplication, QLabel
    import app as appmod
    HAVE_QT = True
except Exception:
    HAVE_QT = False


@pytest.fixture()
def window():
    if not HAVE_QT:
        pytest.skip('PySide6 is not available')
    qapp = QApplication.instance() or QApplication([])
    try:
        win = appmod.Window(audio=False)
    except Exception as exc:
        pytest.skip(f'Window cannot be built here: {exc}')
    yield win
    win.close()
    qapp.processEvents()


def _deck_chips(win):
    """The rendered rows' deck-letter chips, in row order."""
    chips = []
    for i in range(win.setlist_rows.count()):
        container = win.setlist_rows.itemAt(i).widget()
        for j in range(container.layout().count()):
            widget = container.layout().itemAt(j).widget()
            if isinstance(widget, QLabel) and widget.width() == 22:
                chips.append(widget.text())
    return chips


def test_app_opens_als_as_setlist(window, tmp_path, monkeypatch):
    # A slots: one song. B slots: two songs -> rows A, B, B.
    # The third row's chip proves the entry's real deck wins
    # over the plan alternation (which would paint A).
    als = _two_track_set(
        tmp_path,
        a_session=[_clip(1, 'Alpha Artist - Alpha Song',
                         'C:\\music\\alpha.wav')],
        b_session=[_clip(2, 'Beta Artist - Beta Song',
                         'C:\\music\\beta.wav'),
                   _clip(3, 'Lost Artist - Lost Song', None)])
    monkeypatch.setattr(
        appmod.QFileDialog, 'getOpenFileName',
        staticmethod(lambda *a, **k: (str(als), '')))
    window.open_setlist()
    data = window.setlist_data
    assert data is not None
    rows = data['tracks']
    assert [(r['title'], r['deck']) for r in rows] == [
        ('Alpha Song', 0), ('Beta Song', 1), ('Lost Song', 1)]
    assert rows[0]['path'] == 'C:\\music\\alpha.wav'
    assert rows[2]['path'] is None
    assert data['set_bpm'] == 125.0
    # The clip-conversion view keeps the paths (row loads work).
    assert window.clip_tracks[0]['path'] == 'C:\\music\\alpha.wav'
    # The status line states the file-less count plainly.
    assert 'ファイルなし 1 曲' in window._status_fn('jp')
    assert _deck_chips(window) == ['A', 'B', 'B']
