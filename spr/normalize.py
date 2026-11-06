"""Profile-driven canonical normalization. Input documents are never executed."""
import csv, io, math
from urllib.parse import quote
import orjson
import polars as pl
from defusedxml import ElementTree as ET
COLS=['APP_ID','SOURCE','PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','VALUE','ADJUSTED_VALUE','PATH','TYPE','ADDRESS','KEY_JSON','PRESENT']
SCHEMA={x:pl.String for x in COLS}
def get(obj,path,default=None):
 for part in path.split('.') if path else []:
  if not isinstance(obj,dict) or part not in obj:return default
  obj=obj[part]
 return obj
def assign(obj,path,value):
 parts=path.split('.')
 for p in parts[:-1]:obj=obj.setdefault(p,{})
 obj[parts[-1]]=value

def scalar(v):
 if v is None:return 'NULL','null'
 if isinstance(v,bool):return str(v).lower(),'bool'
 if isinstance(v,str):return v,'str'
 if isinstance(v,(int,float)):
  if not math.isfinite(v):raise ValueError('Non-finite number')
  return str(v),'number'
 return orjson.dumps(v,option=orjson.OPT_SORT_KEYS).decode(),'container'

def parse(data,fmt,profile):
 if fmt=='json':obj=orjson.loads(data)
 elif fmt=='xml':
  def node(e):
   children=list(e)
   if not children and not e.attrib:return e.text or ''
   d={'@'+k:v for k,v in e.attrib.items()}
   for c in children:
    k=c.tag if profile.get('xml_namespaces',False) else c.tag.split('}')[-1]
    v=node(c)
    if k in d:
     if not isinstance(d[k],list):d[k]=[d[k]]
     d[k].append(v)
    else:d[k]=v
   return d
  obj=node(ET.fromstring(data))
 elif fmt=='csv':
  rows=list(csv.DictReader(io.StringIO(data.decode(profile.get('encoding','utf-8-sig'))),delimiter=profile.get('delimiter',';')))
  if len(rows)!=1:raise ValueError('CSV document requires exactly one application row; nested values may be JSON')
  obj={}
  for k,v in rows[0].items():
   if v and v[:1] in ('{','['): v=orjson.loads(v)
   assign(obj,k,v)
 else:raise ValueError('Unsupported format: '+fmt)
 root=profile.get('root')
 if root: obj=get(obj,root)
 if not isinstance(obj,dict):raise ValueError('Root must be an object')
 if profile.get('mapping'):
  target={}
  for dest,src in profile['mapping'].items():
   marker=object();v=get(obj,src,marker)
   if v is not marker:assign(target,dest,v)
  obj=target
 for path,t in profile.get('types',{}).items():
  marker=object();v=get(obj,path,marker)
  if v is marker:continue
  if t=='number':v=float(v)
  elif t=='integer':v=int(v)
  elif t=='boolean':
   if str(v).lower() not in ('true','false'):raise ValueError('Invalid boolean '+path)
   v=str(v).lower()=='true'
  elif t=='json':v=orjson.loads(v)
  elif t=='string':v=str(v)
  assign(obj,path,v)
 for p in profile.get('required',[]):
  marker=object()
  if get(obj,p,marker) is marker:raise ValueError('Required field: '+p)
 return obj

