import argparse,getpass,shutil,hashlib,json,subprocess,sys,signal,time,os
from pathlib import Path
from sqlalchemy import delete,select
from .config import ROOT,DATA,MATERIALS
from .db import Session,User,Member,Record,File,Run,Login,uid
from .security import hash_password,verify_password
from .sources import documents, unique_files
from .paths import storage_ref

def init(password=None, *, sync_admin_password=False):
 if sync_admin_password and not password:raise ValueError('Задайте пароль администратора')
 subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,check=True)
 with Session.begin() as s:
  u=s.execute(select(User).where(User.name=='admin')).scalar()
  if not u or sync_admin_password and (not u.admin or not verify_password(password,u.password)):
   value=password or getpass.getpass('Новый пароль администратора (12+ символов): ')
   digest=hash_password(value,minimum_length=5 if sync_admin_password and value=='admin' else 12)
   if u:
    u.password=digest;u.admin=1
    s.execute(delete(Login).where(Login.user_id==u.id))
   else:
    u=User(id=uid(),name='admin',password=digest,admin=1);s.add(u)
  project=s.execute(select(Record).where(Record.kind=='project')).scalar()
  if not project:
   ident=uid();project=Record(id=ident,project=ident,kind='project',name='Стратегия · открытый датасет',author=u.id,body={});s.add(project);s.add(Member(user_id=u.id,project=ident,role='admin'))
  for p in unique_files(ROOT/'profiles', ('.json',)):
   if not s.execute(select(Record).where(Record.project==project.id,Record.kind=='profile',Record.name==p.stem)).scalar():s.add(Record(project=project.id,kind='profile',name=p.stem,author=u.id,body=json.loads(p.read_text(encoding='utf-8'))))
  if MATERIALS:
   for p in unique_files(MATERIALS/'checks', ('.py',)):
    if not p.name.startswith('check_'):continue
    if not s.execute(select(Record).where(Record.project==project.id,Record.kind=='procedure',Record.name==p.stem)).scalar():s.add(Record(project=project.id,kind='procedure',name=p.stem,author=u.id,body={'code':p.read_text(encoding='utf-8'),'params':{},'type':'check'}))
 return project.id

def import_dir(project,path,name='Открытый набор · 2002 заявки'):
 path=(ROOT / path).resolve();manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'));dataset=uid();files=[]
 for source,spec in manifest['srez'].items():
  for p in documents(path/spec['dir']):
   if p.suffix.lower() not in ('.json','.xml','.csv'):continue
   raw=p.read_bytes();ident=uid();target=DATA/'sources'/project/dataset/ident;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
   files.append({'id':ident,'project':project,'dataset':dataset,'source':source,'name':p.name,'path':storage_ref('sources',project,dataset,ident).as_posix(),'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'format':p.suffix[1:],'status':'stored'})
 with Session.begin() as s:
  s.add(Record(id=dataset,project=project,kind='dataset',name=name,body={'origin':path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else 'Импорт из каталога','manifest':manifest,'imported':time.time()}));s.execute(File.__table__.insert(),files)
 return dataset

def main():
 parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','local-init','local-reset','demo','serve','password','worker','backup','restore']);parser.add_argument('argument',nargs='?');parser.add_argument('--password-stdin',action='store_true');parser.add_argument('--if-available',action='store_true');parser.add_argument('--host',default='127.0.0.1')
 parser.add_argument('--port',type=int,default=8765)
 args=parser.parse_args()
 if args.command=='local-init':
  init('admin',sync_admin_password=True)
  return
 if args.command=='local-reset':
  from .db import Audit
  init('admin',sync_admin_password=True)
  with Session.begin() as s:
   u=s.execute(select(User).where(User.name=='admin')).scalar_one()
   s.execute(delete(Login).where(Login.user_id==u.id))
   s.execute(delete(Audit).where(Audit.action=='login_failed',Audit.object_id.in_(['127.0.0.1','::1'])))
  print('Local login reset: admin / admin')
  return
 if args.command=='init':print('Проект:',init(sys.stdin.readline().rstrip('\n') if args.password_stdin else None))
 elif args.command=='demo':
  with Session() as s:
   project=s.scalars(select(Record.id).where(Record.kind=='project').order_by(Record.created).limit(1)).first()
   if not project:parser.error('Сначала выполните init или local-reset')
   ds=s.scalars(select(Record.id).where(Record.kind=='dataset',Record.project==project).order_by(Record.created).limit(1)).first()
  if not ds:
   source=MATERIALS/'data/RUN-20260827-0001' if MATERIALS else None
   if source is None or not (source/'manifest.json').is_file():
    if args.if_available:
     print('Учебный набор отсутствует. Загрузите свои файлы в разделе «Данные и профили».')
     return
    parser.error('Учебный набор не найден')
   ds=import_dir(project,source)
  print('Датасет:',ds)
 elif args.command=='password':
  value=sys.stdin.readline().rstrip('\n') if args.password_stdin else getpass.getpass('Новый пароль (12+ символов): ')
  with Session.begin() as s:
   u=s.execute(select(User).where(User.name==(args.argument or 'admin'))).scalar_one();u.password=hash_password(value)
   s.execute(delete(Login).where(Login.user_id==u.id))
  print('Пароль обновлён; предыдущие сеансы завершены.')
 elif args.command=='serve':
  children=[subprocess.Popen([sys.executable,'-m','spr.worker']),subprocess.Popen([sys.executable,'-m','uvicorn','spr.api:app','--host',args.host,'--port',str(args.port)])]
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
