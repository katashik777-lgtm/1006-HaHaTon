import os, sys, json, tempfile, subprocess, shutil, signal
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def capability():
 if os.environ.get('SPR_TRUSTED_LOCAL_CODE')=='1':return 'Доверенный локальный Python (без изоляции ОС)'
 if sys.platform=='darwin' and Path('/usr/bin/sandbox-exec').exists():return 'macOS Seatbelt'
 if sys.platform=='linux' and shutil.which('bwrap'):return 'Linux bubblewrap'
 return None

def execute(code,params,artifact,kind="check",data=None):
 if not capability():return {'error':'Изоляция ОС недоступна. Исполнение кода отключено.'}
 if os.environ.get('SPR_TRUSTED_LOCAL_CODE')=='1':return execute_trusted(code,params,artifact,kind,data)
 artifact=Path(artifact).resolve()
 with tempfile.TemporaryDirectory(prefix='spr-exec-') as tmp:
  tmp=Path(tmp).resolve();job=tmp/'job.json';job.write_text(json.dumps({'kind':kind,'data':data,'code':code,'params':params,'artifact':str(artifact)}));python=sys.executable;host=str(ROOT/'spr/check_host.py')
  command=[python,'-I',host,str(job)]
  if sys.platform=='darwin':
   allow=[str(ROOT/'.venv'),str(ROOT/'spr'),str(artifact),str(tmp),'/System','/usr','/bin','/Library','/opt/homebrew','/private/var/db/dyld','/dev','/private/etc']
   policy='(version 1)(deny default)(allow process-exec sysctl-read mach-lookup)(allow file-read-metadata)(allow file-read* (literal "/"))(allow file-read* '+ ' '.join('(subpath '+json.dumps(p,ensure_ascii=False)+')' for p in allow)+')(allow file-write* (subpath '+json.dumps(str(tmp),ensure_ascii=False)+') (literal "/dev/null"))'
   policyfile=tmp/'policy.sb';policyfile.write_text(policy);command=['/usr/bin/sandbox-exec','-f',str(policyfile)]+command
  else:
   command=['bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL','--clearenv','--setenv','PATH','/usr/bin','--setenv','POLARS_MAX_THREADS','2','--setenv','HOME','/tmp','--proc','/proc','--dev','/dev','--tmpfs','/tmp']
   # venv Python may point into setup-python, pyenv or /opt runtimes.
   runtime=str(Path(sys.base_prefix).resolve())
   for p in dict.fromkeys(['/usr','/lib','/lib64',runtime,str(Path(sys.executable).resolve().parent.parent),sys.prefix,str(ROOT/'spr'),str(artifact),str(tmp)]):
    if Path(p).exists():command+=['--ro-bind',p,p]
   command += ['--remount-ro','/tmp','--chdir','/tmp',python,'-I',host,str(job)]
  env={'PATH':'/usr/bin:/bin:/opt/homebrew/bin','HOME':str(tmp),'TMPDIR':str(tmp),'POLARS_MAX_THREADS':'2','OMP_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}
  with (tmp/'stdout').open('w+b') as out,(tmp/'stderr').open('w+b') as err:
   proc=subprocess.Popen(command,stdout=out,stderr=err,env=env,start_new_session=True)
   try:proc.wait(timeout=25)
   except subprocess.TimeoutExpired:
    os.killpg(proc.pid,signal.SIGKILL);proc.wait();return {'error':'Превышено время исполнения (25 секунд)'}
   finally:
    try:os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:pass
   out.seek(0);err.seek(0);raw=out.read(4*1024*1024);stderr=err.read(16000).decode(errors='replace')
  try:return json.loads(raw)
  except Exception:return {'error':'Исполнитель завершился без результата','stderr':stderr,'exit_code':proc.returncode}


def execute_trusted(code,params,artifact,kind="check",data=None):
 """Explicit opt-in for code trusted by the laptop owner; not a sandbox."""
 with tempfile.TemporaryDirectory(prefix='spr-local-') as folder:
  tmp=Path(folder)
  job=tmp/'job.json'
  job.write_text(json.dumps({'kind':kind,'data':data,'code':code,'params':params,'artifact':str(Path(artifact).resolve()),'trusted_local':True}),encoding='utf-8')
  env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP','HOME','USERPROFILE')}
  env.update(POLARS_MAX_THREADS='2',OMP_NUM_THREADS='1',PYTHONIOENCODING='utf-8')
  with (tmp/'stdout').open('w+b') as out,(tmp/'stderr').open('w+b') as err:
   proc=subprocess.Popen([sys.executable,'-I','-X','utf8',str(ROOT/'spr/check_host.py'),str(job)],cwd=tmp,env=env,stdout=out,stderr=err,start_new_session=os.name!='nt')
   timed_out=False
   try:proc.wait(timeout=25)
   except subprocess.TimeoutExpired:timed_out=True
   finally:
    if os.name=='nt':
     if proc.poll() is None:subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
    else:
     try:os.killpg(proc.pid,signal.SIGKILL)
     except ProcessLookupError:pass
    proc.wait()
   if timed_out:return {'error':'Превышено время исполнения (25 секунд)'}
   out.seek(0);err.seek(0)
   raw=out.read(4*1024*1024+1);stderr=err.read(16000).decode('utf-8',errors='replace')
  try:return json.loads(raw)
  except Exception:return {'error':'Исполнитель завершился без результата','stderr':stderr,'exit_code':proc.returncode}
