import time, json, secrets, hashlib, csv, io, ast
from pathlib import Path
from contextlib import contextmanager
from functools import lru_cache
from threading import Lock
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File as Upload, Form, Depends
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, func
from .paths import storage_ref,storage_path,portable_snapshot
from .config import ROOT,DATA,BASE_URL,MATERIALS
from .db import Session,User,Login,Member,Record,Run,File,Audit,uid,audit,serialize
from .security import verify_password,token_hash,hash_password
from .analytics import Mart,where,FACT_DIMS
from .analysis_scope import AnalysisScope,parse_options,model_direction,DIRECTIONS
from .runner import capability,execute,hosted_isolation
from .public_demo import VIEWER_ID,demo_run,demo_repeats,is_demo_run
app=FastAPI(title='Скорифай · анализ изменений СПР',version='1.0.0')
demo_queue_lock=Lock()

@app.exception_handler(ValueError)
async def invalid_value(request,exc):
 return JSONResponse(status_code=422,content={'detail':str(exc)})

@app.middleware('http')
async def request_context(request,call_next):
 request.state.request_id=uid()
 if request.method not in ('GET','HEAD','OPTIONS'):
  origin=request.headers.get('origin')
  if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/') and origin.rstrip('/')!=BASE_URL.rstrip('/'):return Response('Origin rejected',status_code=403)
 response=await call_next(request)
 response.headers['X-Request-ID']=request.state.request_id;response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Frame-Options']='DENY';response.headers['Referrer-Policy']='same-origin'
 response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
 response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api') else 'no-cache'
 return response

def user(request:Request):
 token=request.cookies.get('spr_session','')
 with Session() as s:
  login=s.get(Login,token_hash(token))
  if not login or login.expires<time.time():raise HTTPException(401,'Требуется вход')
  u=s.get(User,login.user_id)
  if not u or u.id==VIEWER_ID and not demo_run(s):raise HTTPException(401)
  return u

def access(s,u,project,write=False,lead=False):
 if u.id==VIEWER_ID:
  run=demo_run(s)
  if write or lead or not run or run.project!=project:raise HTTPException(403,'Доступен просмотр открытого отчёта')
  return 'observer'
 role='admin' if u.admin else s.execute(select(Member.role).where(Member.user_id==u.id,Member.project==project)).scalar()
 if not role or write and role=='observer' or lead and role not in ('admin','lead'):raise HTTPException(403,'Недостаточно прав проекта')
 return role

def run_access(s,u,runid,ready=False):
 r=s.get(Run,runid)
 if not r:raise HTTPException(404,'Прогон не найден')
 access(s,u,r.project)
 if u.id==VIEWER_ID and not is_demo_run(r,demo_run(s)):raise HTTPException(404,'Прогон не найден')
 if ready and r.status!='completed':raise HTTPException(409,'Отчёт ещё не опубликован')
 return r
def run_artifact(run):
 path=storage_path(DATA,'artifacts',run.project,run.dataset,run.id)
 if run.status=='completed' and not path.is_dir():raise HTTPException(404,'Файлы отчёта отсутствуют в хранилище')
 return path

@contextmanager
def mart_for(u,runid,analysis=None):
 with Session() as s:r=run_access(s,u,runid,True);path=run_artifact(r)
 m=Mart(path)
 try:
  m.analysis=AnalysisScope(m,parse_options(analysis)) if analysis is not None else None
  yield m
 finally:m.close()

@app.get('/api/health')
def health():
 with Session() as s:return {'status':'ok','demo_login':bool(demo_run(s))}
@app.get('/api/ready')
def ready():
 with Session() as s:s.execute(select(User.id).limit(1))
 return {'status':'ready','code_isolation':capability()}

def open_session(s,u,request):
 token=secrets.token_urlsafe(40)
 s.add(Login(token=token_hash(token),user_id=u.id,expires=time.time()+12*3600))
 audit(s,u.id,'','login',detail={'demo':u.id==VIEWER_ID},request_id=request.state.request_id)
 return token

def login_response(response,u,token):
 response.set_cookie('spr_session',token,httponly=True,samesite='strict',secure=BASE_URL.startswith('https'),max_age=43200)
 return {'id':u.id,'name':u.name,'admin':u.admin,'isolation':capability(),'demo':u.id==VIEWER_ID}

@app.post('/api/login')
def login(body:dict,response:Response,request:Request):
 error='Неверное имя пользователя или пароль'
 with Session.begin() as s:
  recent=s.execute(select(Audit.id).where(Audit.action=='login_failed',Audit.object_id==request.client.host,Audit.created>time.time()-300)).all()
  if len(recent)>=10:raise HTTPException(429,'Слишком много попыток. Повторите через 5 минут.')
  name=body.get('name')
  u=s.execute(select(User).where(User.name==name)).scalar()
  password=str(body.get('password',''))
  valid=u and u.id!=VIEWER_ID and verify_password(password,u.password)
  if valid:token=open_session(s,u,request)
  else:
   audit(s,'','','login_failed',request.client.host,request_id=request.state.request_id)
   if name=='admin' and password=='admin' and demo_run(s):
    error='Для входа администратором нужен пароль сервера. Готовый демоотчёт открывается отдельной кнопкой.'
 if not valid:raise HTTPException(401,error)
 return login_response(response,u,token)

@app.post('/api/demo-login')
def demo_login(response:Response,request:Request):
 with Session.begin() as s:
  u=s.get(User,VIEWER_ID) if demo_run(s) else None
  if not u or u.admin:raise HTTPException(404,'Открытое демо сейчас недоступно')
  token=open_session(s,u,request)
 return login_response(response,u,token)

@app.post('/api/logout')
def logout(request:Request,response:Response,u=Depends(user)):
 with Session.begin() as s:s.execute(delete(Login).where(Login.token==token_hash(request.cookies.get('spr_session',''))))
 response.delete_cookie('spr_session');return {'ok':True}
@app.get('/api/me')
def me(u=Depends(user)):return {'id':u.id,'name':u.name,'admin':u.admin,'isolation':capability(),'demo':u.id==VIEWER_ID}
@app.get('/api/projects')
def projects(u=Depends(user)):
 with Session() as s:
  q=select(Record).where(Record.kind=='project')
  if u.id==VIEWER_ID:q=q.where(Record.id==demo_run(s).project)
  elif not u.admin:q=q.where(Record.id.in_(select(Member.project).where(Member.user_id==u.id)))
  return [{**serialize(r),'role':access(s,u,r.id)} for r in s.scalars(q)]
