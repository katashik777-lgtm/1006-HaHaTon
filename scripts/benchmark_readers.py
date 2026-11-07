"""Reproducible reader benchmark; does not claim a full import speedup."""
import sys,time,statistics,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import duckdb
from spr.analytics import Mart
from spr.config import ROOT
source=(ROOT / (sys.argv[1] if len(sys.argv)>1 else 'tmp/benchmark-sequential-warm/artifacts')).resolve()
target=Path('tmp/prompt2-reader-benchmark');target.mkdir(exist_ok=True)
import shutil
shutil.copyfile(source/'comparison.parquet',target/'comparison.parquet')
file=str(target/'comparison.parquet').replace("'","''")
db=target/'comparison.duckdb'
if db.exists():db.unlink()
t=time.perf_counter()
with duckdb.connect(str(db)) as c:
 c.execute(f"CREATE TABLE comparison AS SELECT * FROM read_parquet('{file}')")
 c.execute('CREATE INDEX comparison_app ON comparison(APP_ID)');c.execute('CREATE INDEX comparison_path ON comparison(PATH)')
build=time.perf_counter()-t
results={}
for mode in ('before_rebuild','after_persisted'):
 opens=[];first=[];repeat=[]
 for iteration in range(7):
  t=time.perf_counter()
  if mode=='before_rebuild':
   c=duckdb.connect(':memory:',config={'threads':2});c.execute(f"CREATE TABLE comparison AS SELECT * FROM read_parquet('{file}')");c.execute('CREATE INDEX comparison_app ON comparison(APP_ID)');c.execute('CREATE INDEX comparison_path ON comparison(PATH)')
  else:c=duckdb.connect(str(db),read_only=True,config={'threads':2})
  opens.append(time.perf_counter()-t)
  count=c.execute('SELECT count(*) FROM comparison').fetchone()[0];app=c.execute('SELECT APP_ID FROM comparison LIMIT 1').fetchone()[0]
  t=time.perf_counter();c.execute('SELECT count(*) FROM comparison WHERE APP_ID=?',[app]).fetchone();first.append(time.perf_counter()-t)
  t=time.perf_counter()
  for _ in range(100):c.execute('SELECT count(*) FROM comparison WHERE APP_ID=?',[app]).fetchone()
  repeat.append((time.perf_counter()-t)/100)
  plan=c.execute('EXPLAIN ANALYZE SELECT count(*) FROM comparison WHERE APP_ID=?',[app]).fetchall()
  c.close()
 results[mode]={'open_median_seconds':statistics.median(opens),'first_query_median_seconds':statistics.median(first),'warm_query_median_seconds':statistics.median(repeat)}
report={'rows':count,'input':source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else source.name,'input_bytes':(source/'comparison.parquet').stat().st_size,'repeats':7,'condition':'same Parquet, warm OS cache, 2 DuckDB threads; publication and full import not measured','index_build_seconds':build,'results':results,'plan':str(plan)}
Path('docs/prompt2-reader-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='plan'},indent=2))
