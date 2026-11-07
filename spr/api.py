import time, json, secrets, hashlib, html, csv, io, ast
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File as Upload, Form, Depends
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete
from .config import ROOT,DATA,BASE_URL,MATERIALS
from .db import Session,User,Login,Member,Record,Run,File,Audit,uid,audit,serialize
from .security import verify_password,token_hash,hash_password
from .analytics import Mart,where,FACT_DIMS
from .runner import capability,execute
app=FastAPI(title='Скорифай · анализ изменений СПР',version='1.0.0')

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
  if not u:raise HTTPException(401)
  return u

def access(s,u,project,write=False,lead=False):
 role='admin' if u.admin else s.execute(select(Member.role).where(Member.user_id==u.id,Member.project==project)).scalar()
 if not role or write and role=='observer' or lead and role not in ('admin','lead'):raise HTTPException(403,'Недостаточно прав проекта')
 return role

def run_access(s,u,runid,ready=False):
 r=s.get(Run,runid)
 if not r:raise HTTPException(404,'Прогон не найден')
 access(s,u,r.project)
 if ready and r.status!='completed':raise HTTPException(409,'Отчёт ещё не опубликован')
 return r
@contextmanager
def mart_for(u,runid):
 with Session() as s:r=run_access(s,u,runid,True);path=r.artifact
 m=Mart(path)
 try:yield m
 finally:m.close()

@app.get('/api/health')
def health():return {'status':'ok'}
@app.get('/api/ready')
def ready():
 with Session() as s:s.execute(select(User.id).limit(1))
 return {'status':'ready','code_isolation':capability()}
@app.post('/api/login')
def login(body:dict,response:Response,request:Request):
 with Session.begin() as s:
  recent=s.execute(select(Audit.id).where(Audit.action=='login_failed',Audit.object_id==request.client.host,Audit.created>time.time()-300)).all()
  if len(recent)>=10:raise HTTPException(429,'Слишком много попыток. Повторите через 5 минут.')
  u=s.execute(select(User).where(User.name==body.get('name'))).scalar()
  valid=u and verify_password(str(body.get('password','')),u.password)
  if not valid:audit(s,'','', 'login_failed',request.client.host,request_id=request.state.request_id)
  else:
   token=secrets.token_urlsafe(40);s.add(Login(token=token_hash(token),user_id=u.id,expires=time.time()+12*3600));audit(s,u.id,'','login',request_id=request.state.request_id)
 if not valid:raise HTTPException(401,'Неверный логин или пароль')
 response.set_cookie('spr_session',token,httponly=True,samesite='strict',secure=BASE_URL.startswith('https'),max_age=43200)
 return {'id':u.id,'name':u.name,'admin':u.admin,'isolation':capability()}
@app.post('/api/logout')
def logout(request:Request,response:Response,u=Depends(user)):
 with Session.begin() as s:s.execute(delete(Login).where(Login.token==token_hash(request.cookies.get('spr_session',''))))
 response.delete_cookie('spr_session');return {'ok':True}
@app.get('/api/me')
def me(u=Depends(user)):return {'id':u.id,'name':u.name,'admin':u.admin,'isolation':capability()}
@app.get('/api/projects')
def projects(u=Depends(user)):
 with Session() as s:
  q=select(Record).where(Record.kind=='project')
  if not u.admin:q=q.where(Record.id.in_(select(Member.project).where(Member.user_id==u.id)))
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
   try:ast.parse(r.body.get('code',''))
   except SyntaxError as e:raise HTTPException(422,str(e))
  s.add(r);audit(s,u.id,project,'update_'+kind if existing else 'create_'+kind,r.id,{'before':previous,'after':r.body} if kind not in ['source'] else {'name':r.name},request_id=request.state.request_id)
  return {'id':r.id}
@app.get('/api/projects/{project}/files')
def files(project:str,dataset:str,u=Depends(user)):
 with Session() as s:
  access(s,u,project);return [{k:v for k,v in serialize(f).items() if k!='path'} for f in s.scalars(select(File).where(File.project==project,File.dataset==dataset).limit(10000))]
