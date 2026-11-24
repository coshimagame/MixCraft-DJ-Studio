"""Native app integration check, runs with real output or CI silent rendering."""
import json
from pathlib import Path
from PySide6.QtCore import QTimer
from engine import RATE, load_track
from make_demo import create

def schedule(app,window,directory,hardware):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    create(directory/'demo')
    tracks=sorted((directory/'demo').glob('*.wav'))
    for i,path in enumerate(tracks): window.loaded(i,load_track(path),'')
    if not hardware: window.engine.render(512)
    window.engine.command('play',0,True)
    window.engine.command('play',1,True)
    def finish():
        if not hardware:
            for _ in range(200): window.engine.render(512)
        window.tick()
        result=dict(frames=window.engine.frame,peak=window.engine.peak,
            positions=[d['position'] for d in window.engine.decks],
            overloads=window.engine.overloads,stream_active=bool(window.stream and window.stream.active),
            latency=window.stream.latency if window.stream else None)
        window.grab().save(str(directory/'screenshot.png'))
        window.journal.drain(window.engine)
        window.journal.export(directory/'events.json')
        (directory/'smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        success=all(p>0 for p in result['positions']) and result['peak']>0 and (not hardware or result['stream_active'])
        window.close()
        app.exit(0 if success else 1)
    QTimer.singleShot(4000 if hardware else 300,finish)
