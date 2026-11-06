import argparse,getpass,shutil,hashlib,json,subprocess,sys,signal,time,os
from pathlib import Path
from sqlalchemy import select
from .config import ROOT,DATA,MATERIALS
from .db import Session,User,Member,Record,File,Run,uid
from .security import hash_password

def init(password=None):
 subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,check=True)
 with Session.begin() as s:
  u=s.execute(select(User).where(User.name=='admin')).scalar()
  if not u:
   u=User(id=uid(),name='admin',password=hash_password(password or getpass.getpass('Новый пароль администратора (12+ символов): ')),admin=1);s.add(u)
  project=s.execute(select(Record).where(Record.kind=='project')).scalar()
  if not project:
   ident=uid();project=Record(id=ident,project=ident,kind='project',name='Стратегия · открытый датасет',author=u.id,body={});s.add(project);s.add(Member(user_id=u.id,project=ident,role='admin'))
  for p in (ROOT/'profiles').glob('*.json'):
   if not s.execute(select(Record).where(Record.project==project.id,Record.kind=='profile',Record.name==p.stem)).scalar():s.add(Record(project=project.id,kind='profile',name=p.stem,author=u.id,body=json.loads(p.read_text(encoding='utf-8'))))
  if MATERIALS:
   for p in (MATERIALS/'checks').glob('check_*.py'):
    if not s.execute(select(Record).where(Record.project==project.id,Record.kind=='procedure',Record.name==p.stem)).scalar():s.add(Record(project=project.id,kind='procedure',name=p.stem,author=u.id,body={'code':p.read_text(encoding='utf-8'),'params':{},'type':'check'}))
 return project.id

def import_dir(project,path,name='Открытый набор · 2002 заявки'):
 path=Path(path);manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'));dataset=uid();files=[]
 for source,spec in manifest['srez'].items():
  for p in sorted((path/spec['dir']).glob('*')):
   if p.suffix.lower() not in ('.json','.xml','.csv'):continue
   raw=p.read_bytes();ident=uid();target=DATA/'sources'/project/dataset/ident;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
   files.append({'id':ident,'project':project,'dataset':dataset,'source':source,'name':p.name,'path':str(target),'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'format':p.suffix[1:],'status':'stored'})
 with Session.begin() as s:
  s.add(Record(id=dataset,project=project,kind='dataset',name=name,body={'origin':str(path),'manifest':manifest,'imported':time.time()}));s.execute(File.__table__.insert(),files)
 return dataset

def main():
 parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','local-reset','demo','serve','password','worker','backup','restore']);parser.add_argument('argument',nargs='?');parser.add_argument('--password-stdin',action='store_true');parser.add_argument('--host',default='127.0.0.1');args=parser.parse_args()
 if args.command=='local-reset':
  import secrets
  from .db import Login,Audit
  from sqlalchemy import delete
  init(secrets.token_urlsafe(24))
  with Session.begin() as s:
   u=s.execute(select(User).where(User.name=='admin')).scalar_one()
   u.password=hash_password('admin',minimum_length=5)
   s.execute(delete(Login).where(Login.user_id==u.id))
   s.execute(delete(Audit).where(Audit.action=='login_failed',Audit.object_id.in_(['127.0.0.1','::1'])))
  print('Local login reset: admin / admin')
  return
 if args.command=='init':print('Проект:',init(sys.stdin.readline().rstrip('\n') if args.password_stdin else None))
 elif args.command=='demo':
  with Session() as s:project=s.execute(select(Record.id).where(Record.kind=='project')).scalar();ds=s.execute(select(Record.id).where(Record.kind=='dataset')).scalar()
  if not ds:ds=import_dir(project,MATERIALS/'data/RUN-20260827-0001')
  print('Датасет:',ds)
 elif args.command=='password':
  value=sys.stdin.readline().rstrip('\n') if args.password_stdin else getpass.getpass('Новый пароль (12+ символов): ')
  with Session.begin() as s:
   u=s.execute(select(User).where(User.name==(args.argument or 'admin'))).scalar_one();u.password=hash_password(value)
   from .db import Login
   from sqlalchemy import delete
   s.execute(delete(Login).where(Login.user_id==u.id))
  print('Пароль обновлён; предыдущие сеансы завершены.')
 elif args.command=='serve':
  children=[subprocess.Popen([sys.executable,'-m','spr.worker']),subprocess.Popen([sys.executable,'-m','uvicorn','spr.api:app','--host',args.host,'--port','8765'])]
  try:
   while all(c.poll() is None for c in children):time.sleep(1)
  except KeyboardInterrupt:pass
  finally:
   for c in children:c.terminate()
   for c in children:c.wait()
 elif args.command=='worker':
  from .worker import main as work;work()
 elif args.command in ('backup','restore'):
  from .backup import backup,restore
  (backup if args.command=='backup' else restore)(Path(args.argument))
if __name__=='__main__':main()
