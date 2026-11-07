"""Procedure host for isolated and explicitly trusted local execution."""
import sys, json, io, contextlib, traceback, os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analytics import Mart
job=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
hosted=os.environ.get('SPR_SANDBOX_APPLIED')=='1'
if sys.platform!='win32' and not job.get('trusted_local',False) and not hosted:
 import resource
 resource.setrlimit(resource.RLIMIT_CPU,(15,15))
 resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024*1024,4*1024*1024))
 resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
 # Darwin RLIMIT_DATA cannot be raised above its platform ceiling. Linux
 # additionally enforces address-space memory; Darwin uses the OS sandbox.
 if sys.platform=='linux':resource.setrlimit(resource.RLIMIT_DATA,(1024**3,1024**3))
 if sys.platform=='linux':
  resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
  resource.setrlimit(resource.RLIMIT_NPROC,(64,64))
buf=io.StringIO();err=io.StringIO();mart=None
try:
 # DuckDB's background workers can fail under the hosted syscall filter.
 scope={};mart=Mart(job['artifact'],threads=1 if hosted else 2)
 if job.get('kind','check')=='check' and isinstance(job.get('data'),dict) and '_analysis_scope' in job['data']:
  from analysis_scope import AnalysisScope,parse_options
  AnalysisScope(mart,parse_options(job['data']['_analysis_scope']),valid_pairs_only=True)
 with contextlib.redirect_stdout(buf),contextlib.redirect_stderr(err):
  exec(compile(job['code'],'procedure.py','exec'),scope)
  if job.get('kind','check')!='check':
   rows=scope['run'](job['data'],job['params'])
   if job['kind']=='parse':
    if not isinstance(rows,dict):raise ValueError('Expected dict')
   elif not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError('Expected list[dict]')
  else:
   check=scope.get('CHECK')
   if not isinstance(check,dict) or not callable(check.get('entrypoint')):raise ValueError('Expected CHECK with callable entrypoint')
   rows=check['entrypoint'](mart,job['params'])
   if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Return list[dict], max 10000 rows')
   required={'LABEL','SOURCE','ARGUMENT','CNT','TOTAL','METRIC','APP_IDS','RESULT','CONFIDENCE'}
   for row in rows:
    if not isinstance(row,dict) or not required<=row.keys():raise ValueError('Incomplete check result contract')
    if row['SOURCE'] not in ('BEFORE','AFTER') or row['RESULT'] not in ('PASS','FAIL') or row['CONFIDENCE'] not in ('HIGH','LOW'):raise ValueError('Invalid check enum')
    count,total,ids=row['CNT'],row['TOTAL'],row['APP_IDS']
    if type(count) is not int or type(total) is not int or not 0<=count<=total:
     raise ValueError('CNT and TOTAL must be application counts, with 0 <= CNT <= TOTAL')
    if not isinstance(ids,list) or any(not isinstance(app,str) for app in ids) or len(set(ids))!=len(ids) or len(ids)>count:
     raise ValueError('APP_IDS must contain unique application IDs and cannot exceed CNT')
 result={'rows':rows}
except BaseException as e:result={'error':str(e),'traceback':traceback.format_exc()[-12000:]}
finally:
 if mart:mart.close()
result.update(stdout=buf.getvalue()[-16000:],stderr=err.getvalue()[-16000:])
print(json.dumps(result,ensure_ascii=False,allow_nan=False))
