"""Collect unmodified license notices from the exact installed dependencies."""
from importlib.metadata import distribution
from pathlib import Path
import json
import shutil
import sys

def collect(destination):
    root=Path(destination);root.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for name in ['PySide6-Essentials','shiboken6','numpy','scipy','sounddevice','soundfile','cffi','pycparser','typing-extensions','pyinstaller']:
        d=distribution(name)
        notices=[]
        for f in d.files or []:
            if any(word in f.name.lower() for word in ['license','licence','copying','notice']) and str(f).lower().endswith(('.txt','.md','.rst','.html','license','copying','notice')):
                source=Path(d.locate_file(f))
                if source.is_file():
                    target=root/name/str(f).replace('..','parent')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source,target)
                    notices.append(str(target.relative_to(root)))
        manifest.append(dict(name=name,version=d.version,license=d.metadata.get('License-Expression') or d.metadata.get('License'),files=notices))
    shutil.copyfile(Path(sys.base_prefix)/'LICENSE.txt',root/'PYTHON-LICENSE.txt')
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

if __name__=='__main__': collect(sys.argv[1])
