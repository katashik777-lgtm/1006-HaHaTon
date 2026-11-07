import time, os, shutil, threading, traceback
from pathlib import Path
from sqlalchemy import select, update
from .db import Session,Run,File,uid
from .config import DATA
from .paths import storage_path,storage_ref,portable_snapshot
from .pipeline import process,verify_manifest

class Cancelled(Exception):pass

def claim():
 now=time.time();owner=uid()
 with Session.begin() as s:
  candidate=s.execute(select(Run.id).where((Run.status=='queued')|((Run.status=='running')&(Run.lease<now))).order_by(Run.created).limit(1)).scalar()
  if not candidate:return None
  n=s.execute(update(Run).where(Run.id==candidate,((Run.status=='queued')|((Run.status=='running')&(Run.lease<now)))).values(status='running',owner=owner,lease=now+45,attempts=Run.attempts+1,updated=now)).rowcount
  return (candidate,owner) if n else None

def work(run_id,owner):
 stop=threading.Event()
 def heartbeat():
  while not stop.wait(10):
   with Session.begin() as s:s.execute(update(Run).where(Run.id==run_id,Run.owner==owner,Run.status=='running').values(lease=time.time()+45))
 thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
 def progress(stage,value,detail):
  with Session.begin() as s:
   r=s.get(Run,run_id)
   if r.status=='cancelled' or r.owner!=owner:raise Cancelled()
   logs=[dict(entry) for entry in (r.log or [])];now=time.time()
   if not logs or logs[-1]['stage']!=stage:
    if logs:logs[-1]['duration_seconds']=max(0,now-logs[-1]['time'])
    logs.append({'stage':stage,'time':now,'detail':detail})
   else:logs[-1]['detail']=detail
   r.stage=stage;r.progress=value;r.updated=time.time();r.log=logs
 try:
  with Session() as s:
   r=s.get(Run,run_id);snap=portable_snapshot(r.snapshot,r.project,r.dataset)
   if r.attempts>3:raise ValueError('Исчерпаны 3 попытки восстановления')
   final=storage_path(DATA,'artifacts',r.project,r.dataset,r.id)
   ref=storage_ref('artifacts',r.project,r.dataset,r.id)
   files=[{**item,'path':storage_path(DATA,'sources',r.project,r.dataset,item['id'])} for item in snap['files']]
  if final.exists():summary=verify_manifest(final)['summary']
  else:
   temp=final.with_name(final.name+'.tmp-'+owner[:8]);temp.mkdir(parents=True,exist_ok=True)
   summary=process(files,snap['profile'],temp,progress,checks=snap.get('checks'),expected=snap.get('expected'),procedures=snap.get('procedures'))
   (temp/'snapshot.json').write_text(__import__('json').dumps(snap,ensure_ascii=False),encoding='utf-8')
   # Include snapshot before publication and fsync both file contents and directory entry.
   import orjson
   from .pipeline import sha
   m=orjson.loads((temp/'manifest.json').read_bytes());m['files']['snapshot.json']={'sha256':sha(temp/'snapshot.json'),'size':(temp/'snapshot.json').stat().st_size};(temp/'manifest.json').write_bytes(orjson.dumps(m))
   verify_manifest(temp)
   for f in temp.iterdir():
    with f.open('r+b') as stream:os.fsync(stream.fileno())
   progress('Публикация',98,{})
   os.rename(temp,final)
   # Windows cannot open a directory with os.open. The rename is still atomic;
   # POSIX additionally makes the directory entry durable across power loss.
   if os.name!='nt':
    fd=os.open(final.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
  with Session.begin() as s:
   r=s.get(Run,run_id)
   if r.owner!=owner or r.status=='cancelled':return
   r.artifact=ref.as_posix();r.summary=summary;r.updated=time.time()
   if summary['files'] and not summary['quality']['parsed']:
    r.status='failed';r.stage='Все файлы отклонены'
    checksums=summary['quality'].get('checksum_error',0)
    r.error=f'Не удалось обработать ни одного файла. Отклонено файлов: {summary["files"]}.'
    if checksums:r.error+=f' Контрольная сумма не совпала у {checksums} файлов.'
   else:r.status='completed';r.stage='Готово';r.progress=100
 except Cancelled:pass
 except Exception as e:
  with Session.begin() as s:
   r=s.get(Run,run_id)
   if r.owner==owner and r.status=='running':r.status='failed';r.stage='Ошибка';r.error=str(e);r.log=(r.log or [])+[{'stage':'Ошибка','traceback':traceback.format_exc()[-10000:],'time':time.time()}]
 finally:
  stop.set();thread.join(timeout=1)
  with Session() as s:
   r=s.get(Run,run_id)
   if r:
    for p in (DATA/'artifacts'/r.project/r.dataset).glob(run_id+'.tmp-'+owner[:8]):shutil.rmtree(p,ignore_errors=True)

def main():
 while True:
  job=claim()
  if job:work(*job)
  else:time.sleep(1)
if __name__=='__main__':main()