@app.post('/api/projects')
def project_new(body:dict,u=Depends(user)):
 if not u.admin:raise HTTPException(403)
 with Session.begin() as s:
  i=uid();s.add(Record(id=i,project=i,kind='project',name=str(body['name'])[:200],body={},author=u.id));s.add(Member(user_id=u.id,project=i,role='admin'));audit(s,u.id,i,'project_create',i)
 return {'id':i}
@app.get('/api/projects/{project}/records')
def records(project:str,kind:str='',u=Depends(user)):
 with Session() as s:
  access(s,u,project);q=select(Record).where(Record.project==project)
  if u.id==VIEWER_ID:q=q.where(Record.id==demo_run(s).dataset)
  if kind:q=q.where(Record.kind==kind)
  return [serialize(r) for r in s.scalars(q.order_by(Record.created))]
@app.post('/api/projects/{project}/records')
def record_save(project:str,body:dict,request:Request,u=Depends(user)):
 kind=body.get('kind')
 if kind not in ['dataset','profile','procedure','sequence','view','note','source']:raise HTTPException(422,'Неизвестный тип объекта')
 with Session.begin() as s:
  access(s,u,project,write=True,lead=kind in ['profile','procedure','sequence','source'])
  existing=s.get(Record,body.get('id',''))
  if existing and (existing.project!=project or existing.kind!=kind):raise HTTPException(403)
  r=existing or Record(id=uid(),project=project,kind=kind,author=u.id)
  previous=r.body
  r.name=str(body.get('name','Без названия'))[:200];r.body=body.get('body',{})
  if len(json.dumps(r.body))>500000:raise HTTPException(413)
  if kind=='procedure':
   if r.body.get('cells') is not None:
    cells=r.body['cells']
    if not isinstance(cells,list) or not cells or any(not isinstance(c,dict) or not isinstance(c.get('code'),str) for c in cells):raise HTTPException(422,'Неверные ячейки')
    r.body={**r.body,'code':'\n\n'.join(c['code'] for c in cells)}
   if r.body.get('type','check') not in ('check','parse','normalize','compare','facts'):raise HTTPException(422,'Неизвестный тип процедуры')
   try:ast.parse(r.body.get('code',''))
   except SyntaxError as e:raise HTTPException(422,str(e))
  s.add(r);audit(s,u.id,project,'update_'+kind if existing else 'create_'+kind,r.id,{'before':previous,'after':r.body} if kind not in ['source'] else {'name':r.name},request_id=request.state.request_id)
  return {'id':r.id}
@app.post('/api/projects/{project}/notebook')
def notebook_cell(project:str,body:dict,u=Depends(user)):
 import os
 from .notebook import execute as cell
 with Session() as s:access(s,u,project,lead=True)
 if os.environ.get('SPR_TRUSTED_LOCAL_CODE')!='1' and not (hosted_isolation() and capability()):
  raise HTTPException(409,'Изолированный сеанс Python недоступен')
 code=body.get('code','');session=body.get('session','')
 if not isinstance(code,str) or len(code)>100000 or not isinstance(session,str) or not session or len(session)>100:raise HTTPException(422,'Неверная ячейка')
 return cell((u.id,project),session,code,body.get('reset',False))
@app.get('/api/projects/{project}/files')
def files(project:str,dataset:str,summary:bool=False,u=Depends(user)):
 with Session() as s:
  access(s,u,project)
  if u.id==VIEWER_ID and demo_run(s).dataset!=dataset:raise HTTPException(404)
  filters=(File.project==project,File.dataset==dataset)
  if u.id==VIEWER_ID:filters+= (File.id.in_([f['id'] for f in demo_run(s).snapshot['files']]),)
  if summary:
   return dict(s.execute(select(File.source,func.count()).where(*filters).group_by(File.source)).all())
  return [{k:v for k,v in serialize(f).items() if k!='path'} for f in s.scalars(select(File).where(*filters).limit(10000))]
@app.post('/api/projects/{project}/upload')
async def upload(project:str,dataset:str=Form(...),source:str=Form(...),files:list[UploadFile]=Upload(...),u=Depends(user)):
 if source not in ('BEFORE','AFTER'):raise HTTPException(422,'Неверный срез')
 with Session() as s:
  access(s,u,project,write=True);ds=s.get(Record,dataset)
  if not ds or ds.project!=project or ds.kind!='dataset':raise HTTPException(404)
 folder=DATA/'sources'/project/dataset
 folder.mkdir(parents=True,exist_ok=True)
 saved=[];entries=[]
 try:
  for file in files:
   name=Path(file.filename.replace('\\','/')).name;fmt=Path(name).suffix[1:].lower()
   if fmt not in ('json','csv','xml'):raise HTTPException(422,'Поддерживаются JSON / XML / CSV')
   raw=await file.read(10*1024*1024+1)
   if len(raw)>10*1024*1024:raise HTTPException(413,'Максимум 10 МБ на документ')
   ident=uid();path=folder/ident;saved.append(path);path.write_bytes(raw)
   entries.append(File(id=ident,project=project,dataset=dataset,source=source,name=name,path=storage_ref('sources',project,dataset,ident).as_posix(),sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),format=fmt))
  with Session.begin() as s:
   s.add_all(entries)
   for f in entries:audit(s,u.id,project,'import',f.id,{'source':source,'name':f.name})
 except BaseException:
  for path in saved:path.unlink(missing_ok=True)
  raise
 return {'stored':len(entries)}
@app.get('/api/files/{fileid}')
def raw_file(fileid:str,u=Depends(user)):
 with Session.begin() as s:
  f=s.get(File,fileid)
  if not f:raise HTTPException(404)
  access(s,u,f.project)
  if u.id==VIEWER_ID and not any(item['id']==fileid for item in demo_run(s).snapshot['files']):
   raise HTTPException(404)
  audit(s,u.id,f.project,'source_access',f.id)
  try:raw=storage_path(DATA,'sources',f.project,f.dataset,f.id).read_bytes()
  except FileNotFoundError:raise HTTPException(404,'Исходный документ отсутствует в хранилище') from None
  except OSError:raise HTTPException(500,'Не удалось прочитать исходный документ') from None
 return Response(raw,media_type='text/plain; charset=utf-8')
