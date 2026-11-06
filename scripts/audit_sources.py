from pathlib import Path
import json,csv,collections,hashlib,openpyxl,sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pypdf import PdfReader
from spr.config import MATERIALS,ROOT
report=[]
for folder in ['example','examples','samples']:
 for f in sorted((MATERIALS/folder).rglob('*')):
  if not f.is_file() or f.name.startswith('.'):continue
  item={'path':str(f.relative_to(ROOT)),'size':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
  if f.suffix=='.json':
   obj=json.loads(f.read_text(encoding='utf-8'));item['structure']=list(obj) if isinstance(obj,dict) else {'rows':len(obj)}
  elif f.suffix=='.csv':
   rows=list(csv.DictReader(f.open(encoding='utf-8-sig'),delimiter=';'));item['rows']=len(rows);item['columns']=list(rows[0]) if rows else []
  elif f.suffix=='.xlsx':
   w=openpyxl.load_workbook(f,data_only=False,read_only=True);item['sheets']={s.title:{'rows':s.max_row,'columns':s.max_column,'formulas':sum(isinstance(c,str) and c.startswith('=') for row in s.values for c in row)} for s in w};w.close()
  else:item['text']=f.read_text(errors='replace')[:250]
  report.append(item)
for f in ROOT.glob('*.pdf'):
 reader=PdfReader(f);text='\n'.join(p.extract_text() for p in reader.pages);(ROOT/'tmp/lecture.txt').write_text(text);report.append({'path':str(f.relative_to(ROOT)),'pages':len(reader.pages),'text_length':len(text)});print('Lecture:',text[:13000])
(ROOT/'docs/source-inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
root=MATERIALS/'data/RUN-20260827-0001';stats={}
for src in ['BEFORE_MASTER','AFTER_BETA']:
 paths=list((root/'RESULTS'/src).glob('*.json'));bad=[];keys=collections.Counter()
 for f in paths:
  try:obj=json.loads(f.read_text(encoding='utf-8'));keys.update(obj.keys())
  except Exception as e:bad.append({'file':f.name,'error':str(e)})
 stats[src]={'files':len(paths),'bytes':sum(p.stat().st_size for p in paths),'top_keys':dict(keys),'parse_errors':bad}
(ROOT/'docs/open-dataset-inventory.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2));print(stats)
