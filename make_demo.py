"""Generate original synthetic test audio; no copyrighted music is included."""
from pathlib import Path
import numpy as np
import soundfile as sf
from engine import RATE

def create(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    for name,bpm,freq in [('A - Tidal Circuit',120,110),('B - Violet Horizon',125,146.83)]:
        t=np.arange(RATE*24)/RATE
        phase=t%(60/bpm)
        kick=np.sin(2*np.pi*(48*phase+2*(1-np.exp(-phase*35))))*np.exp(-phase*20)
        pad=(np.sin(2*np.pi*freq*t)+.4*np.sin(2*np.pi*freq*1.5*t))*.10
        a=(kick*.36+pad)*np.minimum(1,t*2)*np.minimum(1,(24-t)*2)
        sf.write(directory/(name+'.wav'),np.column_stack([a,a]),RATE,subtype='PCM_16')

if __name__=='__main__':
    import sys
    create(sys.argv[1] if len(sys.argv)>1 else 'demo')
