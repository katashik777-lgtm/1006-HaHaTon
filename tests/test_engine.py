import csv,json,copy
from pathlib import Path
import pytest
from spr.normalize import normalize,frame,compare,parse
from spr.config import MATERIALS,ROOT
from spr.pipeline import process,verify_manifest
from spr.analytics import Mart

def diff(b,a,p=None):
 p=p or {};return compare(frame(normalize(b,'a','BEFORE',p)+normalize(a,'a','AFTER',p)),['a'])
@pytest.mark.parametrize('profile,folder,before,after',[('example','example','01_response_BEFORE.json','02_response_AFTER.json'),('examples','examples','01_response_BEFORE_MASTER_app1001.json','02_response_AFTER_BETA_app1001.json')])
def test_teaching(profile,folder,before,after):
 p=json.loads((ROOT/'profiles'/f'{profile}.json').read_text(encoding='utf-8'));base=MATERIALS/folder
 b=json.loads((base/before).read_text(encoding='utf-8'));a=json.loads((base/after).read_text(encoding='utf-8'));nb=normalize(b,'1001','BEFORE',p);na=normalize(a,'1001','AFTER',p)
 assert (len(nb),len(na),len(nb+na))==(12,14,26)
 c=compare(frame(nb+na),['1001']);assert c.height==14;assert c['DIFF_FLAG'].sum()==6
 assert c.filter(__import__('polars').col('CHANGE_TYPE')=='VALUE_CHANGED').height==4
 expected=list(csv.DictReader(next(base.glob('03*.csv')).open(encoding='utf-8-sig'),delimiter=';'))
 for actual,want in zip(nb,expected):assert {k:actual[k] for k in want}==want
@pytest.mark.parametrize('mode,keys,b,a,changes',[
 ('key',['id'],[{'id':'b','v':2},{'id':'a','v':1}],[{'id':'a','v':1},{'id':'b','v':2}],0),
 ('value',[],['x','y'],['y','x'],0),('index',[],[1,2],[2,1],2)])
def test_array_modes(mode,keys,b,a,changes):assert diff({'x':b},{'x':a},{'arrays':{'x':{'mode':mode,'keys':keys}}})['DIFF_FLAG'].sum()==changes
@pytest.mark.parametrize('a,b',[(None,False),(False,0),(0,''),(0,'0'),(False,'false'),(None,'NULL')])
def test_types(a,b):assert diff({'x':a},{'x':b})['CHANGE_TYPE'][0]=='VALUE_CHANGED'
def test_absence():
 c=diff({'a':None,'same':1,'old':0},{'a':False,'same':1,'new':''})
 assert set(c['CHANGE_TYPE'])=={'SAME','VALUE_CHANGED','ATTRIBUTE_NEW','ATTRIBUTE_LOST'}
def test_nested_and_composites():
 p={'arrays':{'x':{'mode':'key','keys':['a','b']},'x.models':{'mode':'key','keys':['name']}}}
 d={'x':[{'a':'a|b','b':'c','models':[{'name':'m1','f':0},{'name':'m2','f':1}]},{'a':'a','b':'b|c','models':[{'name':'m1','f':2}]}]}
 n=normalize(d,'a','BEFORE',p);assert len({(r['PATH'],r['KEY_JSON']) for r in n})==len(n)
 assert diff(d,copy.deepcopy(d),p)['DIFF_FLAG'].sum()==0
@pytest.mark.parametrize('mode,arr',[('value',['a','a']),('key',[{'id':1},{'id':1}])])
def test_collision(mode,arr):
 with pytest.raises(ValueError,match='collision'):normalize({'x':arr},'a','BEFORE',{'arrays':{'x':{'mode':mode,'keys':['id']}}})
def test_bucket_original_values():
 p={'buckets':{'v':[{'min':0,'max':1,'label':'same bucket'}]}}
 c=diff({'v':.42},{'v':.58},p);assert c['DIFF_FLAG'][0]==1;assert c['ADJUSTED_BEFORE'][0]==c['ADJUSTED_AFTER'][0]
