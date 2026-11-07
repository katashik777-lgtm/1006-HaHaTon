"""Equivalent nested inputs through the production process entry point."""
import csv,io,json
from xml.etree.ElementTree import Element,SubElement,tostring
import polars as pl
from spr.pipeline import process

def xml_doc(doc):
 root=Element('root')
 def fill(parent,k,v):
  if isinstance(v,list):
   for item in v:fill(parent,k,item)
   return
  node=SubElement(parent,k)
  if isinstance(v,dict):
   for key,item in v.items():fill(node,key,item)
  else:node.text=str(v).lower() if isinstance(v,bool) else str(v)
 for k,v in doc.items():fill(root,k,v)
 return tostring(root,encoding='utf-8')

def test_formats_end_to_end(tmp_path):
 profile={'app_id':'id','types':{'score':'number','decisionResult.product_trials.product_id':'string','decisionResult.product_trials.scoring.pd.value':'number','decisionResult.product_trials.cutOff.value':'number','decisionResult.product_trials.rules.hit':'boolean'},'arrays':{'decisionResult.product_trials':{'mode':'key','keys':['product_id']},'decisionResult.product_trials.rules':{'mode':'key','keys':['name']}},'bucket_config':{'score':{'threshold':.6,'radius':.1,'width':.2}}}
 outputs=[]
 for fmt,delimiter in [('json',';'),('xml',';'),('csv',';'),('csv',',')]:
  folder=tmp_path/(fmt+str(ord(delimiter)));folder.mkdir();files=[]
  for i,(before,after) in enumerate([(.42,.58),(.55,.7),(.73,.75),(.6,.6001)]):
   for source,value in [('BEFORE',before),('AFTER',after)]:
    doc={'id':str(i),'code':'0012','text':'Кириллица; "кавычки", запятая','empty':'','score':value,'decisionResult':{'decision':'APPROVE','product_trials':[{'product_id':str(j),'decision':'APPROVE','scoring':{'pd':{'modelName':'demo','value':value}},'cutOff':{'value':.6},'rules':[{'name':'Стоп-фактор 1','hit':False},{'name':'Стоп-фактор 2','hit':False}]} for j in (1,2)]}}
    doc['only_'+source]='one side'
    if fmt=='json':raw=json.dumps(doc,ensure_ascii=False).encode()
    elif fmt=='xml':raw=xml_doc(doc)
    else:
     out=io.StringIO();writer=csv.DictWriter(out,fieldnames=doc,delimiter=delimiter);writer.writeheader();writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,dict) else v for k,v in doc.items()});raw=out.getvalue().encode()
    path=folder/(source+str(i)+'.'+fmt);path.write_bytes(raw);files.append({'name':str(i)+'.'+fmt,'path':str(path),'source':source,'format':fmt})
  target=folder/'out';summary=process(files,{**profile,'delimiter':delimiter},target)
  assert summary['valid_pairs']==4
  outputs.append({name:pl.read_parquet(target/(name+'.parquet')).sort(['APP_ID']+(['SOURCE','PATH','KEY_JSON'] if name=='normalized' else ['PATH','KEY_JSON'] if name=='comparison' else ['SOURCE','PRODUCT'])).to_dicts() for name in ('normalized','comparison','products')})
  assert json.loads((target/'checks.json').read_bytes())
 assert all(result==outputs[0] for result in outputs)

def test_quality_broken_and_clean(tmp_path):
 files=[]
 for source,raw in [('BEFORE',b'{"score":0}'),('AFTER',b'not json')]:
  path=tmp_path/source;path.write_bytes(raw);files.append({'name':'a.json','path':str(path),'source':source,'format':'json'})
 target=tmp_path/'out';summary=process(files,{},target)
 assert summary['quality']['parse_error']==1 and summary['quality']['parsed']==1
 assert summary['excluded']==1