@app.post('/api/projects/{project}/runs')
def start_run(project:str,body:dict,u=Depends(user)):
 with Session.begin() as s:
  access(s,u,project,write=True)
  dataset_id=body.get('dataset');profile_id=body.get('profile')
  ds=s.get(Record,dataset_id) if dataset_id else None
  profile=s.get(Record,profile_id) if profile_id else None
  if not ds or ds.project!=project or ds.kind!='dataset':raise HTTPException(422,'Выберите набор данных проекта')
  if profile_id and (not profile or profile.project!=project or profile.kind!='profile'):raise HTTPException(422,'Выберите модель проекта')
  model=profile.body if profile else json.loads((ROOT/'profiles/main.json').read_text(encoding='utf-8'))
  procedures={}
  for kind, ident in body.get('procedures',{}).items():
   if kind not in ('parse','normalize','compare','facts'):raise HTTPException(422,'Неизвестный этап')
   p=s.get(Record,ident)
   if not p or p.project!=project or p.kind!='procedure' or p.body.get('type')!=kind:raise HTTPException(422,'Неверная процедура этапа')
   procedures[kind]={'id':p.id,'name':p.name,**p.body}
  checks=[]
  for i in body.get('checks',[]):
   p=s.get(Record,i)
   if not p or p.project!=project or p.kind!='procedure' or p.body.get('type','check')!='check':raise HTTPException(422,'Неизвестная проверка')
   checks.append({'id':p.id,'name':p.name,**p.body})
  if (checks or procedures) and not capability():raise HTTPException(409,'Изоляция ОС не настроена: исполнение Python отключено')
  files=[serialize(f) for f in s.scalars(select(File).where(File.dataset==ds.id,File.project==project))]
  if not files:raise HTTPException(422,'В датасете нет файлов')
  ident=uid();snap={'profile':model,'profile_id':profile_id or None,'checks':checks,'procedures':procedures,'files':files,'sequence':['import','normalize','union','compare','segments','checks','facts','publish'],'dataset_name':ds.name,'builtin_code':{name:(ROOT/'spr'/name).read_text(encoding='utf-8') for name in ('normalize.py','pipeline.py','bucket_config.py','buckets.py')}}
  s.add(Run(id=ident,project=project,dataset=ds.id,author=u.id,snapshot=portable_snapshot(snap,project,ds.id)));audit(s,u.id,project,'run',ident,{'profile':profile_id or 'builtin','files':len(files)})
 return {'id':ident}
@app.post('/api/projects/{project}/demo-run')
def start_demo_run(project:str,u=Depends(user)):
 if u.id!=VIEWER_ID:raise HTTPException(403,'Демосравнение доступно из демовхода')
 with demo_queue_lock,Session.begin() as s:
  base=demo_run(s,lock=True)
  if not base or base.project!=project:raise HTTPException(404,'Демосравнение недоступно')
  latest=s.scalars(demo_repeats(base).order_by(Run.created.desc()).limit(1)).first()
  if latest and (latest.status in ('queued','running') or latest.created>time.time()-300):
   if latest.status in ('failed','cancelled'):
    raise HTTPException(409,'Не удалось завершить демосравнение. Повторите через несколько минут.')
   return {'id':latest.id,'status':latest.status}
  snapshot={**base.snapshot,'demo_base':base.id}
  if (snapshot.get('checks') or snapshot.get('procedures')) and not capability():
   raise HTTPException(409,'Для повтора этого демосравнения нужен доступный исполнитель Python')
  ident=uid()
  s.add(Run(id=ident,project=project,dataset=base.dataset,author=u.id,
            snapshot=portable_snapshot(snapshot,project,base.dataset)))
  audit(s,u.id,project,'demo_run',ident,{'base':base.id})
  return {'id':ident,'status':'queued'}
@app.get('/api/projects/{project}/runs')
def runs(project:str,u=Depends(user)):
 with Session() as s:
  access(s,u,project);query=select(Run).where(Run.project==project)
  if u.id==VIEWER_ID:
   base=demo_run(s);visible=[*s.scalars(demo_repeats(base).order_by(Run.created.desc()).limit(9)),base]
  else:visible=s.scalars(query.order_by(Run.created.desc()).limit(100))
  return [{**{k:v for k,v in serialize(r).items() if k not in ['snapshot','artifact']},
           'dataset_name':r.snapshot.get('dataset_name')} for r in visible]
@app.get('/api/runs/{runid}')
def get_run(runid:str,u=Depends(user)):
 with Session() as s:
  r=run_access(s,u,runid);out=serialize(r);out.pop('artifact',None);out['snapshot']={k:v for k,v in r.snapshot.items() if k!='files'}
  timings=run_artifact(r)/'timings.json'
  out['timings']=json.loads(timings.read_text(encoding='utf-8')) if r.status=='completed' and timings.is_file() else None
  return out
@app.post('/api/runs/{runid}/cancel')
def cancel(runid:str,u=Depends(user)):
 with Session.begin() as s:
  r=run_access(s,u,runid);access(s,u,r.project,write=True)
  if r.status not in ('queued','running'):raise HTTPException(409)
  r.status='cancelled';r.stage='Отменено';audit(s,u.id,r.project,'cancel',r.id)
 return {'ok':True}
@app.post('/api/runs/{runid}/retry')
def retry(runid:str,u=Depends(user)):
 with Session.begin() as s:
  r=run_access(s,u,runid);access(s,u,r.project,write=True);ident=uid();s.add(Run(id=ident,project=r.project,dataset=r.dataset,author=u.id,snapshot=portable_snapshot(r.snapshot,r.project,r.dataset)));audit(s,u.id,r.project,'retry',ident,{'base':runid})
 return {'id':ident}
