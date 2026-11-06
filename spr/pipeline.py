import hashlib, time, os, shutil, json
from pathlib import Path
import orjson, polars as pl
from concurrent.futures import ThreadPoolExecutor
from .normalize import parse,normalize,get,frame,compare
from .analytics import Mart

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def input_bytes(files,workers=4):
 """Bounded I/O prefetch. Parsing remains deterministic in input order."""
 def read(f):
  try:return f,Path(f['path']).read_bytes()
  except OSError as e:return f,e
 if workers<=1:
  yield from map(read,files)
 else:
  with ThreadPoolExecutor(max_workers=min(workers,8)) as pool:
   for start in range(0,len(files),64):
    yield from pool.map(read,files[start:start+64])
def segment(app,b,a,p):
 def trials(d):return sorted(get(d,p.get('trials_path','decisionResult.product_trials'),[]) or [],key=lambda x:str(x.get('product_id')))
 tb,ta=trials(b),trials(a)
 r={'APP_ID':app,'PRODUCTS':';'.join(sorted({str(t['product_id']) for t in tb+ta})),'PARTNER_TYPE':';'.join(sorted({str(t.get('partner_type','')) for t in tb+ta})),'ROUTING_METHOD':';'.join(sorted({str(t.get('routing_method','')) for t in tb+ta}))}
 for src,d,ts in [('BEFORE',b,tb),('AFTER',a,ta)]:
  first=ts[0] if ts else {};offers=get(d,p.get('offers_path','decisionResult.available_offers'),[]) or []
  for k,v in {'DECISION':get(d,p.get('decision_path','decisionResult.decision')),'PD_MODEL':get(first,'scoring.pd.modelName'),'CUTOFF':get(first,'cutOff.name'),'REJECT_REASON':first.get('rejectReason'),'SCORE_OKB':get(d,'characteristics.score_okb'),'TOP_OFFER':str(min(offers,key=lambda x:x.get('rank',999)).get('product_id')) if offers else None}.items():r[k+'_'+src]=v
 r['DECISION_CHANGED']=int(r['DECISION_BEFORE']!=r['DECISION_AFTER'])
 return r

def product_rows(app,b,a,p):
 rows=[]
 for src,d in [('BEFORE',b),('AFTER',a)]:
  for t in get(d,p.get('trials_path','decisionResult.product_trials'),[]) or []:
   rows.append({'APP_ID':app,'SOURCE':src,'PRODUCT':str(t.get('product_id')),'PARTNER':str(t.get('partner_type','')),'MODEL':get(t,'scoring.pd.modelName'),'SCORE':get(t,'scoring.pd.value'),'CUTOFF':get(t,'cutOff.value'),'DECISION':t.get('decision'),'STOP':any(x.get('hit') is True for x in t.get('rules',[]))})
 return rows

