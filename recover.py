"""Recover complete events from a JSONL journal; ignore only a truncated final line."""
import json
from pathlib import Path

def recover(source,destination):
    lines=Path(source).read_text(encoding='utf-8').splitlines()
    events=[]
    for i,line in enumerate(lines):
        try: events.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines)-1: raise
    Path(destination).write_text(json.dumps(dict(schema=1,sample_rate=48000,events=events),ensure_ascii=False,indent=2),encoding='utf-8')
    return len(events)

if __name__=='__main__':
    import sys
    print(f'Recovered {recover(sys.argv[1],sys.argv[2])} events')
