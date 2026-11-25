"""rekordbox XML cue import + Live clip-plan conversion (stdlib only).

Covers the 2026-11-25 companion feature: POSITION_MARK entries in a
rekordbox XML export are read by import_rekordbox_xml, and
cues_to_clip_plan turns Hot Cues into Session-clip plans, memory cues
into Arrangement locators and loop marks into clip loop ranges.
"""
from library import cues_to_clip_plan, import_rekordbox_xml

XML = """<DJ_PLAYLISTS><COLLECTION>
<TRACK Name="Song X" AverageBpm="124.0" Tonality="Am" Location="file://localhost/music/x.wav">
<POSITION_MARK Name="Intro" Type="0" Start="0.0" Num="0"/>
<POSITION_MARK Name="" Type="0" Start="32.5" Num="1"/>
<POSITION_MARK Name="Break" Type="0" Start="96.0" Num="-1"/>
<POSITION_MARK Name="Loop8" Type="4" Start="128.0" End="143.5" Num="2"/>
</TRACK></COLLECTION></DJ_PLAYLISTS>"""


def _track(tmp_path):
    xml = tmp_path / 'rekordbox.xml'
    xml.write_text(XML, encoding='utf-8')
    tracks = import_rekordbox_xml(xml)
    assert len(tracks) == 1
    return tracks[0]


def test_import_reads_position_marks(tmp_path):
    track = _track(tmp_path)
    assert track['bpm'] == 124.0 and track['key'] == 'Am'
    cues = track['cues']
    assert len(cues) == 4
    by_num = {c['num']: c for c in cues}
    assert by_num[0]['name'] == 'Intro' and by_num[0]['start'] == 0.0
    assert by_num[1]['start'] == 32.5
    assert by_num[-1]['name'] == 'Break'          # memory cue
    assert by_num[2]['type'] == '4'               # loop mark
    assert by_num[2]['start'] == 128.0 and by_num[2]['end'] == 143.5


def test_cues_to_clip_plan(tmp_path):
    plan = cues_to_clip_plan(_track(tmp_path))
    clips, locators = plan['clips'], plan['locators']
    assert len(clips) == 3 and len(locators) == 1  # 3 hot cues, 1 memory cue
    assert clips[0]['name'].endswith('Intro') and clips[0]['start_seconds'] == 0.0
    assert clips[1]['name'].endswith('Hot Cue B')  # unnamed hot cue gets a label
    assert clips[1]['start_seconds'] == 32.5
    assert clips[2]['loop_end_seconds'] == 143.5   # loop mark -> loop range
    assert clips[0]['loop_end_seconds'] is None
    assert locators[0]['name'].endswith('Break')
    assert locators[0]['start_seconds'] == 96.0


def test_track_without_cues(tmp_path):
    xml = tmp_path / 'rekordbox.xml'
    xml.write_text('<DJ_PLAYLISTS><COLLECTION>'
                   '<TRACK Name="Y" Location="file://localhost/music/y.wav"/>'
                   '</COLLECTION></DJ_PLAYLISTS>', encoding='utf-8')
    track = import_rekordbox_xml(xml)[0]
    assert track['cues'] == []
    assert cues_to_clip_plan(track) == {'clips': [], 'locators': []}