@app.post('/api/projects/{project}/upload')
async def upload(project:str,dataset:str=Form(...),source:str=Form(...),files:list[UploadFile]=Upload(...),u=Depends(user)):
 if source not in ('BEFORE','AFTER'):raise HTTPException(422,'Неверный срез')
 with Session() as s:
  access(s,u,project,write=True);ds=s.get(Record,dataset)
  if not ds or ds.project!=project or ds.kind!='dataset':raise HTTPException(404)
 count=0
 for file in files:
  name=Path(file.filename.replace('\\','/')).name;fmt=Path(name).suffix[1:].lower()
  if fmt not in ['json','csv','xml']:raise HTTPException(422,'Поддерживаются JSON / XML / CSV')
  raw=await file.read(10*1024*1024+1)
  if len(raw)>10*1024*1024:raise HTTPException(413,'Максимум 10 МБ на документ')
  ident=uid();folder=DATA/'sources'/project/dataset;folder.mkdir(parents=True,exist_ok=True);path=folder/ident;path.write_bytes(raw)
  with Session.begin() as s:s.add(File(id=ident,project=project,dataset=dataset,source=source,name=name,path=str(path),sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),format=fmt));audit(s,u.id,project,'import',ident,{'source':source,'name':name})
  count+=1
 return {'stored':count}
@app.get('/api/files/{fileid}')
def raw_file(fileid:str,u=Depends(user)):
 with Session.begin() as s:
  f=s.get(File,fileid)
  if not f:raise HTTPException(404)
  access(s,u,f.project);audit(s,u.id,f.project,'source_access',f.id)
  return Response(Path(f.path).read_bytes(),media_type='text/plain; charset=utf-8')
@app.post('/api/projects/{project}/runs')
def start_run(project:str,body:dict,u=Depends(user)):
 with Session.begin() as s:
  access(s,u,project,write=True)
  ds=s.get(Record,body.get('dataset'));profile=s.get(Record,body.get('profile'))
  if not ds or not profile or ds.project!=project or profile.project!=project or ds.kind!='dataset' or profile.kind!='profile':raise HTTPException(422,'Выберите датасет и профиль проекта')
  checks=[]
  for i in body.get('checks',[]):
   p=s.get(Record,i)
   if not p or p.project!=project or p.kind!='procedure':raise HTTPException(422,'Неизвестная процедура')
   checks.append({'id':p.id,'name':p.name,**p.body})
  if checks and not capability():raise HTTPException(409,'Изоляция ОС не настроена: исполнение Python отключено')
  files=[serialize(f) for f in s.scalars(select(File).where(File.dataset==ds.id,File.project==project))]
  if not files:raise HTTPException(422,'В датасете нет файлов')
  ident=uid();snap={'profile':profile.body,'profile_id':profile.id,'checks':checks,'files':files,'sequence':['import','normalize','union','compare','segments','checks','facts','publish'],'dataset_name':ds.name}
  s.add(Run(id=ident,project=project,dataset=ds.id,author=u.id,snapshot=snap));audit(s,u.id,project,'run',ident,{'profile':profile.id,'files':len(files)})
 return {'id':ident}
@app.get('/api/projects/{project}/runs')
def runs(project:str,u=Depends(user)):
 with Session() as s:
  access(s,u,project);return [{k:v for k,v in serialize(r).items() if k not in ['snapshot','artifact']} for r in s.scalars(select(Run).where(Run.project==project).order_by(Run.created.desc()).limit(100))]
@app.get('/api/runs/{runid}')
def get_run(runid:str,u=Depends(user)):
 with Session() as s:
  r=run_access(s,u,runid);out=serialize(r);out.pop('artifact',None);out['snapshot']={k:v for k,v in r.snapshot.items() if k!='files'}
  out['timings']=json.loads((Path(r.artifact)/'timings.json').read_text(encoding='utf-8')) if r.status=='completed' and r.artifact and (Path(r.artifact)/'timings.json').is_file() else None
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
  r=run_access(s,u,runid);access(s,u,r.project,write=True);ident=uid();s.add(Run(id=ident,project=r.project,dataset=r.dataset,author=u.id,snapshot=r.snapshot));audit(s,u.id,r.project,'retry',ident,{'base':runid})
 return {'id':ident}
