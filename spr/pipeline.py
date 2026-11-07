import hashlib, time, os, shutil, json
from pathlib import Path
import orjson, polars as pl
from concurrent.futures import ThreadPoolExecutor
from .normalize import parse,normalize,get,frame,compare,PARSE_ERRORS
from .analytics import Mart

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def verified_text(raw, digest, fmt):
 if not digest or hashlib.sha256(raw).hexdigest()==digest:return raw,False
 if fmt.lower() in ('json','xml','csv'):
  lf=raw.replace(b'\r\n',b'\n')
  for candidate in (lf,lf.replace(b'\n',b'\r\n')):
   if candidate!=raw and hashlib.sha256(candidate).hexdigest()==digest:return candidate,True
 raise ValueError('Контрольная сумма исходного файла не совпадает')

def input_bytes(files,workers=4):
 """Bounded I/O prefetch. Parsing remains deterministic in input order."""
 def read(f):
  try:
   started=time.perf_counter();raw=Path(f['path']).read_bytes();return {**f,'read_seconds':time.perf_counter()-started},raw
  except OSError as e:return f,e
 if workers<=1:
  yield from map(read,files)
 else:
  with ThreadPoolExecutor(max_workers=min(workers,8)) as pool:
   for start in range(0,len(files),64):
    yield from pool.map(read,files[start:start+64])
def segment(app,b,a,p):
 def trials(d):return get(d,p.get('trials_path','decisionResult.product_trials'),[]) or []
 tb,ta=trials(b),trials(a)
 r={'APP_ID':app,'PRODUCTS':';'.join(sorted({str(t['product_id']) for t in tb+ta})),'PARTNER_TYPE':';'.join(sorted({str(t.get('partner_type','')) for t in tb+ta})),'ROUTING_METHOD':';'.join(sorted({str(t.get('routing_method','')) for t in tb+ta}))}
 for src,d,ts in [('BEFORE',b,tb),('AFTER',a,ta)]:
  decision=get(d,p.get('decision_path','decisionResult.decision'))
  primary_path=p.get('decision_product_path')
  primary=get(d,primary_path) if primary_path else None
  candidates=[t for t in ts if str(t.get('product_id'))==str(primary)] if primary is not None else [t for t in ts if decision is not None and t.get('decision')==decision]
  chosen=candidates[0] if len(candidates)==1 else {}
  product=chosen.get('product_id')
  offers=get(d,p.get('offers_path','decisionResult.available_offers'),[]) or []
  for k,v in {'DECISION':decision,'DECISION_PRODUCT':str(product) if product is not None else None,
              'PD_MODEL':get(chosen,'scoring.pd.modelName'),'CUTOFF':get(chosen,'cutOff.name'),
              'REJECT_REASON':chosen.get('rejectReason'),'SCORE_OKB':get(d,'characteristics.score_okb'),
              'TOP_OFFER':str(min(offers,key=lambda x:x.get('rank',999)).get('product_id')) if offers else None}.items():r[k+'_'+src]=v
 r['DECISION_CHANGED']=int(r['DECISION_BEFORE']!=r['DECISION_AFTER'])
 return r

def product_rows(app,b,a,p):
 rows=[]
 for src,d in [('BEFORE',b),('AFTER',a)]:
  for t in get(d,p.get('trials_path','decisionResult.product_trials'),[]) or []:
   rows.append({'APP_ID':app,'SOURCE':src,'PRODUCT':str(t.get('product_id')),'PARTNER':str(t.get('partner_type','')),'MODEL':get(t,'scoring.pd.modelName'),'SCORE':get(t,'scoring.pd.value'),'CUTOFF':get(t,'cutOff.value'),'DECISION':t.get('decision'),'STOP_UNKNOWN':any(x.get('hit') is None for x in t.get('rules',[])), 'STOP':any(x.get('hit') is True for x in t.get('rules',[])), 'STOP_COUNT':sum(x.get('hit') is True for x in t.get('rules',[])), 'STOP_NAMES':json.dumps([str(x.get('name','Без названия')) for x in t.get('rules',[]) if x.get('hit') is True],ensure_ascii=False), 'REJECT_REASON':t.get('rejectReason')})
 return rows

