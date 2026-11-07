"""Measure the current snapshot, optionally against an earlier project directory."""
import argparse, json, os, platform, statistics, subprocess, sys, tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from spr.config import MATERIALS
from spr.sources import documents,unique_files
cli=argparse.ArgumentParser(description=__doc__)
cli.add_argument('--baseline', type=Path, help='Project directory with the earlier spr package')
cli.add_argument('--output', type=Path, help='Write the measurements as JSON')
args=cli.parse_args()
if args.baseline and not (args.baseline/'spr/pipeline.py').is_file():
    cli.error('--baseline must contain spr/pipeline.py')
versions=([('before', str(args.baseline.resolve()))] if args.baseline else [])+[('after',str(root))]
scratch=tempfile.TemporaryDirectory(prefix='scorify-prompt2-bench-')
target=Path(scratch.name)
source=MATERIALS/'data/RUN-20260827-0001'
manifest=json.loads((source/'manifest.json').read_text())
files=[dict(name=p.name,path=str(p),source=stage,format='json') for stage,spec in manifest['srez'].items() for p in documents(source/spec['dir'])]
checks=[dict(id=p.stem,name=p.stem,code=p.read_text(),params={}) for p in unique_files(MATERIALS/'checks',('.py',)) if p.name.startswith('check_')]
job=target/'job.json'
job.write_text(json.dumps({'files':files,'profile':json.loads((root/'profiles/main.json').read_text()),'checks':checks}))
program=r'''
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from spr.pipeline import process
from spr.analytics import Mart
p=Path(sys.argv[3]); job=json.loads(Path(sys.argv[2]).read_text())
summary=process(job['files'],job['profile'],p,checks=job['checks'])
published=json.loads((p/'checks.json').read_bytes())
assert all(r['RESULT']!='ERROR' for r in published), published[-5:]
duration=lambda start:time.perf_counter()-start
series=[]
for _ in range(7):
 t=time.perf_counter();json.loads((p/'checks.json').read_bytes());report=duration(t)
 t=time.perf_counter()
 with Mart(p) as m:
  reader=duration(t)
  t=time.perf_counter()
  filters={'PATH':'decisionResult.product_trials.scoring.pd.value','DIFF_FLAG':1}
  m.pivot(['SOURCE','PRODUCT','VALUE_ADJUSTED'],filters)
  first=duration(t)
  t=time.perf_counter();m.pivot(['SOURCE','PRODUCT','VALUE_ADJUSTED'],filters);repeat=duration(t)
 series.append(dict(report=report,reader=reader,pivot_first=first,pivot_repeat=repeat))
if sys.argv[4]=='after':
 from spr.analysis_scope import AnalysisScope,parse_options
 opts=parse_options({'product':'751','basis':'scenario','mode':'penalty','direction':'le','penalty':'0.1','minimum':'0.3'})
 for row in series:
  t=time.perf_counter()
  with Mart(p) as m:
   scope=AnalysisScope(m,opts)
   m.pivot(['SOURCE','PRODUCT','VALUE_ADJUSTED'],filters)
  row['scenario_pivot']=duration(t)
  t=time.perf_counter()
  with Mart(p) as m:
   scope=AnalysisScope(m,opts)
   m.bucket_report()
  row['scenario_report']=duration(t)
print(json.dumps({'summary':summary,'timing':json.loads((p/'timings.json').read_text()),'reader':series}))
'''
results={'files':len(files),'bytes':sum(Path(f['path']).stat().st_size for f in files),
         'conditions':f'{platform.system()} {platform.machine()}, Python {platform.python_version()}, sequential runs with no OS cache flush, read_workers=1, 3 imports per version and 7 queries per import. Both versions use the current profile and the same bundled checks.',
         'baseline':args.baseline.name if args.baseline else None, 'runs':{name:[] for name,_ in versions}}
for i in range(3):
 for label,path in versions:
  output=subprocess.check_output([sys.executable,'-c',program,path,str(job),str(target/f'{label}-{i}'),label],env={**os.environ,'SPR_TRUSTED_LOCAL_CODE':'1','POLARS_MAX_THREADS':'2'})
  row=json.loads(output); results['runs'][label].append(row)
  print(label,i,row['timing']['total'],file=sys.stderr,flush=True)
results['medians']={}
for label,rows in results['runs'].items():
 queries=[v for row in rows for v in row['reader']]
 results['medians'][label]={'import':{k:statistics.median(r['timing'][k] for r in rows) for k in rows[0]['timing']},
                            'reader':{k:statistics.median(r[k] for r in queries) for k in queries[0]}}
if args.output:
    args.output.write_text(json.dumps(results,indent=2)+'\n', encoding='utf-8')
print(json.dumps(results['medians'],indent=2))
