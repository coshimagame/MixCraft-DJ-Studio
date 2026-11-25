"""Track library, playlists and rekordbox XML import (Phase 2).

Original audio files are only ever read.  The library stores paths and
analysis metadata in SQLite next to the app data, never inside the tracks.
"""
import json, sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

class Library:
    def __init__(self, db_path):
        self.db = sqlite3.connect(str(db_path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS tracks(
            path TEXT PRIMARY KEY, title TEXT, bpm REAL, key_name TEXT, camelot TEXT)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS playlists(name TEXT, position INTEGER, path TEXT)")
        self.db.commit()
    def add(self, path, title=None, bpm=None, key_name=None, camelot=None):
        self.db.execute("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?)",
                        (str(Path(path).resolve()), title or Path(path).stem, bpm, key_name, camelot))
        self.db.commit()
    def all(self):
        return [dict(path=r[0], title=r[1], bpm=r[2], key=r[3], camelot=r[4])
                for r in self.db.execute("SELECT * FROM tracks ORDER BY title")]
    def search(self, text):
        return [t for t in self.all() if text.lower() in (t['title'] or '').lower()]
    def set_playlist(self, name, paths):
        self.db.execute("DELETE FROM playlists WHERE name=?", (name,))
        self.db.executemany("INSERT INTO playlists VALUES (?,?,?)",
                            [(name, i, str(Path(p).resolve())) for i, p in enumerate(paths)])
        self.db.commit()
    def playlist(self, name):
        return [r[0] for r in self.db.execute(
            "SELECT path FROM playlists WHERE name=? ORDER BY position", (name,))]
    def close(self): self.db.close()

def import_rekordbox_xml(path):
    """Read a rekordbox export XML. Returns TrackInfo-ready dicts (read-only)."""
    root = ET.parse(path).getroot()
    out = []
    for node in root.iter('TRACK'):
        loc = node.get('Location', '')
        if loc.startswith('file://localhost/'): loc = loc[len('file://localhost/'):]
        elif loc.startswith('file://'): loc = loc[len('file://'):]
        from urllib.parse import unquote
        cues = []
        for mark in node.iter('POSITION_MARK'):
            cues.append(dict(name=mark.get('Name') or '',
                             type=mark.get('Type'),            # 0=cue, 4=loop (rekordbox)
                             start=float(mark.get('Start')) if mark.get('Start') else 0.0,
                             end=float(mark.get('End')) if mark.get('End') else None,
                             num=int(mark.get('Num')) if mark.get('Num') is not None else -1))
            # Num: -1 = memory cue, 0.. = hot cue A, B, C...
        out.append(dict(path=unquote(loc), title=node.get('Name'),
                        bpm=float(node.get('AverageBpm')) if node.get('AverageBpm') else None,
                        key=node.get('Tonality'), cues=cues))
    return [t for t in out if t['path']]

def cues_to_clip_plan(track):
    """Convert rekordbox cues into a Live clip plan (user decision 2026-11-25).

    Live has no hot-cue concept, so each hot cue becomes its own Session clip
    that starts at the cue point; memory cues become Arrangement locators.
    Returns dict(clips=[...], locators=[...]) with times in seconds.
    """
    clips, locators = [], []
    for cue in track.get('cues', []):
        label = cue['name'] or (f"Hot Cue {'ABCDEFGH'[cue['num']]}" if cue['num'] >= 0 else 'Memory Cue')
        entry = dict(name=f"{track.get('title') or ''} – {label}".strip(' –'),
                     start_seconds=cue['start'], loop_end_seconds=cue['end'] if cue['type'] == '4' else None)
        (clips if cue['num'] >= 0 else locators).append(entry)
    return dict(clips=clips, locators=locators)

def export_playlist_json(library, name, path):
    Path(path).write_text(json.dumps(library.playlist(name), ensure_ascii=False, indent=2), encoding='utf-8')

def export_bundle(events_path, tracks, out_dir):
    """Ableton-alternative export (Phase 5 fallback, ALS互換は未確認).

    Writes arrangement.json (clip placement, BPM, markers), markers.csv and
    copies the event log — everything Live needs to rebuild the set by hand,
    without claiming the generated data is a native .als project.
    """
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    events = json.loads(Path(events_path).read_text(encoding='utf-8')) if Path(events_path).exists() else {'events': []}
    arrangement = dict(schema=1, sample_rate=48000, tracks=tracks, events=events.get('events', events))
    (out/'arrangement.json').write_text(json.dumps(arrangement, ensure_ascii=False, indent=2), encoding='utf-8')
    markers = [e for e in arrangement['events'] if e.get('action') in ('load', 'preview', 'play')]
    (out/'markers.csv').write_text('frame,action,deck,value\n' + ''.join(
        f"{m.get('frame')},{m.get('action')},{m.get('deck')},{m.get('value')}\n" for m in markers), encoding='utf-8')
    return out