def membership_rows(normalized, comparison, valid):
 return normalized.filter(pl.col('APP_ID').is_in(valid)).join(
  comparison.select(['APP_ID','PATH','KEY_JSON','DIFF_FLAG','CHANGE_TYPE','VALUE_BEFORE','VALUE_AFTER']),
  on=['APP_ID','PATH','KEY_JSON'],how='inner').rename({'ADJUSTED_VALUE':'VALUE_ADJUSTED'})


def partitioned_comparison(target, valid, tolerance, dims, batch_size, progress):
 # The input parquet is already published to the unpublished run directory.
 # Each APP_ID occurs in just one partition, so distinct counts are additive.
 import tempfile
 totals={'attributes':0,'changes':0,'changed_apps':0}
 with tempfile.TemporaryDirectory(dir=target) as folder:
  folder=Path(folder)
  normalized=pl.scan_parquet(target/'normalized.parquet')
  for index,start in enumerate(range(0,max(1,len(valid)),batch_size)):
   group=valid[start:start+batch_size]
   rows=normalized.filter(pl.col('APP_ID').is_in(group)).collect(engine='streaming')
   comparison=compare(rows,group,tolerance)
   membership=membership_rows(rows,comparison,group)
   comparison.write_parquet(folder/f'comparison-{index:06}.parquet')
   membership.write_parquet(folder/f'membership-{index:06}.parquet')
   totals['attributes']+=comparison.height
   totals['changes']+=comparison['DIFF_FLAG'].sum() or 0
   totals['changed_apps']+=comparison.filter(pl.col('DIFF_FLAG')==1)['APP_ID'].n_unique()
   progress('Сопоставление и разрезы',65+8*(start+len(group))/max(1,len(valid)),{'pairs':len(valid)})
  for name in ('comparison','membership'):
   parts=sorted(folder.glob(name+'-*.parquet'))
   pl.scan_parquet(parts).sink_parquet(target/(name+'.parquet'),engine='streaming')
  (pl.scan_parquet(target/'membership.parquet').group_by(dims)
   .agg(pl.col('APP_ID').n_unique().alias('CNT')).sort(dims)
   .sink_parquet(target/'facts.parquet',engine='streaming'))
 return totals


