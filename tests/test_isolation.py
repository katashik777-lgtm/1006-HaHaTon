import pytest
from spr.runner import execute,capability
from test_engine import artifact

@pytest.mark.skipif(not capability(),reason='OS isolation unavailable')
@pytest.mark.parametrize('code,fragment',[
 ('open("/etc/shadow").read()',''),
 ('import socket; socket.create_connection(("1.1.1.1",443),timeout=1)','permitted'),
 ('open("/tmp/spr-unauthorized-write", "w").write("x")','permitted'),
])
def test_denied(artifact,code,fragment):
 r=execute(code,{},artifact[0]);assert r.get('error');assert fragment in r['error']

def test_success(artifact):
 if not capability():pytest.skip()
 code='''def run(mart,params):
 print("preview output")
 return [{"LABEL":"test","SOURCE":"AFTER","ARGUMENT":"x","CNT":len(mart.app_ids(diff_flag=1)),"TOTAL":mart.total_apps(),"METRIC":1,"APP_IDS":mart.app_ids(diff_flag=1),"RESULT":"FAIL","CONFIDENCE":"HIGH"}]
CHECK={"id":"test","entrypoint":run}
'''
 r=execute(code,{},artifact[0]);assert not r.get('error'),r;assert r['rows'][0]['CNT']==2;assert 'preview output' in r['stdout']