def process(files,profile,target,progress=lambda *x:None,expected=None,checks=None,read_workers=1):
 target=Path(target);target.mkdir(parents=True,exist_ok=True)
 started=time.perf_counter();timings={};quality=[];docs={};rows=[];batches=[];found=set(expected or []);bad=set();stage=time.perf_counter()
 for i,(f,raw) in enumerate(input_bytes(files,read_workers)):
  app=Path(f['name']).stem;src=f['source'];entry={'APP_ID':app,'SOURCE':src,'FILE_ID':f.get('id',''),'NAME':f['name'],'STATUS':'parsed','ERROR':''}
  try:
   if isinstance(raw,Exception):raise raw
   if f.get('sha256') and hashlib.sha256(raw).hexdigest()!=f['sha256']:raise ValueError('Source checksum mismatch')
   doc=parse(raw,f.get('format','json'),profile)
   internal=get(doc,profile.get('app_id','')) if profile.get('app_id') else app
   if internal is None:raise ValueError('APP_ID missing')
   internal=str(internal)
   if profile.get('filename_id',True) and internal!=app:entry['STATUS']='id_conflict';raise ValueError('APP_ID differs from filename: '+internal)
   app=internal;entry['APP_ID']=app
   if (app,src) in docs or (app,src) in bad:entry['STATUS']='duplicate';bad.add((app,src));raise ValueError('Duplicate response')
   n=normalize(doc,app,src,profile);docs[app,src]=doc;rows.extend(n)
  except Exception as e:
   if entry['STATUS']=='parsed':entry['STATUS']='parse_error' if isinstance(e,(orjson.JSONDecodeError,UnicodeError)) or f.get('format')=='xml' else 'schema_error'
   entry['ERROR']=str(e)[:1000];bad.add((app,src))
  found.add(app);quality.append(entry)
  if i%100==0:
   if rows:batches.append(frame(rows));rows=[]
   progress('Чтение и нормализация',i/len(files)*55,{'files':i,'total':len(files)})
 timings['read_parse_normalize']=time.perf_counter()-stage
 # Sets make quality accounting linear rather than repeated scans.
 existing={(q['APP_ID'],q['SOURCE']) for q in quality}
 for q in quality:
  if q['STATUS']=='parsed' and (q['APP_ID'],q['SOURCE']) in bad:q['STATUS']='duplicate';q['ERROR']='Конфликтующий дубликат ответа'
 for app in sorted(found):
  for src in ('BEFORE','AFTER'):
   if (app,src) not in existing:quality.append({'APP_ID':app,'SOURCE':src,'FILE_ID':'','NAME':'','STATUS':'missing','ERROR':'Ответ отсутствует'})
 valid=sorted(a for a in found if all((a,s) in docs and (a,s) not in bad for s in ('BEFORE','AFTER')))
 stage=time.perf_counter();n=pl.concat(batches+[frame(rows)],how='vertical');batches.clear();rows.clear();timings['union']=time.perf_counter()-stage
 stage=time.perf_counter();c=compare(n,valid,profile.get('numeric_tolerance',0));timings['comparison']=time.perf_counter()-stage
 progress('Сопоставление и разрезы',65,{'pairs':len(valid)})
 stage=time.perf_counter();segs=[segment(a,docs[a,'BEFORE'],docs[a,'AFTER'],profile) for a in valid]
 ss={'APP_ID':pl.String,'PRODUCTS':pl.String,'PARTNER_TYPE':pl.String,'ROUTING_METHOD':pl.String,'DECISION_CHANGED':pl.Int64}
 for k in ['DECISION','PD_MODEL','CUTOFF','REJECT_REASON','SCORE_OKB','TOP_OFFER']:
  for src in ['BEFORE','AFTER']:ss[k+'_'+src]=pl.Float64 if k=='SCORE_OKB' else pl.String
 s=pl.DataFrame(segs,schema=ss)
 products=pl.DataFrame([r for a in valid for r in product_rows(a,docs[a,'BEFORE'],docs[a,'AFTER'],profile)],schema={'APP_ID':pl.String,'SOURCE':pl.String,'PRODUCT':pl.String,'PARTNER':pl.String,'MODEL':pl.String,'SCORE':pl.Float64,'CUTOFF':pl.Float64,'DECISION':pl.String,'STOP':pl.Boolean})
 timings['segments']=time.perf_counter()-stage
 stage=time.perf_counter();membership=n.filter(pl.col('APP_ID').is_in(valid)).join(c.select(['APP_ID','PATH','KEY_JSON','DIFF_FLAG','CHANGE_TYPE']),on=['APP_ID','PATH','KEY_JSON'],how='inner').rename({'ADJUSTED_VALUE':'VALUE_ADJUSTED'})
 dims=['PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','VALUE_ADJUSTED','DIFF_FLAG','SOURCE']
 facts=membership.group_by(dims).agg(pl.col('APP_ID').n_unique().alias('CNT')).sort(dims)
 timings['aggregations']=time.perf_counter()-stage
 stage=time.perf_counter()
 for name,df in {'normalized':n,'comparison':c,'quality':pl.DataFrame(quality,schema={k:pl.String for k in ['APP_ID','SOURCE','FILE_ID','NAME','STATUS','ERROR']}),'segments':s,'products':products,'membership':membership,'facts':facts}.items():
  df.write_parquet(target/(name+'.parquet'),compression='zstd',statistics=True)
 timings['parquet']=time.perf_counter()-stage
 progress('Автоматические проверки',80,{})
 stage=time.perf_counter();check_results=[]
 if checks:
  from .runner import execute
  for check in checks:
   result=execute(check['code'],check.get('params',{}),target)
   if result.get('error'):check_results.append({'LABEL':check['name'],'RESULT':'ERROR','ERROR':result['error'],'STDOUT':result.get('stdout',''),'STDERR':result.get('stderr',''),'TRACEBACK':result.get('traceback',''),'APP_IDS':[]})
   else:
    for row in result['rows']:row['CHECK_ID']=check.get('id');row['APP_IDS_LIMITED']=len(row.get('APP_IDS',[]))<row.get('CNT',0)
    check_results.extend(result['rows'])
 (target/'checks.json').write_bytes(orjson.dumps(check_results));timings['checks']=time.perf_counter()-stage
 changed=c.filter(pl.col('DIFF_FLAG')==1)['APP_ID'].n_unique()
 summary={'discovered':len(found),'valid_before':sum((a,'BEFORE') in docs and (a,'BEFORE') not in bad for a in found),'valid_after':sum((a,'AFTER') in docs and (a,'AFTER') not in bad for a in found),'valid_pairs':len(valid),'excluded':len(found)-len(valid),'changed_apps':changed,'decision_changed':s['DECISION_CHANGED'].sum() or 0,'attributes':c.height,'changes':c.filter(pl.col('DIFF_FLAG')==1).height,'normalized_rows':n.height,'files':len(files),'quality':{status:sum(q['STATUS']==status for q in quality) for status in ['missing','parse_error','schema_error','id_conflict','duplicate']}}
 timings['total']=time.perf_counter()-started
 (target/'summary.json').write_bytes(orjson.dumps(summary));(target/'timings.json').write_bytes(orjson.dumps(timings));(target/'profile.json').write_bytes(orjson.dumps(profile))
 manifest={'version':1,'created':time.time(),'summary':summary,'timings':timings,'files':{p.name:{'sha256':sha(p),'size':p.stat().st_size} for p in target.iterdir() if p.is_file()},'inputs':[{k:v for k,v in f.items() if k in ['id','sha256','source','name','size']} for f in files]}
 (target/'manifest.json').write_bytes(orjson.dumps(manifest));progress('Артефакты проверены',95,{})
 return summary

def verify_manifest(path):
 p=Path(path);m=orjson.loads((p/'manifest.json').read_bytes())
 for name,entry in m['files'].items():
  if Path(name).name!=name or sha(p/name)!=entry['sha256']:raise ValueError('Artifact checksum mismatch: '+name)
 return m
