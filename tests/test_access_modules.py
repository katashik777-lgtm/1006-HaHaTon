from test_workflow import system
from spr.decision import decide
import pytest
from fastapi.testclient import TestClient
from spr import api


def test_registration_and_admin_roles(system):
 c,p,s,_=system
 with TestClient(api.app) as newcomer:
  assert newcomer.post('/api/register',json={'name':'new-user','password':'long-password-123','admin':True}).status_code==200
  assert newcomer.post('/api/login',json={'name':'new-user','password':'long-password-123'}).status_code==200
  assert newcomer.get('/api/projects').json()==[]
  assert newcomer.get('/api/admin/users').status_code==403
  ident=next(u['id'] for u in c.get('/api/admin/users').json() if u['name']=='new-user')
  for role in ['observer','analyst','lead']:
   assert c.post('/api/admin/users/'+ident,json={'project':p,'role':role}).status_code==200
   newcomer.post('/api/login',json={'name':'new-user','password':'long-password-123'})
   assert newcomer.get('/api/projects').json()[0]['role']==role
   assert newcomer.post(f'/api/projects/{p}/pools',json={'rows':[{'id':'1'}]}).status_code==(200 if role=='lead' else 403)
  assert c.post('/api/admin/users/admin',json={'admin':False}).status_code==422


def test_configured_admin_password_applies_to_existing_account(system, monkeypatch):
 from spr import cli
 client, _, sessions, _ = system
 monkeypatch.setattr(cli, 'Session', sessions)
 monkeypatch.setattr(cli.subprocess, 'run', lambda *args, **kwargs: None)
 cli.init('admin', sync_admin_password=True)
 assert client.get('/api/me').status_code == 401
 assert client.post('/api/login', json={'name':'admin','password':'workflow-password'}).status_code == 401
 response = client.post('/api/login', json={'name':'admin','password':'admin'})
 assert response.status_code == 200 and response.json()['admin'] == 1 and not response.json()['demo']
 cli.init('admin', sync_admin_password=True)
 assert client.get('/api/me').json()['admin'] == 1


def test_optional_modules(system):
 c,p,_,_=system
 pool=c.post(f'/api/projects/{p}/pools',json={'rows':[{'id':'a','score':.3,'stops':[True]},{'id':'b','score':.3,'stops':[False]},{'id':'c','score':.3,'stops':[None]}]})
 assert pool.status_code==200,pool.text
 versions={'BEFORE':{'cutoff':.5,'direction':'le'},'AFTER':{'cutoff':.2,'direction':'le'}}
 result=c.post(f'/api/projects/{p}/pools/{pool.json()["id"]}/execute',json={'versions':versions})
 assert result.status_code==200,result.text
 assert result.json()['files']==6
 assert result.json()['simulation'] is True
 records=c.get(f'/api/projects/{p}/records').json()
 profile=next(r for r in records if r['kind']=='dataset')
 assert profile['body']['versions']==versions
 from test_workflow import record
 from spr import worker
 prof=record(c,p,'profile',{'app_id':'appData.application_id'})
 run=c.post(f'/api/projects/{p}/runs',json={'dataset':result.json()['id'],'profile':prof}).json()['id']
 worker.work(*worker.claim())
 factors=c.get('/api/runs/'+run+'/decision-factors').json()['items']
 assert len(factors)==6
 assert all(r['EXPECTED']=='REJECT' for r in factors if r['APP_ID']=='a')
 assert all(r['EXPECTED']=='UNKNOWN' for r in factors if r['APP_ID']=='c')

@pytest.mark.parametrize('score,stops,cutoff,direction,result',[(.1,[True],.5,'le','REJECT'),(.1,[False],.5,'le','APPROVE'),(.1,[None],.5,'le','UNKNOWN'),(None,[True],.5,'le','REJECT'),(.8,[],.5,'ge','APPROVE')])
def test_stop_and_score(score,stops,cutoff,direction,result):
 assert decide(score,stops,cutoff,direction)[0]==result

def test_quality_and_decision_analysis(system):
 from test_workflow import record
 from spr import worker
 c,p,_,_=system
 pool=c.post(f'/api/projects/{p}/pools',json={'rows':[{'id':'a','score':.45,'stops':[True]}]}).json()['id']
 dataset=c.post(f'/api/projects/{p}/pools/{pool}/execute',json={'versions':{'BEFORE':{'cutoff':.5,'direction':'le'},'AFTER':{'cutoff':.4,'direction':'le'}}}).json()['id']
 profile=record(c,p,'profile',{'app_id':'appData.application_id'})
 run=c.post(f'/api/projects/{p}/runs',json={'dataset':dataset,'profile':profile}).json()['id']
 worker.work(*worker.claim())
 assert c.get(f'/api/runs/{run}/quality-summary').json()==[{'SOURCE':'AFTER','STATUS':'parsed','CNT':1},{'SOURCE':'BEFORE','STATUS':'parsed','CNT':1}]
 assert len(c.get(f'/api/runs/{run}/decision-analysis').json()['items'])==2
 assert any(r['LABEL']=='BUCKET_DISTRIBUTION' for r in c.get(f'/api/runs/{run}/checks').json())


def test_public_demo_has_access_only_to_published_snapshot(system, monkeypatch):
 from test_workflow import enqueue
 from spr import worker
 from spr.db import Run, User
 from spr.public_demo import VIEWER_ID
 from spr.security import hash_password
 admin, project, sessions, _=system
 run=enqueue(system)
 worker.work(*worker.claim())
 other=enqueue(system)
 worker.work(*worker.claim())
 with sessions.begin() as session:
  dataset=session.get(Run,run).dataset
  private_file=session.get(Run,other).snapshot['files'][0]['id']
  session.add(User(id=VIEWER_ID,name='Demo',admin=0,password=hash_password('random-viewer-password')))
 monkeypatch.setenv('SPR_PUBLIC_DEMO_RUN',run)
 with TestClient(api.app) as guest:
  rejected=guest.post('/api/login',json={'name':'admin','password':'admin'})
  assert rejected.status_code==401 and 'пароль сервера' in rejected.json()['detail']
  assert guest.get('/api/me').status_code==401
  login=guest.post('/api/demo-login').json()
  assert login['admin']==0 and login['demo']
  assert [item['id'] for item in guest.get(f'/api/projects/{project}/runs').json()]==[run]
  assert [item['id'] for item in guest.get(f'/api/projects/{project}/records').json()]==[dataset]
  assert guest.get('/api/runs/'+other).status_code==404
  assert guest.get('/api/files/'+private_file).status_code==404
  assert guest.get('/api/admin/users').status_code==403
  assert guest.post(f'/api/projects/{project}/runs',json={}).status_code==403
  repeat=f'/api/projects/{project}/demo-run'
  demo=guest.post(repeat,json={'dataset':private_file,'procedures':{'parse':'private'}}).json()
  assert demo['status']=='queued'
  assert guest.post(repeat).json()==demo
  assert guest.get('/api/runs/'+demo['id']).json()['dataset']==dataset
  assert guest.post('/api/runs/'+demo['id']+'/cancel').status_code==403
  worker.work(*worker.claim())
  assert guest.get('/api/runs/'+demo['id']).json()['status']=='completed'
  assert guest.post(repeat).json()=={'id':demo['id'],'status':'completed'}
  assert guest.get('/api/runs/'+demo['id']+'/export?format=xlsx').content.startswith(b'PK')
  assert guest.get('/api/runs/'+other+'/export?format=xlsx').status_code==404
 assert admin.get('/api/me').json()['admin']==1
 assert not admin.get('/api/me').json()['demo']