@app.get('/api/runs/{runid}/overview')
def overview(runid:str,analysis:str|None=None,compact:bool=False,u=Depends(user)):
 with mart_for(u,runid,analysis) as m:
  transitions=m.query('SELECT DECISION_BEFORE,DECISION_AFTER,count(*) CNT FROM segments GROUP BY ALL ORDER BY CNT DESC')
  selection=m.analysis.summary() if m.analysis else None
  if compact:return {'selection':selection,'transitions':transitions}
  top=m.query('''WITH ranked AS (
   SELECT PATH,count(DISTINCT APP_ID) CNT FROM comparison WHERE DIFF_FLAG=1
   GROUP BY PATH ORDER BY CNT DESC,PATH LIMIT 15
  ) SELECT r.PATH,r.CNT,list_sort(list(DISTINCT c.APP_ID)) APP_IDS
    FROM ranked r JOIN comparison c ON c.PATH=r.PATH AND c.DIFF_FLAG=1
    GROUP BY r.PATH,r.CNT ORDER BY r.CNT DESC,r.PATH''')
  groups={}
  for row in top:
   group=groups.setdefault(tuple(row.pop('APP_IDS')),{'CNT':row['CNT'],'PATHS':[]})
   group['PATHS'].append(row['PATH'])
  external=m.query("SELECT count(DISTINCT APP_ID) CNT FROM comparison WHERE DIFF_FLAG=1 AND (starts_with(PATH,'ext_data') OR starts_with(PATH,'characteristics'))")[0]['CNT']
  unchanged=m.query('SELECT count(DISTINCT c.APP_ID) CNT FROM comparison c JOIN segments s USING(APP_ID) WHERE c.DIFF_FLAG=1 AND s.DECISION_CHANGED=0')[0]['CNT']
  return {'selection':selection,'analysis_meta':m.analysis.metadata() if m.analysis else None,'transitions':transitions,'top':top,'top_groups':list(groups.values()),'external':external,'changed_without_decision':unchanged,'impact':{dim:m.pivot([dim],{'DIFF_FLAG':1},1000) for dim in ['PRODUCT','PARTNER','ROUTING_METHOD']},'denominator':m.total_apps(),'recommendations':[{'observation':f'Внешние значения изменились у {external} заявок','confidence':'Наблюдение','hypothesis':'Изменение входных данных могло сопровождать изменение решения. Причинность не доказана.','next':'Сравните внешний источник, входное поле модели и решение.','filters':{'path_prefix':['ext_data','characteristics'],'diff_flag':1}},{'observation':f'{unchanged} заявок имеют различия без смены решения','filters':{'diff_flag':1,'DECISION_CHANGED':0},'confidence':'Подтверждено сравнением','next':'Проверьте изменения предложений, условий и структуры ответа.'}]}
@app.post('/api/runs/{runid}/pivot')
def pivot(runid:str,body:dict,u=Depends(user)):
 try:
  with mart_for(u,runid,body.get('analysis')) as m:return {'items':m.pivot(body.get('dimensions',FACT_DIMS[:8]),body.get('filters',{}),min(max(1,int(body.get('limit',250))),1000),max(0,int(body.get('offset',0)))),'measure':'count(distinct APP_ID)','denominator':m.total_apps()}
 except ValueError as e:raise HTTPException(422,str(e))