def test_technical():assert diff({'x':1,'time':1},{'x':1,'time':2},{'exclude':['time']})['DIFF_FLAG'].sum()==0

def test_json_xml_csv():
 p={'types':{'score':'integer','ok':'boolean'}}
 docs=[parse(x,f,p) for f,x in [('json',b'{"id":"1","score":42,"ok":true}'),('xml',b'<root><id>1</id><score>42</score><ok>true</ok></root>'),('csv',b'id;score;ok\n1;42;true\n')]]
 assert docs[0]==docs[1]==docs[2]
def test_xml_external_disabled():
 with pytest.raises(Exception):parse(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>','xml',{})
def test_demo_equivalent():
 b=MATERIALS/'examples';rows=list(csv.DictReader((b/'07_pivot_facts_demo.csv').open(encoding='utf-8-sig'),delimiter=';'));obj=json.loads((b/'08_pivot_facts_demo.json').read_text(encoding='utf-8'))
 assert len(rows)==len(obj)==59
 for x,y in zip(rows,obj):assert {k:str(v) for k,v in x.items()}=={k:str(v) for k,v in y.items()}
 for source in ['BEFORE','AFTER']:assert sum(int(r['CNT']) for r in rows if r['KEY']=='decision' and r['SOURCE'].startswith(source))==465
 assert 'пяти' in (b/'README.md').read_text(encoding='utf-8')
@pytest.fixture
def artifact(tmp_path):
 files=[]
 def add(app,src,raw):
  p=tmp_path/(app+src);p.write_text(raw);files.append({'name':app+'.json','path':str(p),'source':src,'format':'json','id':app+src})
 for a in ['a','b']:
  for src in ['BEFORE','AFTER']:add(a,src,json.dumps({'id':a,'decision':'yes','x':1 if src=='BEFORE' else 2}))
 add('c','BEFORE','{"id":"c"}');add('d','BEFORE','broken');add('d','AFTER','{"id":"d"}')
 out=tmp_path/'artifact';summary=process(files,{'app_id':'id','decision_path':'decision'},out)
 return out,summary

def test_quality(artifact):
 out,s=artifact;assert s['discovered']==4;assert s['valid_pairs']==2;assert s['excluded']==2;assert s['changed_apps']==2
 m=Mart(out);assert m.total_apps()==2;assert m.app_ids()==['a','b'];assert not any(r['CHANGE_TYPE']=='ATTRIBUTE_LOST' for r in m.rows());m.close()
def test_mart_filters_and_distinct(artifact):
 m=Mart(artifact[0]);assert m.count(path='x',diff_flag=1,change_type=['VALUE_CHANGED'],value_before='1',value_after='2',app_ids=['a'])==1
 assert m.app_ids(path_prefix='x')==['a','b'];r=m.pivot(['SOURCE'],{});assert all(x['CNT']==2 for x in r)
 for row in m.pivot(['KEY','SOURCE','DIFF_FLAG'],{}):
  pred={k:v for k,v in row.items() if k!='CNT'};assert m.drill(pred)['total']==row['CNT']
 assert m.rows(path=['x','id']);m.close()
def test_manifest(artifact):
 out,_=artifact;verify_manifest(out);(out/'summary.json').write_text('{}')
 with pytest.raises(ValueError):verify_manifest(out)

def test_prefix_union_and_cache_lifetime(artifact):
 m=Mart(artifact[0])
 assert m.app_ids(path_prefix=['x','id'])==['a','b']
 assert m.app_ids(path_prefix=[])==[]
 m.rows(path='x');assert m._cache
 m.close();assert not m._cache

def test_source_checksum(tmp_path):
 p=tmp_path/'a.json';p.write_text('{"id":"a"}')
 summary=process([{'name':'a.json','path':str(p),'source':'BEFORE','format':'json','sha256':'incorrect'}],{'app_id':'id'},tmp_path/'out')
 assert summary['valid_pairs']==0
 assert summary['quality']['schema_error']==1
