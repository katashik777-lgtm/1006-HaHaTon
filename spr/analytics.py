import json
from pathlib import Path
from functools import lru_cache
import duckdb

FILTERS={'path':'PATH','key':'KEY','array_key':'ARRAY_KEY','diff_flag':'DIFF_FLAG','change_type':'CHANGE_TYPE','value_before':'VALUE_BEFORE','value_after':'VALUE_AFTER','app_ids':'APP_ID'}
FACT_DIMS=['PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','VALUE_ADJUSTED','DIFF_FLAG','SOURCE','PATH','CHANGE_TYPE']
def where(filters, allowed):
 clauses=[];params=[]
 for k,v in filters.items():
  if k=='path_prefix':clauses.append('starts_with("PATH", ?)');params.append(v);continue
  c=allowed.get(k,k)
  if c not in allowed.values():raise ValueError('Unsupported filter: '+k)
  if isinstance(v,list):
   if not v:clauses.append('false')
   else:clauses.append('"'+c+'" IN ('+','.join('?' for _ in v)+')');params.extend(v)
  elif v is None:clauses.append('"'+c+'" IS NULL')
  else:clauses.append('"'+c+'" = ?');params.append(v)
 return (' AND '.join(clauses) or 'true'),params
class Mart:
 def __init__(self,path):
  self.path=Path(path);self.con=duckdb.connect(':memory:',config={'threads':2,'memory_limit':'512MB','enable_external_access':True})
  for name in ('comparison','segments','normalized','quality','facts','membership','products'):
   p=self.path/(name+'.parquet')
   if p.exists():self.con.execute('CREATE VIEW '+name+" AS SELECT * FROM read_parquet('"+str(p).replace("'","''")+"')")
 def close(self):self.con.close()
 def query(self,sql,params=None):
  result=self.con.execute(sql,params or []);names=[x[0] for x in result.description]
  return [dict(zip(names,r)) for r in result.fetchall()]
 @lru_cache(maxsize=64)
 def cached_rows(self,key):
  filters=json.loads(key);w,p=where(filters,FILTERS)
  return self.query('SELECT * FROM comparison WHERE '+w,p)
 def rows(self,**filters):return self.cached_rows(json.dumps(filters,sort_keys=True))
 def count(self,**filters):
  w,p=where(filters,FILTERS);return self.con.execute('SELECT count(*) FROM comparison WHERE '+w,p).fetchone()[0]
 def app_ids(self,**filters):
  w,p=where(filters,FILTERS);return [r[0] for r in self.con.execute('SELECT DISTINCT APP_ID FROM comparison WHERE '+w+' ORDER BY APP_ID',p).fetchall()]
 def segments(self):return self.query('SELECT * FROM segments ORDER BY APP_ID')
 def total_apps(self):return self.con.execute('SELECT count(*) FROM segments').fetchone()[0]
 def pivot(self,dims,filters,limit=250,offset=0):
  if not dims or any(d not in FACT_DIMS for d in dims):raise ValueError('Invalid dimensions')
  w,p=where(filters,{d:d for d in FACT_DIMS});cols=','.join('"'+x+'"' for x in dims)
  return self.query(f'SELECT {cols}, count(DISTINCT APP_ID) CNT FROM membership WHERE {w} GROUP BY {cols} ORDER BY {cols} LIMIT ? OFFSET ?',p+[limit,offset])
 def drill(self,filters,limit=50,offset=0):
  w,p=where(filters,{**FILTERS,**{d:d for d in FACT_DIMS}})
  total=self.con.execute('SELECT count(DISTINCT APP_ID) FROM membership WHERE '+w,p).fetchone()[0]
  return {'total':total,'items':self.query('SELECT DISTINCT APP_ID FROM membership WHERE '+w+' ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset])}
