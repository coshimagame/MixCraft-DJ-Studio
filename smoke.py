"""Native app integration check, runs with real output or CI silent rendering."""
import json
from pathlib import Path
from PySide6.QtCore import QTimer
from engine import RATE, load_track
from make_demo import create

def _audio_inventory():
    """Pass 23 evidence: which host APIs the (possibly packaged)
    PortAudio build exposes, and which output devices it sees —
    this is how we learn whether ASIO exists in the shipped
    binary at all. Best-effort: any failure records the error."""
    try:
        import sounddevice as sd
        hosts=sd.query_hostapis()
        return dict(hostapis=[h['name'] for h in hosts],
            outputs=[dict(name=d['name'],hostapi=hosts[d['hostapi']]['name'],
                channels=d['max_output_channels'])
                for d in sd.query_devices() if d['max_output_channels']>=1])
    except Exception as e:
        return dict(error=str(e))

def schedule(app,window,directory,hardware):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    create(directory/'demo')
    tracks=sorted((directory/'demo').glob('*.wav'))
    for i,path in enumerate(tracks): window.loaded(i,load_track(path),'')
    # Exercise the real slider signal wiring, not just neutral bypass DSP.
    window.decks[0].controls['eq_low'].setValue(40)
    window.decks[0].controls['filter'].setValue(65)
    window.decks[1].controls['eq_high'].setValue(40)
    window.decks[1].controls['filter'].setValue(35)
    if hardware: window.start_audio()
    if not hardware: window.engine.render(512)
    window.engine.command('play',0,True)
    window.engine.command('play',1,True)
    def finish():
        if not hardware:
            for _ in range(200): window.engine.render(512)
        window.tick()
        result=dict(audio_inventory=_audio_inventory(),
            audio_device=getattr(window,'audio_device_name',''),
            midi_available=bool(getattr(window,'midi',None) and window.midi.available),
            frames=window.engine.frame,peak=window.engine.peak,
            positions=[d['position'] for d in window.engine.decks],
            overloads=window.engine.overloads,stream_active=bool(window.stream and window.stream.active),
            latency=window.stream.latency if window.stream else None,
            hostapi=window.hostapi_name, audio_status_count=window.audio_status_count,
            callback_mean_ms=1000*window.callback_seconds/max(1,window.callback_count),
            callback_max_ms=1000*window.callback_max_seconds,
            block_budget_ms=512/RATE*1000,
            scope='synthetic audio, output API latency; not physical end-to-end latency')
        window.grab().save(str(directory/'screenshot.png'))
        window.journal.drain(window.engine)
        window.journal.export(directory/'events.json')
        (directory/'smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        success=all(p>0 for p in result['positions']) and result['peak']>0 and (not hardware or result['stream_active'])
        window.close()
        app.exit(0 if success else 1)
    QTimer.singleShot(4000 if hardware else 300,finish)
