import sys,subprocess,shutil,json,os,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from spr.config import MATERIALS
from spr.sources import documents,unique_files
base=ROOT/'tmp/prompt2-baseline';base.mkdir(exist_ok=True);shutil.copytree(ROOT/'spr',base/'spr',dirs_exist_ok=True)
for name in ('pipeline.py','normalize.py','analytics.py','buckets.py'):
 (base/'spr'/name).write_bytes(subprocess.check_output(['git','show',':spr/'+name],cwd=ROOT))
source=MATERIALS/'data/RUN-20260827-0001';manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
files=[dict(name=p.name,path=str(p),source=stage,format='json') for stage,spec in manifest['srez'].items() for p in documents(source/spec['dir'])]
checks=[dict(id=p.stem,name=p.stem,code=p.read_text(encoding='utf-8'),params={}) for p in unique_files(MATERIALS/'checks',('.py',)) if p.name.startswith('check_')]
job={'files':files,'profile':json.loads((ROOT/'profiles/main.json').read_text(encoding='utf-8')),'checks':checks};jobfile=ROOT/'tmp/prompt2-job.json';jobfile.write_text(json.dumps(job),encoding='utf-8')
driver=ROOT/'tmp/prompt2-driver.py';driver.write_text('''import sys,json,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom spr.pipeline import process\njob=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))\nsummary=process(job["files"],job["profile"],sys.argv[3],checks=job["checks"])\nprint(json.dumps(summary))\n''',encoding='utf-8')
report={'source':'provided JSON sample RUN-20260827-0001, not synthetic','files':len(files),'input_bytes':sum(Path(f['path']).stat().st_size for f in files),'conditions':'Sequential runs on same files, OS cache not cleared, three full imports per variant. Baseline is repository index snapshot before prompt 2 changes.','runs':{}}
for mode,path in [('before',base),('after',ROOT)]*3:
 target=ROOT/'tmp'/('prompt2-full-'+mode+'-'+str(time.time_ns()));env={**os.environ,'SPR_TRUSTED_LOCAL_CODE':'1','PYTHONIOENCODING':'utf-8'}
 r=subprocess.run([sys.executable,str(driver),str(path),str(jobfile),str(target)],env=env,capture_output=True,text=True,encoding='utf-8');print(mode,r.returncode,r.stdout,r.stderr[-1000:],flush=True)
 if r.returncode:raise RuntimeError(mode)
 previous=report['runs'].get(mode,{}).get('repetitions',[])
 report['runs'][mode]={'summary':json.loads(r.stdout),'timings':json.loads((target/'timings.json').read_text()),'artifacts':str(target)}
 report['runs'][mode]['repetitions']=previous+[report['runs'][mode]['timings']]
import statistics
for run in report['runs'].values():run['median_timings']={key:statistics.median(r[key] for r in run['repetitions']) for key in run['timings']}
Path('docs/prompt2-import-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
