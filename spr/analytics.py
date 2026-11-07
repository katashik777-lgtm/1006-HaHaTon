import json
from pathlib import Path
import duckdb

FILTERS={'path':'PATH','key':'KEY','array_key':'ARRAY_KEY','diff_flag':'DIFF_FLAG','change_type':'CHANGE_TYPE','value_before':'VALUE_BEFORE','value_after':'VALUE_AFTER','app_ids':'APP_ID'}
FACT_DIMS=['PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','VALUE_ADJUSTED','DIFF_FLAG','SOURCE','PATH','CHANGE_TYPE','PRODUCT','PARTNER','ROUTING_METHOD','DECISION_CHANGED']
def where(filters, allowed):
 if not isinstance(filters,dict):raise ValueError('Filters must be an object')
 clauses=[];params=[]
 for k,v in filters.items():
  if k=='path_prefix' and 'PATH' in allowed.values():
   prefixes=v if isinstance(v,list) else [v]
   clauses.append('('+' OR '.join('starts_with("PATH", ?)' for _ in prefixes)+')' if prefixes else 'false');params.extend(prefixes);continue
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
  self.path=Path(path);self._cache={};self.con=duckdb.connect(':memory:',config={'threads':2,'memory_limit':'512MB','enable_external_access':True})
  for name in ('comparison','segments','normalized','quality','facts','membership','products','unpaired_segments','unpaired_products'):
   p=self.path/(name+'.parquet')
   if p.exists():self.con.execute('CREATE VIEW '+name+" AS SELECT * FROM read_parquet('"+str(p).replace("'","''")+"')")
  if (self.path/'comparison.duckdb').exists():
   dbpath=str(self.path/'comparison.duckdb').replace("'","''")
   self.con.execute("ATTACH '"+dbpath+"' AS indexed (READ_ONLY)")
   self.con.execute('CREATE OR REPLACE VIEW comparison AS SELECT * FROM indexed.comparison')
  # Older published artifacts stay immutable; expose comparison values via a view.
  if (self.path/'membership.parquet').exists():
   cols={r[0] for r in self.con.execute('DESCRIBE membership').fetchall()}
   if 'VALUE_BEFORE' not in cols:
    self.con.execute('ALTER VIEW membership RENAME TO membership_original')
    self.con.execute('CREATE VIEW membership AS SELECT m.*, c.VALUE_BEFORE, c.VALUE_AFTER FROM membership_original m JOIN comparison c USING(APP_ID,PATH,KEY_JSON)')
  if (self.path/'membership.parquet').exists() and (self.path/'products.parquet').exists():
   self.con.execute('ALTER VIEW membership RENAME TO membership_base')
   self.con.execute("CREATE VIEW membership AS SELECT m.*, p.PRODUCT, p.PARTNER, s.ROUTING_METHOD, s.DECISION_CHANGED FROM membership_base m LEFT JOIN (SELECT DISTINCT APP_ID,SOURCE,PRODUCT,PARTNER FROM products) p USING(APP_ID,SOURCE) LEFT JOIN segments s USING(APP_ID)")
 def __enter__(self):return self
 def __exit__(self,*args):self.close()
 def bucket_report(self):
  try:from .buckets import bucket_report
  except ImportError:from buckets import bucket_report
  if 'bucket_report' not in self._cache:self._cache['bucket_report']=bucket_report(self)
  return self._cache['bucket_report']
 def close(self):self._cache.clear();self.con.close()
 def query(self,sql,params=None):
  result=self.con.execute(sql,params or []);names=[x[0] for x in result.description]
  return [dict(zip(names,r)) for r in result.fetchall()]
 def cached_rows(self,key):
  if key in self._cache:return self._cache[key]
  filters=json.loads(key);w,p=where(filters,FILTERS)
  rows=self.query('SELECT * FROM comparison WHERE '+w,p)
  # Cache belongs to this reader, never retains closed readers or entire marts.
  if len(rows)<=1000:
   if len(self._cache)>=8:self._cache.pop(next(iter(self._cache)))
   self._cache[key]=rows
  return rows
 def rows(self,**filters):return self.cached_rows(json.dumps(filters,sort_keys=True))
 def count(self,**filters):
  w,p=where(filters,FILTERS);return self.con.execute('SELECT count(*) FROM comparison WHERE '+w,p).fetchone()[0]
 def app_ids(self,**filters):
  w,p=where(filters,FILTERS);return [r[0] for r in self.con.execute('SELECT DISTINCT APP_ID FROM comparison WHERE '+w+' ORDER BY APP_ID',p).fetchall()]
 def segments(self):return self.query('SELECT * FROM segments ORDER BY APP_ID')
 def total_apps(self):return self.con.execute('SELECT count(*) FROM segments').fetchone()[0]
 def pivot(self,dims,filters,limit=250,offset=0):
  if not isinstance(dims,list) or not dims or any(d not in FACT_DIMS for d in dims):raise ValueError('Invalid dimensions')
  w,p=where(filters,{d:d for d in FACT_DIMS});cols=','.join('"'+x+'"' for x in dims)
  return self.query(f'SELECT {cols}, count(DISTINCT APP_ID) CNT FROM membership WHERE {w} GROUP BY {cols} ORDER BY {cols} LIMIT ? OFFSET ?',p+[limit,offset])
 def drill(self,filters,limit=50,offset=0):
  w,p=where(filters,{**FILTERS,**{d:d for d in FACT_DIMS}})
  total=self.con.execute('SELECT count(DISTINCT APP_ID) FROM membership WHERE '+w,p).fetchone()[0]
  return {'total':total,'items':self.query('SELECT DISTINCT APP_ID FROM membership WHERE '+w+' ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset])}
