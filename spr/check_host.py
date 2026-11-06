"""Runs ONLY inside OS isolation. No metadata credentials are made available."""
import sys, json, io, contextlib, traceback, resource
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analytics import Mart
resource.setrlimit(resource.RLIMIT_CPU,(15,15))
resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024*1024,4*1024*1024))
resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
resource.setrlimit(resource.RLIMIT_DATA,(1024**3,1024**3))
if sys.platform=='linux':
 resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
 resource.setrlimit(resource.RLIMIT_NPROC,(64,64))
job=json.loads(Path(sys.argv[1]).read_text());buf=io.StringIO();err=io.StringIO();mart=None
try:
 scope={};mart=Mart(job['artifact'])
 with contextlib.redirect_stdout(buf),contextlib.redirect_stderr(err):
  exec(compile(job['code'],'procedure.py','exec'),scope)
  check=scope.get('CHECK')
  if not isinstance(check,dict) or not callable(check.get('entrypoint')):raise ValueError('Expected CHECK with callable entrypoint')
  rows=check['entrypoint'](mart,job['params'])
  if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Return list[dict], max 10000 rows')
  required={'LABEL','SOURCE','ARGUMENT','CNT','TOTAL','METRIC','APP_IDS','RESULT','CONFIDENCE'}
  for row in rows:
   if not isinstance(row,dict) or not required<=row.keys():raise ValueError('Incomplete check result contract')
   if row['SOURCE'] not in ('BEFORE','AFTER') or row['RESULT'] not in ('PASS','FAIL') or row['CONFIDENCE'] not in ('HIGH','LOW'):raise ValueError('Invalid check enum')
   if not isinstance(row['APP_IDS'],list):raise ValueError('APP_IDS must be list')
 result={'rows':rows}
except BaseException as e:result={'error':str(e),'traceback':traceback.format_exc()[-12000:]}
finally:
 if mart:mart.close()
result.update(stdout=buf.getvalue()[-16000:],stderr=err.getvalue()[-16000:])
print(json.dumps(result,ensure_ascii=False,allow_nan=False))
