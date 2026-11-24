import json
import pytest
from recover import recover

def test_truncated_tail(tmp_path):
    src=tmp_path/'session.jsonl';out=tmp_path/'out.json'
    src.write_text('{"frame":0}\n{"frame":512}\n{"fra',encoding='utf-8')
    assert recover(src,out)==2
    assert len(json.loads(out.read_text())['events'])==2

def test_corruption_in_middle_is_reported(tmp_path):
    src=tmp_path/'session.jsonl'
    src.write_text('{"frame":0}\ninvalid\n{"frame":512}',encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):recover(src,tmp_path/'out.json')