def process(files,profile,target,progress=lambda *x:None,expected=None,checks=None,read_workers=1,procedures=None):
 target=Path(target);target.mkdir(parents=True,exist_ok=True)
 started=time.perf_counter();timings={'read':0.0,'parse':0.0,'normalize':0.0};quality=[];docs={};rows=[];batches=[];found=set(expected or []);bad=set();stage=time.perf_counter()
 procedures=procedures or {}
 def custom(kind, data):
  from .procedures import transform
  spec=procedures.get(kind)
  if not spec:return data
  progress('Python: '+kind,60,{'procedure':spec.get('name'),'code':spec['code']})
  return transform(spec,kind,data,target)
 import inspect
 raw_progress=progress
 def progress(stage,value,detail):
  functions={'Чтение и нормализация':[parse,normalize],'Сопоставление и разрезы':[compare,segment,product_rows],'Автоматические проверки':[process],'Артефакты проверены':[verify_manifest]}
  if stage in functions:detail={**detail,'code':'\n\n'.join(inspect.getsource(fn) for fn in functions[stage])}
  raw_progress(stage,value,detail)
 progress('Чтение и нормализация',0,{'files':0,'total':len(files)})
 line_endings_verified=0
 for i,(f,raw) in enumerate(input_bytes(files,read_workers)):
  app=Path(f['name']).stem;src=f['source'];entry={'APP_ID':app,'SOURCE':src,'FILE_ID':f.get('id',''),'NAME':f['name'],'STATUS':'parsed','ERROR':''}
  try:
   if isinstance(raw,Exception):raise raw
   try:raw,recovered=verified_text(raw,f.get('sha256'),f.get('format','json'))
   except ValueError:
    entry['STATUS']='checksum_error';raise
   line_endings_verified+=int(recovered)
   timings['read']+=f.get('read_seconds',0);parse_started=time.perf_counter()
   doc=custom('parse',{'text':raw.decode('utf-8-sig'),'format':f.get('format','json'),'profile':profile,'app_id':app,'source':src}) if procedures.get('parse') else parse(raw,f.get('format','json'),profile)
   timings['parse']+=time.perf_counter()-parse_started
   internal=get(doc,profile.get('app_id','')) if profile.get('app_id') else app
   if internal is None:raise ValueError('APP_ID missing')
   internal=str(internal)
   if profile.get('filename_id',True) and internal!=app:entry['STATUS']='id_conflict';raise ValueError('APP_ID differs from filename: '+internal)
   app=internal;entry['APP_ID']=app
   if (app,src) in docs or (app,src) in bad:entry['STATUS']='duplicate';bad.add((app,src));raise ValueError('Duplicate response')
   normalize_started=time.perf_counter()
   n=custom('normalize',{'document':doc,'app_id':app,'source':src,'profile':profile}) if procedures.get('normalize') else normalize(doc,app,src,profile);
   if procedures.get('normalize'):frame(n)
   timings['normalize']+=time.perf_counter()-normalize_started
   docs[app,src]=doc;rows.extend(n)
  except Exception as e:
   if entry['STATUS']=='parsed':entry['STATUS']='parse_error' if isinstance(e,PARSE_ERRORS) else 'schema_error'
   entry['ERROR']=('Исходный документ отсутствует в хранилище' if isinstance(e,FileNotFoundError) else 'Не удалось прочитать исходный документ' if isinstance(e,OSError) else str(e)[:1000]);bad.add((app,src))
  found.add(app);quality.append(entry)
  if i%100==0:
   if rows:batches.append(frame(rows));rows=[]
   progress('Чтение и нормализация',i/len(files)*55,{'files':i+1,'total':len(files)})
 progress('Чтение и нормализация',55,{'files':len(files),'total':len(files)})
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
 progress('Сопоставление и разрезы',65,{'pairs':len(valid)})
 stage=time.perf_counter();segs=[segment(a,docs[a,'BEFORE'],docs[a,'AFTER'],profile) for a in valid]
 ss={'APP_ID':pl.String,'PRODUCTS':pl.String,'PARTNER_TYPE':pl.String,'ROUTING_METHOD':pl.String,'DECISION_CHANGED':pl.Int64}
 for k in ['DECISION','DECISION_PRODUCT','PD_MODEL','CUTOFF','REJECT_REASON','SCORE_OKB','TOP_OFFER']:
  for src in ['BEFORE','AFTER']:ss[k+'_'+src]=pl.Float64 if k=='SCORE_OKB' else pl.String
 s=pl.DataFrame(segs,schema=ss)
 product_schema={'APP_ID':pl.String,'SOURCE':pl.String,'PRODUCT':pl.String,'PARTNER':pl.String,'MODEL':pl.String,'SCORE':pl.Float64,'CUTOFF':pl.Float64,'DECISION':pl.String,'STOP':pl.Boolean,'STOP_UNKNOWN':pl.Boolean,'STOP_COUNT':pl.Int64,'STOP_NAMES':pl.String,'REJECT_REASON':pl.String}
 products=pl.DataFrame([r for a in valid for r in product_rows(a,docs[a,'BEFORE'],docs[a,'AFTER'],profile)],schema=product_schema)
 unpaired={}
 for a in sorted(found-set(valid)):
  b,after=(docs.get((a,src),{}) if (a,src) not in bad else {} for src in ('BEFORE','AFTER'))
  if b or after:unpaired[a]=(b,after)
 if unpaired:
  pl.DataFrame([segment(a,b,after,profile) for a,(b,after) in unpaired.items()],schema=ss).write_parquet(target/'unpaired_segments.parquet')
  pl.DataFrame([r for a,(b,after) in unpaired.items() for r in product_rows(a,b,after,profile)],schema=product_schema).write_parquet(target/'unpaired_products.parquet')
 timings['segments']=time.perf_counter()-stage
 valid_counts={side:sum((a,side) in docs and (a,side) not in bad for a in found) for side in ('BEFORE','AFTER')}
 docs.clear();unpaired.clear()
 dims=['PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','VALUE_ADJUSTED','DIFF_FLAG','SOURCE']
 decision_changed=s['DECISION_CHANGED'].sum() or 0
 normalized_rows=n.height
 stage=time.perf_counter()
 for name,df in {'normalized':n,'quality':pl.DataFrame(quality,schema={k:pl.String for k in ['APP_ID','SOURCE','FILE_ID','NAME','STATUS','ERROR']}),'segments':s,'products':products}.items():
  df.write_parquet(target/(name+'.parquet'),compression='zstd',statistics=True)
 del df,products,s
 timings['parquet']=time.perf_counter()-stage
 batch_size=int(os.environ.get('SPR_COMPARE_BATCH_SIZE','0'))
 if batch_size>0 and not (procedures.get('compare') or procedures.get('facts')):
  del n
  stage=time.perf_counter()
  totals=partitioned_comparison(target,valid,profile.get('numeric_tolerance',0),dims,batch_size,progress)
  timings['comparison']=time.perf_counter()-stage
  timings['aggregations']=0.0  # Included in the partitioned comparison pass.
 else:
  stage=time.perf_counter()
  c=pl.DataFrame(custom('compare',{'rows':n.to_dicts(),'valid_ids':valid,'profile':profile}),schema=compare(frame([]),[],0).schema) if procedures.get('compare') else compare(n,valid,profile.get('numeric_tolerance',0))
  timings['comparison']=time.perf_counter()-stage
  stage=time.perf_counter()
  membership=membership_rows(n,c,valid)
  del n
  if procedures.get('facts'):
   membership=pl.DataFrame(custom('facts',membership.to_dicts()),schema=membership.schema)
  facts=membership.group_by(dims).agg(pl.col('APP_ID').n_unique().alias('CNT')).sort(dims)
  timings['aggregations']=time.perf_counter()-stage
  stage=time.perf_counter()
  for name,df in {'comparison':c,'membership':membership,'facts':facts}.items():
   df.write_parquet(target/(name+'.parquet'),compression='zstd',statistics=True)
  timings['parquet']+=time.perf_counter()-stage
  totals={'attributes':c.height,'changes':c['DIFF_FLAG'].sum() or 0,
          'changed_apps':c.filter(pl.col('DIFF_FLAG')==1)['APP_ID'].n_unique()}
  del c,membership,facts,df
 summary={'discovered':len(found),'valid_before':valid_counts['BEFORE'],'valid_after':valid_counts['AFTER'],
          'valid_pairs':len(valid),'excluded':len(found)-len(valid),'decision_changed':decision_changed,
          'normalized_rows':normalized_rows,'files':len(files),**totals,
          'line_endings_verified':line_endings_verified,
          'quality':{status:sum(q['STATUS']==status for q in quality) for status in ['parsed','missing','parse_error','schema_error','id_conflict','duplicate','checksum_error']}}
 progress('Индексы витрины',76,{})
 # Build once inside the unpublished snapshot; all readers attach read-only.
 import duckdb
 index_started=time.perf_counter()
 with duckdb.connect(str(target/'comparison.duckdb'),config={'threads':2,'memory_limit':os.environ.get('SPR_DUCKDB_MEMORY','512MB')}) as indexed:
  indexed.execute("CREATE TABLE comparison AS SELECT * FROM read_parquet(?)",[str(target/'comparison.parquet')])
  indexed.execute('CREATE INDEX comparison_app ON comparison(APP_ID)')
  indexed.execute('CREATE INDEX comparison_path ON comparison(PATH)')
 timings['indexes']=time.perf_counter()-index_started
 progress('Автоматические проверки',80,{})
 stage=time.perf_counter()
 with Mart(target) as bucket_mart:bucket_results=bucket_mart.bucket_report()
 check_results=[];check_errors=[]
 if checks:
  from .runner import execute
  for check in checks:
   progress('Проверка: '+check['name'],80,{'procedure':check.get('id'),'code':check['code']})
   result=execute(check['code'],check.get('params',{}),target)
   if result.get('error'):check_errors.append({'LABEL':check['name'],'RESULT':'ERROR','ERROR':result['error'],'STDOUT':result.get('stdout',''),'STDERR':result.get('stderr',''),'TRACEBACK':result.get('traceback',''),'APP_IDS':[]})
   else:
    for row in result['rows']:row['CHECK_ID']=check.get('id');row['APP_IDS_LIMITED']=len(row.get('APP_IDS',[]))<row.get('CNT',0)
    check_results.extend(result['rows'])
 for name,results in [('checks',check_results),('buckets',bucket_results),('check_errors',check_errors)]:
  (target/(name+'.json')).write_bytes(orjson.dumps(results))
 timings['checks']=time.perf_counter()-stage
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
