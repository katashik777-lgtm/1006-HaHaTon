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