def normalize(doc,app_id,source,profile):
 rows=[];seen=set();excludes=profile.get('exclude',[])
 def walk(v,parts,keys,address):
  path='.'.join(parts)
  if path in excludes:return
  if isinstance(v,dict):
   for k,val in v.items():walk(val,parts+[k],keys,address+'/'+k.replace('~','~0').replace('/','~1'))
   return
  if isinstance(v,list):
   spec=profile.get('arrays',{}).get(path,{'mode':'index'})
   ids=set()
   for i,item in enumerate(v):
    mode=spec['mode']
    if mode=='key':
     marker=object();key=[get(item,k,marker) for k in spec['keys']]
     if marker in key:raise ValueError('Missing array key at '+path)
    elif mode=='value':key=[item]
    elif mode=='index':key=[i]
    else:raise ValueError('Unknown array mode')
    encoded=orjson.dumps(key,option=orjson.OPT_SORT_KEYS).decode()
    if encoded in ids:raise ValueError('Array key collision at '+path+': '+encoded)
    ids.add(encoded);walk(item,parts,keys+[key],address+'/'+str(i))
   return
  value,typ=scalar(v);adjusted=value
  for bucket in profile.get('buckets',{}).get(path,[]):
   if typ=='number' and bucket['min']<v<=bucket['max']:adjusted=bucket['label'];break
  key_json=orjson.dumps(keys).decode()
  # Compatibility display is escaped; KEY_JSON is authoritative for matching.
  array_key='|'.join(quote(scalar(x)[0],safe='@_- .') for k in keys for x in k)
  prefix=parts[0] if len(parts)>1 else '';cls=parts[1] if len(parts)>2 else '';suffix='.'.join(parts[2:-1]);leaf=parts[-1]
  if profile.get('teaching'):
   cls=parts[1];suffix={'scoreCardResult':'scoreCard','limitResult':'Limit','rejectReasons':'reason'}.get(cls,'')
   if cls=='rejectReasons':leaf=profile.get('scalar_key','')
  ident=(path,key_json)
  if ident in seen:raise ValueError('Attribute collision '+path)
  seen.add(ident)
  rows.append(dict(zip(COLS,[str(app_id),source,prefix,cls,suffix,array_key,leaf,value,adjusted,path,typ,address,key_json,'1'])))
 walk(doc,[],[],'')
 return rows

def frame(rows):return pl.DataFrame(rows,schema=SCHEMA)
def compare(normalized,valid_ids,tolerance=0):
 keys=['APP_ID','PATH','KEY_JSON']; meta=['PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY']
 b=normalized.filter((pl.col('SOURCE')=='BEFORE') & pl.col('APP_ID').is_in(valid_ids)).drop('SOURCE')
 a=normalized.filter((pl.col('SOURCE')=='AFTER') & pl.col('APP_ID').is_in(valid_ids)).drop('SOURCE')
 b=b.rename({c:c+'_BEFORE' for c in b.columns if c not in keys});a=a.rename({c:c+'_AFTER' for c in a.columns if c not in keys})
 out=b.join(a,on=keys,how='full',coalesce=True,validate='1:1')
 equal=(pl.col('TYPE_BEFORE')==pl.col('TYPE_AFTER')) & (pl.col('VALUE_BEFORE')==pl.col('VALUE_AFTER'))
 if tolerance>0:equal=equal | ((pl.col('TYPE_BEFORE')=='number') & (pl.col('TYPE_AFTER')=='number') & ((pl.col('VALUE_BEFORE').cast(pl.Float64,strict=False)-pl.col('VALUE_AFTER').cast(pl.Float64,strict=False)).abs()<=tolerance))
 out=out.with_columns([pl.coalesce(c+'_BEFORE',c+'_AFTER').alias(c) for c in meta]+[pl.when(pl.col('PRESENT_BEFORE').is_null()).then(pl.lit('ATTRIBUTE_NEW')).when(pl.col('PRESENT_AFTER').is_null()).then(pl.lit('ATTRIBUTE_LOST')).when(equal).then(pl.lit('SAME')).otherwise(pl.lit('VALUE_CHANGED')).alias('CHANGE_TYPE')])
 return out.with_columns((pl.col('CHANGE_TYPE')!='SAME').cast(pl.Int8).alias('DIFF_FLAG')).rename({'ADJUSTED_VALUE_BEFORE':'ADJUSTED_BEFORE','ADJUSTED_VALUE_AFTER':'ADJUSTED_AFTER'}).sort(keys)
