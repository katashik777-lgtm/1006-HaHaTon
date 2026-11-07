import sys,json,os,csv,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['SPR_TRUSTED_LOCAL_CODE']='1'
from spr.config import ROOT,MATERIALS
from spr.sources import documents,unique_files
from spr.pipeline import process
from spr.analytics import Mart
source=MATERIALS/'data/RUN-20260827-0001'
manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
files=[dict(name=p.name,path=str(p),source=stage,format='json') for stage,spec in manifest['srez'].items() for p in documents(source/spec['dir'])]
checks=[dict(id=p.stem,name=p.stem,code=p.read_text(encoding='utf-8'),params={}) for p in unique_files(MATERIALS/'checks',('.py',)) if p.name.startswith('check_')]
target=ROOT/'tmp'/('acceptance-'+str(int(time.time())))
summary=process(files,json.loads((ROOT/'profiles/main.json').read_text(encoding='utf-8')),target,checks=checks)
expected=list(csv.DictReader(next(ROOT.glob('ХАК*/ПАКЕТ/ОРГАНИЗАТОРАМ/ЭТАЛОН/expected_OPEN.csv')).open(encoding='utf-8-sig'),delimiter=';'))
m=Mart(target)
actual=set(m.app_ids(diff_flag=1));wanted={r['APP_ID'] for r in expected if r['CHANGED']=='1'}
assert actual==wanted
assert summary['valid_pairs']==2002
results=json.loads((target/'checks.json').read_text(encoding='utf-8'))
assert not [r for r in results if r.get('RESULT')=='ERROR'],results
for row in m.pivot(['PRODUCT','SOURCE','DIFF_FLAG'],{},10000):
 assert m.drill({k:v for k,v in row.items() if k!='CNT'})['total']==row['CNT']
m.close()
report={'summary':summary,'checks_selected':len(checks),'check_rows':len(results),'check_results':results,'reference_ids_match':True,'artifacts':str(target)}
(ROOT/'docs/acceptance-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='check_results'},ensure_ascii=False))
