import numpy as np
import pytest
from engine import Engine, Track, RATE, _smooth

def playing(audio):
    e=Engine()
    # Pass 35: these are pass-2 regression tests — AUTO SYNC
    # (default on since pass 35) would tempo-match a second
    # deck the moment it starts, which is not what any test
    # here is about. Off restores the world they were written in.
    e.command('auto_sync',None,False)
    e.command('load',0,Track('test.wav',np.column_stack([audio,audio]).astype('float32'),np.ones(4),120,1))
    e.command('cross',value=0);e.command('master',value=1);e.command('volume',0,1)
    e.command('play',0,True);e.render(512)
    return e

def test_loop_no_zero_padding_inside_block():
    # Pass 33: this pins the RESAMPLE read path's loop index
    # mapping sample-for-sample. Key lock (default on since
    # pass 33) is a different read stage — its loop behaviour is
    # covered in tests/test_pass33_key_lock.py — so it is off here.
    e=playing(np.full(5000,.2))
    e.command('key_lock',0,False)
    e.command('seek',0,100);e.command('loop_in',0)
    e.command('seek',0,200);e.command('loop_out',0)
    e.command('seek',0,180)
    assert np.allclose(e.render(512),.2)
    assert e.decks[0]['position']==192

@pytest.mark.parametrize('rate',[.5,1.25,2.0])
def test_rate_sampler_matches_continuous_reference(rate):
    # Pass 33: the sampler under test IS the resample path; key
    # lock (default on since pass 33) replaces it with the WSOLA
    # stage, so it is switched off to keep testing the sampler
    # itself. Assertions unchanged.
    audio=np.arange(10000)/100000
    e=playing(audio)
    e.command('key_lock',0,False)
    e.command('seek',0,100);e.command('tempo',0,rate)
    result=np.concatenate([e.render(137),e.render(211)])[:,0]
    assert np.allclose(result,(100+rate*np.arange(348))/100000,atol=1e-7)
    assert e.decks[0]['position']==100+348*rate

def test_short_slow_tail_does_not_drop_other_deck():
    e=playing(np.full(600,.2))
    e.command('seek',0,599);e.command('tempo',0,.5)
    e.command('load',1,Track('b.wav',np.full((1000,2),.2,dtype='float32'),np.ones(4),120,1))
    e.command('play',1,True);e.command('cross',value=1)
    out=e.render(512)
    assert np.any(out[300:])
    assert e.decks[1]['position']==512

def test_ramp_partition_independence():
    a={'v':1.0};b={'v':1.0}
    full=_smooth(a,'v',0,512)
    split=np.concatenate([_smooth(b,'v',0,n) for n in [64,64,64,64,256]])
    assert np.allclose(full,split)
    assert b['v']==0

def test_eq_gain_changes_are_ramped():
    e=playing(np.full(20000,.2))
    # Warm low-band state before measuring the jump at a new command.
    e.command('eq_low',0,.49)
    for _ in range(10):e.render(512)
    before=e.render(512)[-1,0]
    e.command('eq_low',0,0)
    out=e.render(512)[:,0]
    assert abs(out[0]-before)<.002
    assert np.max(np.abs(np.diff(out)))<.002

def test_highpass_cutoff_updates_after_first_move():
    audio=.2*np.sin(2*np.pi*500*np.arange(RATE)/RATE)
    e=playing(audio)
    e.command('filter',0,.6)
    for _ in range(20):low=e.render(512)
    e.command('filter',0,1)
    for _ in range(20):high=e.render(512)
    assert np.sqrt(np.mean(high**2))<np.sqrt(np.mean(low**2))*.3

def test_quantize_never_seeks_outside_file():
    e=playing(np.full(1000,.2))
    e.command('play',0,False);e.command('beat_offset',0,12000)
    e.command('quantize',0,True);e.command('seek',0,900)
    e.render(32)
    assert 0<=e.decks[0]['position']<=1000

def test_preview_temporarily_disables_loop_rate_and_restores():
    e=playing(np.full(RATE,.2))
    e.command('load',1,Track('b.wav',np.full((RATE,2),.2,dtype='float32'),np.ones(4),120,1))
    e.command('seek',0,100);e.command('loop_in',0)
    e.command('seek',0,200);e.command('loop_out',0)
    e.command('tempo',0,1.25);e.render(1)
    e.command('preview',value=.05);e.render(512)
    assert not e.decks[0]['loop_active'] and e.decks[0]['rate']==1
    e.command('stop_preview');e.render(1)
    assert e.decks[0]['loop_active'] and e.decks[0]['rate']==1.25