@app.get('/api/runs/{runid}/overview')
def overview(runid:str,u=Depends(user)):
 with mart_for(u,runid) as m:
  transitions=m.query('SELECT DECISION_BEFORE,DECISION_AFTER,count(*) CNT FROM segments GROUP BY ALL ORDER BY CNT DESC')
  top=m.query('SELECT PATH,count(DISTINCT APP_ID) CNT FROM comparison WHERE DIFF_FLAG=1 GROUP BY PATH ORDER BY CNT DESC,PATH LIMIT 15')
  external=m.query("SELECT count(DISTINCT APP_ID) CNT FROM comparison WHERE DIFF_FLAG=1 AND (starts_with(PATH,'ext_data') OR starts_with(PATH,'characteristics'))")[0]['CNT']
  unchanged=m.query('SELECT count(DISTINCT c.APP_ID) CNT FROM comparison c JOIN segments s USING(APP_ID) WHERE c.DIFF_FLAG=1 AND s.DECISION_CHANGED=0')[0]['CNT']
  return {'transitions':transitions,'top':top,'external':external,'changed_without_decision':unchanged,'denominator':m.total_apps(),'recommendations':[{'observation':f'Внешние значения изменились у {external} заявок','confidence':'Наблюдение','hypothesis':'Изменение входных данных могло сопровождать изменение решения. Причинность не доказана.','next':'Сравните источник, предиктор соответствующей модели и решение.','filters':{'path_prefix':['ext_data','characteristics'],'diff_flag':1}},{'observation':f'{unchanged} заявок имеют различия без смены решения','confidence':'Подтверждено сравнением','next':'Проверьте изменения офферов, условий и структуры.'}]}
@app.post('/api/runs/{runid}/pivot')
def pivot(runid:str,body:dict,u=Depends(user)):
 try:
  with mart_for(u,runid) as m:return {'items':m.pivot(body.get('dimensions',FACT_DIMS[:8]),body.get('filters',{}),min(max(1,int(body.get('limit',250))),1000),max(0,int(body.get('offset',0)))),'measure':'count(distinct APP_ID)','denominator':m.total_apps()}
 except ValueError as e:raise HTTPException(422,str(e))
