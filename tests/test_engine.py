import hashlib
import json
import numpy as np
import pytest
import soundfile as sf
from engine import Engine, Journal, RATE, Track, load_track, estimate_bpm

def track(value=0.3, frames=48000):
    return Track('test.wav', np.full((frames,2),value,dtype='float32'),np.ones(10),120,1)

def prepared():
    e=Engine()
    for i in range(2):
        e.command('load',i,track(0.2 if i==0 else 0.4))
        e.command('volume',i,1)
        e.command('play',i,True)
    e.command('master',value=1)
    return e

def test_crossfader_endpoints_and_center():
    e=prepared()
    for x,expected in [(0,0.2),(1,0.4),(0.5,0.6/np.sqrt(2))]:
        e.command('cross',value=x)
        assert np.allclose(e.render(128),expected)

def test_pause_seek_eof():
    e=prepared()
    e.command('play',0,False); e.command('play',1,False)
    assert not e.render(64).any()
    e.command('seek',0,47990);e.command('play',0,True)
    e.render(64)
    assert e.decks[0]['position']==48000
    assert not e.decks[0]['playing']
    e.command('play',0,True);e.render(64)
    assert e.decks[0]['position']==64

def test_clip_guard():
    e=prepared()
    for i in range(2): e.command('load',i,track(5));e.command('play',i,True)
    out=e.render(512)
    assert np.max(np.abs(out)) <= np.float32(.98)
    assert e.overloads == 1

def test_preview_restores_positions_and_state():
    e=prepared(); e.render(100)
    e.command('play',1,False);e.render(50)
    before=[(d['position'],d['playing']) for d in e.decks]
    e.command('preview',value=.01)
    e.render(512)
    assert e.preview is None
    assert [(d['position'],d['playing']) for d in e.decks]==before

def test_preview_cancel_and_repeat():
    e=prepared();e.render(100)
    e.command('preview',value=.5);e.render(100)
    e.command('preview',value=.5);e.render(100)
    e.command('stop_preview');e.command('play',0,False);e.command('play',1,False);e.render(10)
    assert all(d['position']==100 for d in e.decks)

@pytest.mark.parametrize('format,ext,subtype',[('WAV','wav','PCM_16'),('FLAC','flac','PCM_16'),('AIFF','aiff','PCM_16'),('MP3','mp3',None)])
def test_decode_resample_and_preserve(tmp_path,format,ext,subtype):
    path=tmp_path/f'a.{ext}'
    sf.write(path,np.sin(np.arange(44100)*.02)*.3,44100,format=format,subtype=subtype)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    t=load_track(path)
    assert t.audio.shape[1]==2
    assert abs(len(t.audio)-RATE)<2000 # MP3 padding
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before

def test_bpm_clicks_and_silence():
    a=np.zeros((RATE*12,2),dtype='float32')
    for i in range(0,len(a),RATE//2): a[i:i+480]=.5
    bpm,confidence=estimate_bpm(a)
    assert abs(bpm-120)<1
    assert confidence>.6
    assert estimate_bpm(np.zeros_like(a))[0] is None

def test_events_are_block_timestamped_and_json(tmp_path):
    e=prepared();e.render(512)
    e.command('cross',value=.2);e.render(512)
    j=Journal(tmp_path/'session.jsonl');j.drain(e);j.export(tmp_path/'events.json');j.file.close()
    events=json.loads((tmp_path/'events.json').read_text())['events']
    assert events[-1]['frame']==512
    assert events[-1]['value']==.2
    assert len((tmp_path/'session.jsonl').read_text().splitlines())==len(events)

def test_invalid_file(tmp_path):
    p=tmp_path/'bad.wav';p.write_bytes(b'not audio')
    with pytest.raises(Exception):load_track(p)
