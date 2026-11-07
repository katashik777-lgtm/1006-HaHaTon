import json
from .runner import execute

def transform(spec,kind,data,target):
 result=execute(spec['code'],spec.get('params',{}),target,kind,data)
 with (target/'procedure-diagnostics.jsonl').open('a',encoding='utf-8') as f:
  f.write(json.dumps({'stage':kind,**{k:v for k,v in result.items() if k!='rows'}},ensure_ascii=False)+'\n')
 if result.get('error'):raise ValueError(kind+': '+result['error'])
 rows=result['rows']
 if kind=='facts' and data and any(not set(data[0])<=set(r) for r in rows):raise ValueError('Missing required columns')
 return rows