@app.post('/api/runs/{runid}/drill')
def drill(runid:str,body:dict,u=Depends(user)):
 with mart_for(u,runid) as m:
  limit=min(max(1,int(body.get('limit',50))),200);offset=max(0,int(body.get('offset',0)));kind=body.get('kind','facts');filters=body.get('filters',{})
  if kind=='facts':return m.drill(filters,limit,offset)
  if kind=='quality':
   cols={'APP_ID':'APP_ID','STATUS':'STATUS','SOURCE':'SOURCE'};w,p=where(filters,cols)
   if body.get('excluded'):w+=' AND APP_ID NOT IN (SELECT APP_ID FROM segments)'
   total=m.con.execute('SELECT count(DISTINCT APP_ID) FROM quality WHERE '+w,p).fetchone()[0]
   items=m.query('SELECT APP_ID,string_agg(DISTINCT STATUS,\', \') STATUS FROM quality WHERE '+w+' GROUP BY APP_ID ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset]);return {'total':total,'items':items}
  if kind=='segments':
   w,p=where(filters,{k:k for k in ['DECISION_BEFORE','DECISION_AFTER','DECISION_CHANGED','APP_ID']});total=m.con.execute('SELECT count(*) FROM segments WHERE '+w,p).fetchone()[0]
   return {'total':total,'items':m.query('SELECT * FROM segments WHERE '+w+' ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset])}
  if kind=='products':
   w,p=where(filters,{k:k for k in ['PRODUCT','MODEL','SOURCE','DECISION','APP_ID']});total=m.con.execute('SELECT count(DISTINCT APP_ID) FROM products WHERE '+w,p).fetchone()[0]
   return {'total':total,'items':m.query('SELECT DISTINCT APP_ID FROM products WHERE '+w+' ORDER BY APP_ID LIMIT ? OFFSET ?',p+[limit,offset])}
  raise HTTPException(422,'Неверный вид детализации')
@app.get('/api/runs/{runid}/apps/{appid}')
def application(runid:str,appid:str,u=Depends(user)):
 with mart_for(u,runid) as m:
  return {'comparison':m.query('SELECT * FROM comparison WHERE APP_ID=? ORDER BY PATH,KEY_JSON',[appid]),'quality':m.query('SELECT * FROM quality WHERE APP_ID=? ORDER BY SOURCE',[appid]),'segments':m.query('SELECT * FROM segments WHERE APP_ID=?',[appid]),'products':m.query('SELECT * FROM products WHERE APP_ID=? ORDER BY PRODUCT,SOURCE',[appid])}
@app.get('/api/runs/{runid}/checks')
def checks(runid:str,u=Depends(user)):
 with Session() as s:r=run_access(s,u,runid,True);return json.loads((Path(r.artifact)/'checks.json').read_text(encoding='utf-8'))
@app.post('/api/runs/{runid}/test-procedure')
def test_procedure(runid:str,body:dict,u=Depends(user)):
 with Session.begin() as s:
  r=run_access(s,u,runid,True);access(s,u,r.project,lead=True);path=r.artifact;audit(s,u.id,r.project,'procedure_preview',r.id)
 return execute(body['code'],body.get('params',{}),path)
@app.get('/api/runs/{runid}/scorecards')
def scorecards(runid:str,u=Depends(user)):
 with mart_for(u,runid) as m:return {'groups':m.query('SELECT MODEL,PRODUCT,SOURCE,count(DISTINCT APP_ID) CNT,min(SCORE) MIN,avg(SCORE) AVG,max(SCORE) MAX,min(CUTOFF) CUTOFF_MIN,max(CUTOFF) CUTOFF_MAX FROM products GROUP BY ALL ORDER BY MODEL,PRODUCT,SOURCE'),'distribution':m.query('SELECT MODEL,SOURCE,floor(SCORE*10)/10 BUCKET,count(DISTINCT APP_ID) CNT FROM products WHERE SCORE IS NOT NULL GROUP BY ALL ORDER BY MODEL,SOURCE,BUCKET'),'features':m.query("SELECT PATH,ARRAY_KEY,CHANGE_TYPE,count(DISTINCT APP_ID) CNT FROM comparison WHERE starts_with(PATH,'decisionResult.scoring') GROUP BY ALL ORDER BY PATH,ARRAY_KEY")}
@app.post('/api/runs/{runid}/scenario')
def scenario(runid:str,body:dict,u=Depends(user)):
 cutoff=float(body.get('cutoff',0.55));direction=body.get('direction','ge');product=str(body.get('product',''))
 if not 0<=cutoff<=1 or direction not in ['ge','le']:raise HTTPException(422)
 with Session() as s:r=run_access(s,u,runid,True);access(s,u,r.project,write=True);project=r.project
 with mart_for(u,runid) as m:
  rows=m.query('SELECT * FROM products WHERE SOURCE=\'AFTER\' AND PRODUCT=? ORDER BY APP_ID',[product])
  if not rows:raise HTTPException(422,'Для продукта нет баллов и порогов')
  eligible=[r for r in rows if r['SCORE'] is not None and r['CUTOFF'] is not None and not r.get('STOP_UNKNOWN',True)]
  for r in eligible:r['SIMULATED']='APPROVE' if not r['STOP'] and (r['SCORE']>=cutoff if direction=='ge' else r['SCORE']<=cutoff) else 'REJECT'
  changed=[r for r in eligible if r['SIMULATED']!=r['DECISION']]
  result={'base':runid,'cutoff':cutoff,'direction':direction,'product':product,'total':len(eligible),'excluded':len(rows)-len(eligible),'changed':len(changed),'items':changed,'simulation':True,'limitations':'Симуляция одного продуктового порога и hit=true. Это не ответ банковской СПР; остальные правила и офферы не пересчитываются.','code':'APPROVE if not STOP and compare(SCORE, cutoff, direction) else REJECT'}
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
def export(runid:str,format:str='html',filters:str='{}',dimensions:str='',u=Depends(user)):
 selected_filters=json.loads(filters);selected_dims=json.loads(dimensions) if dimensions else FACT_DIMS[:8]
 if not isinstance(selected_filters,dict) or not isinstance(selected_dims,list):raise ValueError('Invalid export filters or dimensions')
 with Session.begin() as s:
  r=run_access(s,u,runid,True);summary=r.summary;artifact=r.artifact;audit(s,u.id,r.project,'export',runid,{'format':format})
 if format=='html':
  results=json.loads((Path(artifact)/'checks.json').read_text(encoding='utf-8'))
  table=''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(str(v))+'</td></tr>' for k,v in summary.items())
  checks=''.join('<tr><td>'+html.escape(str(x.get('LABEL','')))+'</td><td>'+html.escape(str(x.get('RESULT','')))+'</td><td>'+html.escape(str(x.get('CNT','')))+'</td><td>'+html.escape(str(x.get('TOTAL','')))+'</td></tr>' for x in results)
  doc='<!doctype html><meta charset="utf-8"><title>Отчёт СПР</title><style>body{font:16px system-ui;max-width:1100px;margin:50px auto;color:#18312e}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ddd;text-align:left}</style><h1>Скорифай · отчёт сравнения</h1><p>Прогон '+html.escape(runid)+'</p><p>Сравнительная база — только валидные пары. Исключения не считаются исчезновением атрибутов. Различие не доказывает причинность.</p><a href="'+html.escape(BASE_URL+'/?run='+runid,quote=True)+'">Открыть интерактивный отчёт</a><h2>Выборка и показатели</h2><table>'+table+'</table><h2>Проверки</h2><table><tr><th>Проверка</th><th>Итог</th><th>Число</th><th>База</th></tr>'+checks+'</table><h2>Дата и параметры</h2><pre>'+html.escape(json.dumps({'created':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'profile':json.loads((Path(artifact)/'profile.json').read_text(encoding='utf-8'))},ensure_ascii=False,indent=2))+'</pre>'
  return HTMLResponse(doc,headers={'Content-Disposition':f'attachment; filename="report-{runid}.html"'})
 with mart_for(u,runid) as m:rows=m.pivot(selected_dims,selected_filters,limit=100001)
 if len(rows)>100000:raise HTTPException(413,'Более 100000 групп: сузьте фильтры экспорта')
 if format=='json':return Response(json.dumps({'schema':'pivot facts v1; CNT=distinct APP_ID per group; totals overlap','run':runid,'dimensions':selected_dims,'filters':selected_filters,'items':rows},ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="facts.json"'})
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

@app.get('/api/runs/{runid}/decision-factors')
def decision_factors(runid:str,direction:str='le',u=Depends(user)):
 from .decision import decide
 if direction not in ('le','ge'):raise ValueError('Выберите направление порога')
 with mart_for(u,runid) as m:rows=m.query('SELECT * FROM products ORDER BY APP_ID,PRODUCT,SOURCE')
 for r in rows:
  stops=[True] if r['STOP'] else [None] if r.get('STOP_UNKNOWN',True) else []
  if r['CUTOFF'] is None:result,reason='UNKNOWN','INCOMPLETE'
  else:result,reason=decide(r['SCORE'],stops,r['CUTOFF'],direction)
  r.update(EXPECTED=result,REASON=reason,DIRECTION=direction)
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
  entries.append(File(id=ident,project=project,dataset=dataset,source=source,name=appid+'.json',path=str(path),sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),format='json'))
 with Session.begin() as s:
  s.add(Record(id=dataset,project=project,kind='dataset',name=('Симуляция · ' if mode=='demo' else 'СПР · ')+pool.name,author=u.id,body={'simulation':mode=='demo','pool':poolid,'versions':versions,'engine':'demo-score-v1' if mode=='demo' else 'http-adapter-v1','limitations':'Демонстрационная СПР: стоп-факторы и один скор, не банковская стратегия'}));s.add_all(entries);audit(s,u.id,project,'pool_execute',dataset,{'pool':poolid,'simulation':mode=='demo'})
 return {'id':dataset,'simulation':mode=='demo','files':len(entries)}

if (ROOT/'frontend/dist/assets').exists():app.mount('/assets',StaticFiles(directory=ROOT/'frontend/dist/assets'),name='assets')
@app.get('/{path:path}')
def spa(path:str):
 if path.startswith('api/'):raise HTTPException(404)
 p=ROOT/'frontend/dist/index.html'
 return FileResponse(p) if p.exists() else HTMLResponse('Соберите фронтенд: cd frontend && npm ci && npm run build',503)