@app.post('/api/runs/{runid}/drill')
def drill(runid:str,body:dict,u=Depends(user)):
 with mart_for(u,runid,body.get('analysis')) as m:
  limit=min(max(1,int(body.get('limit',50))),200);offset=max(0,int(body.get('offset',0)));kind=body.get('kind','facts');filters=body.get('filters',{})
  if kind=='facts':return m.drill(filters,limit,offset)
  if kind=='ids':
   w,p=where(filters,{'app_ids':'APP_ID'})
   table='analysis_apps' if m.analysis else '(SELECT DISTINCT APP_ID FROM quality)'
   total=m.con.execute(f'SELECT count(*) FROM {table} WHERE '+w,p).fetchone()[0]
   return {'total':total,'items':m.query(f'SELECT APP_ID FROM {table} WHERE '+w+' ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset])}
  if kind=='quality':
   cols={'APP_ID':'APP_ID','STATUS':'STATUS','SOURCE':'SOURCE'};w,p=where(filters,cols)
   if body.get('excluded'):w+=' AND APP_ID NOT IN (SELECT APP_ID FROM '+('original_segments' if m.analysis else 'segments')+')'
   total=m.con.execute('SELECT count(DISTINCT APP_ID) FROM quality WHERE '+w,p).fetchone()[0]
   items=m.query('SELECT APP_ID,string_agg(DISTINCT STATUS,\', \') STATUS FROM quality WHERE '+w+' GROUP BY APP_ID ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset]);return {'total':total,'items':items}
  if kind=='segments':
   w,p=where(filters,{k:k for k in ['DECISION_BEFORE','DECISION_AFTER','DECISION_CHANGED','APP_ID']});
   if body.get('paired') and m.analysis:w+=' AND APP_ID IN (SELECT APP_ID FROM original_segments)'
   total=m.con.execute('SELECT count(*) FROM segments WHERE '+w,p).fetchone()[0]
   return {'total':total,'items':m.query('SELECT * FROM segments WHERE '+w+' ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset])}
  if kind=='products':
   w,p=where(filters,{k:k for k in ['PRODUCT','MODEL','SOURCE','DECISION','APP_ID']});total=m.con.execute('SELECT count(DISTINCT APP_ID) FROM products WHERE '+w,p).fetchone()[0]
   return {'total':total,'items':m.query('SELECT DISTINCT APP_ID FROM products WHERE '+w+' ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset])}
  raise HTTPException(422,'Неверный вид детализации')
@app.get('/api/runs/{runid}/apps/{appid}')
def application(runid:str,appid:str,analysis:str|None=None,u=Depends(user)):
 with mart_for(u,runid,analysis) as m:
  return {'scorecards':[r for r in m.analysis.rows if r['APP_ID']==appid] if m.analysis else [],'comparison':m.query('SELECT * FROM comparison WHERE APP_ID=? ORDER BY PATH,KEY_JSON',[appid]),'quality':m.query('SELECT * FROM quality WHERE APP_ID=? ORDER BY SOURCE',[appid]),'segments':m.query('SELECT * FROM segments WHERE APP_ID=?',[appid]),'products':m.query('SELECT * FROM products WHERE APP_ID=? ORDER BY PRODUCT,SOURCE',[appid])}
@lru_cache(maxsize=4)
def published_checks(artifact):
 return [row for name in ('buckets.json','checks.json','check_errors.json')
         if (path:=Path(artifact)/name).is_file() for row in json.loads(path.read_bytes())]

@lru_cache(maxsize=4)
def selection_buckets(artifact,settings):
 with Mart(artifact) as m:
  AnalysisScope(m,parse_options(settings))
  return m.bucket_report()

@app.get('/api/runs/{runid}/checks')
def checks(runid:str,analysis:str|None=None,u=Depends(user)):
 with Session() as s:artifact=run_artifact(run_access(s,u,runid,True))
 if analysis is None:return published_checks(artifact)
 options=parse_options(analysis)
 if not options.filtered and options.basis=='historical':
  buckets=[row for row in published_checks(artifact) if row.get('LABEL') in ('BUCKET_DISTRIBUTION','BUCKET_TRANSITION') and row.get('REPORT_VERSION')==2]
  if buckets:return buckets
 return selection_buckets(artifact,options.model_dump_json())

@app.post('/api/runs/{runid}/scoped-checks')
def scoped_checks(runid:str,body:dict,request:Request,u=Depends(user)):
 options=parse_options(body.get('analysis',{}))
 with Session.begin() as s:
  r=run_access(s,u,runid,True);access(s,u,r.project,write=True)
  artifact=run_artifact(r);procedures=r.snapshot.get('checks',[])
  audit(s,u.id,r.project,'scoped_checks',runid,request_id=request.state.request_id)
 if procedures and not capability():raise HTTPException(409,'Исполнение Python отключено')
 if not options.filtered and options.basis=='historical':
  return [r for r in published_checks(artifact) if r.get('LABEL') not in ('BUCKET_DISTRIBUTION','BUCKET_TRANSITION')]
 rows=[]
 for check in procedures:
  result=execute(check['code'],check.get('params',{}),artifact,data={'_analysis_scope':options.model_dump(mode='json')})
  if result.get('error'):
   rows.append({'CHECK_ID':check.get('id'),'LABEL':check['name'],'RESULT':'ERROR','ERROR':result['error'],'APP_IDS':[]})
  else:
   for row in result['rows']:
    rows.append({**row,'CHECK_ID':check.get('id'),'APP_IDS_LIMITED':len(row['APP_IDS'])<row['CNT']})
 return rows

@app.post('/api/runs/{runid}/test-procedure')
def test_procedure(runid:str,body:dict,u=Depends(user)):
 with Session.begin() as s:
  r=run_access(s,u,runid,True);access(s,u,r.project,lead=True);path=run_artifact(r);audit(s,u.id,r.project,'procedure_preview',r.id)
 kind=body.get('type','check')
 if kind not in ('check','parse','normalize','compare','facts'):raise HTTPException(422,'Неизвестный тип')
 data=body.get('data')
 if kind in ('compare','facts'):
  import polars as pl
  data=pl.read_parquet(Path(path)/({'normalize':'normalized','compare':'normalized','facts':'membership'}[kind]+'.parquet')).head(100).to_dicts()
 if kind=='compare':data={'rows':data,'valid_ids':sorted({row['APP_ID'] for row in data}),'profile':r.snapshot['profile']}
 if kind in ('parse','normalize') and data is None:raise HTTPException(422,'Укажите тестовый JSON в поле входных данных')
 return execute(body['code'],body.get('params',{}),path,kind,data)
@app.get('/api/runs/{runid}/scorecards')
def scorecards(runid:str,analysis:str|None=None,u=Depends(user)):
 with mart_for(u,runid,analysis) as m:
  bucket='BUCKET' if m.analysis else 'floor(SCORE*10)/10'
  return {'groups':m.query('SELECT MODEL,PRODUCT,SOURCE,count(DISTINCT APP_ID) CNT,min(SCORE) MIN,avg(SCORE) AVG,max(SCORE) MAX,min(CUTOFF) CUTOFF_MIN,max(CUTOFF) CUTOFF_MAX FROM products GROUP BY ALL ORDER BY MODEL,PRODUCT,SOURCE'),'distribution':m.query(f'SELECT MODEL,SOURCE,{bucket} BUCKET,count(DISTINCT APP_ID) CNT FROM products WHERE SCORE IS NOT NULL GROUP BY ALL ORDER BY MODEL,SOURCE,BUCKET'),'features':m.query("SELECT PATH,ARRAY_KEY,CHANGE_TYPE,count(DISTINCT APP_ID) CNT FROM comparison WHERE starts_with(PATH,'decisionResult.scoring') GROUP BY ALL ORDER BY PATH,ARRAY_KEY")}
@app.post('/api/runs/{runid}/scenario')
def scenario(runid:str,body:dict,u=Depends(user)):
 cutoff=float(body.get('cutoff',0.55));direction=body.get('direction','ge');product=str(body.get('product',''))
 if not 0<=cutoff<=1 or direction not in ['ge','le']:raise HTTPException(422)
 with Session() as s:r=run_access(s,u,runid,True);access(s,u,r.project,write=True);project=r.project
 with mart_for(u,runid,body.get('analysis')) as m:
  rows=m.query('SELECT * FROM products WHERE SOURCE=\'AFTER\' AND PRODUCT=? ORDER BY APP_ID',[product])
  if not rows:raise HTTPException(422,'Для продукта нет баллов и порогов')
  eligible=[r for r in rows if r['SCORE'] is not None and r['CUTOFF'] is not None and not r.get('STOP_UNKNOWN',True)]
  for r in eligible:r['SIMULATED']='APPROVE' if not r['STOP'] and (r['SCORE']>=cutoff if direction=='ge' else r['SCORE']<=cutoff) else 'REJECT'
  changed=[r for r in eligible if r['SIMULATED']!=r['DECISION']]
  result={'base':runid,'cutoff':cutoff,'direction':direction,'product':product,'total':len(eligible),'excluded':len(rows)-len(eligible),'changed':len(changed),'items':changed,'simulation':True,'analysis':body.get('analysis'),'limitations':'Симуляция одного продуктового порога и hit=true. Это не ответ банковской СПР; остальные правила и офферы не пересчитываются.','code':'APPROVE if not STOP and compare(SCORE, cutoff, direction) else REJECT'}
 with Session.begin() as s:
  ident=uid();s.add(Record(id=ident,project=project,kind='scenario',name='Порог '+str(cutoff),body=result,author=u.id));audit(s,u.id,project,'scenario',ident,{'base':runid,'cutoff':cutoff})
 return {'id':ident,**result}
@app.get('/api/projects/{project}/audit')
def history(project:str,u=Depends(user)):
 with Session() as s:
  access(s,u,project,lead=True);return [serialize(r) for r in s.scalars(select(Audit).where(Audit.project==project).order_by(Audit.created.desc()).limit(200))]
@app.get('/api/projects/{project}/members')
def members(project:str,u=Depends(user)):
 with Session() as s:
  access(s,u,project,lead=True);return [{'user_id':m.user_id,'name':usr.name,'role':m.role} for m,usr in s.execute(select(Member,User).join(User,User.id==Member.user_id).where(Member.project==project))]
@app.post('/api/projects/{project}/members')
def add_member(project:str,body:dict,u=Depends(user)):
 with Session.begin() as s:
  role=access(s,u,project,lead=True)
  if role!='admin':raise HTTPException(403)
  if body['role'] not in ['lead','analyst','observer']:raise HTTPException(422)
  usr=s.execute(select(User).where(User.name==body['name'])).scalar()
  if not usr:usr=User(id=uid(),name=body['name'],password=hash_password(body['password']));s.add(usr)
  m=s.execute(select(Member).where(Member.project==project,Member.user_id==usr.id)).scalar()
  if m:m.role=body['role']
  else:s.add(Member(user_id=usr.id,project=project,role=body['role']))
  audit(s,u.id,project,'access_change',usr.id,{'role':body['role']})
 return {'ok':True}
@app.get('/api/runs/{runid}/export')
def export(runid:str,format:str='html',filters:str='{}',dimensions:str='',analysis:str|None=None,flat:bool=False,u=Depends(user)):
 from .exports import FACT_DIMS as canonical_dims, fact_values
 selected_filters=json.loads(filters);selected_dims=json.loads(dimensions) if dimensions else FACT_DIMS[:8]
 if not isinstance(selected_filters,dict) or not isinstance(selected_dims,list):raise ValueError('Invalid export filters or dimensions')
 with Session.begin() as s:
  r=run_access(s,u,runid,True);artifact=run_artifact(r);audit(s,u.id,r.project,'export',runid,{'format':format})
 if format=='checks-html':
  from .exports import check_sections, checks_page_url, write_html
  stream=io.BytesIO()
  sections=check_sections(published_checks(artifact),checks_page_url(BASE_URL,runid))
  write_html(stream,sections,r,BASE_URL,checks_only=True)
  return HTMLResponse(stream.getvalue(),headers={'Content-Disposition':f'attachment; filename="scorify-checks-{runid}.html"'})
 if format in ('html','xlsx','xml'):
  from itertools import chain
  from tempfile import SpooledTemporaryFile
  from .exports import pivot_sections, report_sections, write_excel, write_html, write_xml
  stream=SpooledTemporaryFile(max_size=1024*1024)
  try:
   with mart_for(u,runid,analysis or '{}') as m:
    facts=m.pivot(list(canonical_dims),selected_filters,limit=100001)
    if len(facts)>100000:raise HTTPException(413,'Более 100000 групп: сузьте фильтры сводной')
    pivot=None
    if dimensions and tuple(selected_dims)!=canonical_dims and format!='xml':
     rows=m.pivot(selected_dims,selected_filters,limit=100001)
     if len(rows)>100000:raise HTTPException(413,'Более 100000 групп: сузьте фильтры сводной')
     pivot=(selected_dims,selected_filters,rows)
    checks=published_checks(artifact)
    sections=report_sections(m,r,checks,pivot,selected_filters)
    if format=='xlsx':write_excel(stream,chain(pivot_sections(facts,excel=True),sections))
    elif format=='xml':write_xml(stream,m,r,facts,checks,selected_filters)
    else:write_html(stream,chain(sections,pivot_sections(facts)),r,BASE_URL)
   length=stream.tell()
   stream.seek(0)
  except BaseException:
   stream.close()
   raise
  def chunks():
   with stream:
    yield from iter(lambda:stream.read(65536),b'')
  media={'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
         'html':'text/html; charset=utf-8','xml':'application/xml'}[format]
  return StreamingResponse(chunks(),media_type=media,headers={
   'Content-Disposition':f'attachment; filename="scorify-{runid}.{format}"',
   'Content-Length':str(length),
  })
 if flat:selected_dims=list(canonical_dims)
 with mart_for(u,runid,analysis) as m:rows=m.pivot(selected_dims,selected_filters,limit=100001)
 if len(rows)>100000:raise HTTPException(413,'Более 100000 групп: сузьте фильтры экспорта')
 if flat:rows=[fact_values(row,json_types=format=='json') for row in rows]
 if format=='json' and flat:return Response(json.dumps(rows,ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="facts.json"'})
 if format=='json':return Response(json.dumps({'schema':'pivot facts v1; CNT=distinct APP_ID per group; totals overlap','run':runid,'dimensions':selected_dims,'filters':selected_filters,'analysis':json.loads(analysis) if analysis is not None else None,'items':rows},ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="facts.json"'})
 if format=='csv':
  stream=io.StringIO();w=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else selected_dims+['CNT'],delimiter=';');w.writeheader()
  for r in rows:w.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v) for k,v in r.items()})
  return Response(('\ufeff'+stream.getvalue()).encode(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="facts.csv"'})
 raise HTTPException(422)

# Registration grants no project privileges; an administrator approves access.
@app.post('/api/register')
def register(body:dict,request:Request):
 name=str(body.get('name','')).strip()
 if not 3<=len(name)<=80:raise ValueError('Логин: от 3 до 80 символов')
 password=hash_password(str(body.get('password','')))
 with Session.begin() as s:
  if s.scalar(select(User.id).where(User.name==name)):raise HTTPException(409,'Логин уже занят')
  s.add(User(id=uid(),name=name,password=password,admin=0))
  audit(s,'','','register',name,request_id=request.state.request_id)
 return {'ok':True,'message':'Аккаунт создан. Администратор назначит доступ.'}

@app.get('/api/admin/users')
def admin_users(u=Depends(user)):
 if not u.admin:raise HTTPException(403)
 with Session() as s:return [{**serialize(usr),'memberships':[serialize(m) for m in s.scalars(select(Member).where(Member.user_id==usr.id))]} for usr in s.scalars(select(User).order_by(User.name))]

@app.post('/api/admin/users/{ident}')
def admin_update(ident:str,body:dict,request:Request,u=Depends(user)):
 if not u.admin:raise HTTPException(403)
 with Session.begin() as s:
  target=s.get(User,ident)
  if not target:raise HTTPException(404)
  if target.id==VIEWER_ID:raise HTTPException(403,'Права публичного демовхода задаются настройками сервера')
  if 'admin' in body:
   if ident==u.id and not body['admin']:raise ValueError('Нельзя снять собственные права администратора')
   target.admin=int(bool(body['admin']))
  project=body.get('project');role=body.get('role')
  if project:
   record=s.get(Record,project)
   if not record or record.kind!='project':raise ValueError('Проект не найден')
   if role not in ('lead','analyst','observer','none'):raise ValueError('Неизвестная роль')
   m=s.scalar(select(Member).where(Member.user_id==ident,Member.project==project))
   if role=='none':
    if m:s.delete(m)
   elif m:m.role=role
   else:s.add(Member(user_id=ident,project=project,role=role))
  if ident!=u.id:s.execute(delete(Login).where(Login.user_id==ident))
  audit(s,u.id,project or '','access_change',ident,{'admin':target.admin,'role':role},request_id=request.state.request_id)
 return {'ok':True}

@app.get('/api/runs/{runid}/factor-changes')
def factor_changes(runid:str,analysis:str|None=None,u=Depends(user)):
 with mart_for(u,runid,analysis) as m:
  rows=m.query("SELECT * FROM products WHERE APP_ID IN (SELECT APP_ID FROM segments WHERE DECISION_CHANGED=1) ORDER BY APP_ID,PRODUCT,SOURCE")
  profile=m.analysis.profile if m.analysis else json.loads((m.path/'profile.json').read_bytes())
  offers=profile.get('offers_path','decisionResult.available_offers')
  names={r['PRODUCT']:r['NAME'] for r in m.query('''
    SELECT id.VALUE PRODUCT,min(trim(n.VALUE)) NAME FROM normalized id
    JOIN normalized n USING (APP_ID,SOURCE,KEY_JSON)
    WHERE id.PATH=? AND n.PATH=? AND n.TYPE='str' AND trim(n.VALUE)<>''
    GROUP BY id.VALUE HAVING count(DISTINCT trim(n.VALUE))=1''',
    [offers+'.product_id',offers+'.product_name'])}
 groups={};pairs={}
 for row in rows:pairs.setdefault((row['APP_ID'],row['PRODUCT']),{})[row['SOURCE']]=row
 for (app,product),pair in pairs.items():
  before=pair.get('BEFORE');after=pair.get('AFTER');reasons=[]
  if not before or not after:reasons=['Продукт добавлен или удалён']
  else:
   if before.get('STOP_UNKNOWN',True) or after.get('STOP_UNKNOWN',True):reasons.append('Стоп-факторы: недостаточно данных')
   elif before['STOP']!=after['STOP']:reasons.append('Новый стоп-фактор' if after['STOP'] else 'Снят стоп-фактор')
   for field,label in [('SCORE','Изменился балл'),('CUTOFF','Изменился порог'),('MODEL','Изменилась модель')]:
    if before[field]!=after[field]:reasons.append(label)
   if not reasons:reasons=['Другие атрибуты или правила']
  for reason in reasons:
   group=groups.setdefault((product,reason),{'PRODUCT':product,'PRODUCT_NAME':names.get(product),'FACTOR':reason,'APP_IDS':set(),'DETAILS':[]})
   group['APP_IDS'].add(app);group['DETAILS'].append({'APP_ID':app,'BEFORE':before,'AFTER':after})
 result=[]
 for group in groups.values():
  group['APP_IDS']=sorted(group['APP_IDS']);group['CNT']=len(group['APP_IDS']);result.append(group)
 return {'items':result,'note':'Факторы, изменившиеся вместе с итоговым решением. Несколько факторов могут относиться к одной заявке; причинность не установлена.'}

@app.get('/api/runs/{runid}/decision-factors')
def decision_factors(runid:str,direction:str='',u=Depends(user)):
 if direction and direction not in DIRECTIONS:raise ValueError('Выберите направление порога')
 with mart_for(u,runid) as m:
  rows=m.query('SELECT * FROM products ORDER BY APP_ID,PRODUCT,SOURCE')
  config=json.loads((m.path/'profile.json').read_bytes()).get('decision_analysis',{})
 for r in rows:
  rule=model_direction(config,r['MODEL'],direction)
  if r['STOP']:result,reason='REJECT','STOP_FACTOR'
  elif r['CUTOFF'] is None or r['SCORE'] is None or r.get('STOP_UNKNOWN',True):result,reason='UNKNOWN','INCOMPLETE'
  elif rule is None:result,reason='UNKNOWN','DIRECTION_UNSET'
  elif DIRECTIONS[rule](r['SCORE']-r['CUTOFF']):result,reason='APPROVE','SCORE_PASS'
  else:result,reason='REJECT','SCORE_CUTOFF'
  r.update(EXPECTED=result,REASON=reason,DIRECTION=rule)
 return {'items':rows,'definition':'Проверка одного порога и стоп-факторов, не восстановление всей стратегии'}

@app.post('/api/projects/{project}/pools')
def prepare_pool(project:str,body:dict,u=Depends(user)):
 rows=body.get('rows',[])
 if not isinstance(rows,list) or not 1<=len(rows)<=2000:raise ValueError('Загрузите от 1 до 2000 заявок')
 if not isinstance(body.get('filters',{}),dict):raise ValueError('Фильтры должны быть объектом')
 if len(json.dumps(rows))>4000000:raise ValueError('Пул слишком большой: максимум 4 МБ')
 ids=set();selected=[]
 for row in rows:
  if not isinstance(row,dict) or not str(row.get('id','')).strip():raise ValueError('У каждой заявки нужен id')
  ident=str(row['id'])
  if any(c in ident for c in '/\\') or ident in ('.','..'):raise ValueError('Недопустимый id')
  if ident in ids:raise ValueError('Повторный id: '+ident)
  ids.add(ident)
  if all(row.get(k)==v for k,v in body.get('filters',{}).items()):selected.append(row)
 with Session.begin() as s:
  access(s,u,project,lead=True);ident=uid()
  s.add(Record(id=ident,project=project,kind='pool',name=str(body.get('name','Пул заявок'))[:200],author=u.id,body={'rows':selected,'filters':body.get('filters',{}),'input_count':len(rows)}))
  audit(s,u.id,project,'pool_prepare',ident,{'count':len(selected)})
 return {'id':ident,'count':len(selected)}

@app.post('/api/projects/{project}/pools/{poolid}/execute')
def execute_pool(project:str,poolid:str,body:dict,u=Depends(user)):
 from .decision import decide
 from .normalize import get
 with Session() as s:
  access(s,u,project,lead=True);pool=s.get(Record,poolid)
  if not pool or pool.project!=project or pool.kind!='pool':raise HTTPException(404)
  rows=pool.body['rows']
 if not rows:raise ValueError('Пул пуст')
 versions=body.get('versions',{})
 if set(versions)!=set(['BEFORE','AFTER']):raise ValueError('Нужны конфигурации BEFORE и AFTER')
 mode=body.get('mode','demo')
 if mode not in ('demo','http'):raise ValueError('Неизвестный исполнитель')
 if mode=='http':
  import os,httpx
  urls={src:os.environ.get('SPR_'+src+'_URL') for src in versions}
  if not all(urls.values()):raise HTTPException(409,'Настройте SPR_BEFORE_URL и SPR_AFTER_URL на сервере')
  if len(rows)>100:raise ValueError('HTTP-прогон: максимум 100 заявок за запуск')
 documents=[]
 for source,config in versions.items():
  for row in rows:
   if mode=='http':
    try:
     with httpx.Client(timeout=15,follow_redirects=False) as client:
      response=client.post(urls[source],json={'application':row,'version':config},headers={'Authorization':'Bearer '+os.environ.get('SPR_'+source+'_TOKEN','')})
      response.raise_for_status();doc=response.json()
     if not isinstance(doc,dict) or get(doc,'appData.application_id')!=str(row['id']):raise ValueError('Ответ СПР должен содержать appData.application_id исходной заявки')
    except Exception:raise HTTPException(502,'Ошибка подключения или контракта ответа СПР; датасет не опубликован')
    documents.append((source,str(row['id']),doc));continue
   stops=row.get('stops',[])
   if not isinstance(stops,list) or any(v is not None and not isinstance(v,bool) for v in stops):raise ValueError('stops: массив true/false/null')
   decision,reason=decide(row.get('score'),stops,config.get('cutoff',.5),config.get('direction','le'))
   documents.append((source,str(row['id']),{'appData':{'application_id':str(row['id'])},'decisionResult':{'decision':decision,'product_trials':[{'product_id':str(row.get('product','demo')),'decision':decision,'rejectReason':reason,'scoring':{'pd':{'modelName':'demo-score','value':row.get('score')}},'cutOff':{'value':config.get('cutoff',.5)},'rules':[{'name':'stop_'+str(i),'hit':v} for i,v in enumerate(stops)]}]}}))
 dataset=uid();entries=[]
 for source,appid,doc in documents:
  ident=uid();path=DATA/'sources'/project/dataset/ident;path.parent.mkdir(parents=True,exist_ok=True);raw=json.dumps(doc).encode();path.write_bytes(raw)
  entries.append(File(id=ident,project=project,dataset=dataset,source=source,name=appid+'.json',path=storage_ref('sources',project,dataset,ident).as_posix(),sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),format='json'))
 with Session.begin() as s:
  s.add(Record(id=dataset,project=project,kind='dataset',name=('Симуляция · ' if mode=='demo' else 'СПР · ')+pool.name,author=u.id,body={'simulation':mode=='demo','pool':poolid,'versions':versions,'engine':'demo-score-v1' if mode=='demo' else 'http-adapter-v1','limitations':'Демонстрационная СПР: стоп-факторы и один скор, не банковская стратегия'}));s.add_all(entries);audit(s,u.id,project,'pool_execute',dataset,{'pool':poolid,'simulation':mode=='demo'})
 return {'id':dataset,'simulation':mode=='demo','files':len(entries)}

@app.get('/api/runs/{runid}/decision-analysis')
def decision_analysis(runid:str,u=Depends(user)):
 with mart_for(u,runid) as m:
  return {'items':m.query('SELECT * FROM products ORDER BY APP_ID,PRODUCT,SOURCE'), 'decisions':m.query('SELECT APP_ID,DECISION_BEFORE,DECISION_AFTER,REJECT_REASON_BEFORE,REJECT_REASON_AFTER FROM segments ORDER BY APP_ID')}
@app.post('/api/runs/{runid}/decision-analysis')
def filtered_decision_analysis(runid:str,body:dict,u=Depends(user)):
 with mart_for(u,runid,body.get('analysis') if body.get('analysis') is not None else {}) as m:
  return m.analysis.decision_page(max(0,int(body.get('offset',0))),min(200,max(0,int(body.get('limit',25)))),body.get('passes_only') is True)

@app.get('/api/runs/{runid}/quality-summary')
def quality_summary(runid:str,analysis:str|None=None,u=Depends(user)):
 with mart_for(u,runid,analysis) as m:return m.query('SELECT SOURCE,STATUS,count(*) CNT FROM quality GROUP BY ALL ORDER BY SOURCE,STATUS')

if (ROOT/'frontend/dist/assets').exists():app.mount('/assets',StaticFiles(directory=ROOT/'frontend/dist/assets'),name='assets')
@app.get('/{path:path}')
def spa(path:str):
 if path.startswith('api/'):raise HTTPException(404)
 p=ROOT/'frontend/dist/index.html'
 return FileResponse(p) if p.exists() else HTMLResponse('Соберите фронтенд: cd frontend && npm ci && npm run build',503)
