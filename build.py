"""Build with a controlled PATH so unrelated application DLLs cannot leak in."""
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parent
env=os.environ.copy()
if sys.platform=='win32':
    windows=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    env['PATH']=os.pathsep.join([str(Path(sys.executable).parent),str(windows/'System32'),str(windows)])
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
    '--exclude-module','pytest','--name','MixCraftDJStudio','app.py',*sys.argv[1:]],cwd=root,env=env,check=True)
