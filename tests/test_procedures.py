import pytest
from spr.runner import execute
from spr.pipeline import process

def test_custom_pipeline(tmp_path,monkeypatch):
 monkeypatch.setenv('SPR_TRUSTED_LOCAL_CODE','1')
 files=[]
 for source in ('BEFORE','AFTER'):
  p=tmp_path/(source+'.json');p.write_text('{"score": 1}')
  files.append(dict(path=str(p),name='a.json',source=source,format='json'))
 identity={'code':'def run(data, params):\n return data','params':{}}
 parse={'code':'import json\ndef run(data, params):\n return json.loads(data["text"])','params':{}}
 stages=dict(parse=parse,normalize={'code':'from normalize import normalize\ndef run(d,p): return normalize(d["document"],d["app_id"],d["source"],d["profile"])'},compare={'code':'from normalize import frame,compare\ndef run(d,p): return compare(frame(d["rows"]),d["valid_ids"]).to_dicts()'},facts=identity)
 result=process(files,{},tmp_path/'out',procedures=stages)
 assert result['valid_pairs']==1
 assert result['changes']==0
 assert (tmp_path/'out'/'procedure-diagnostics.jsonl').exists()

def test_invalid_contract(tmp_path,monkeypatch):
 monkeypatch.setenv('SPR_TRUSTED_LOCAL_CODE','1')
 result=execute('def run(data, params): return 12',{},tmp_path,'parse',{})
 assert 'error' in result
