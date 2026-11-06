"""Run while API and worker are stopped to obtain one consistent generation."""
import sqlite3,shutil,json,hashlib
from pathlib import Path
from .config import DATA,DATABASE
from .pipeline import verify_manifest

def backup(destination):
 destination=destination.resolve()
 if destination.exists():raise ValueError('Destination must not exist')
 if DATA==destination or DATA in destination.parents:raise ValueError('Backup outside live storage')
 if not DATABASE.startswith('sqlite'):raise ValueError('PostgreSQL: use documented pg_dump coordinated procedure')
 destination.mkdir(parents=True,mode=0o700)
 with sqlite3.connect(DATA/'metadata.sqlite') as src,sqlite3.connect(destination/'metadata.sqlite') as dst:src.backup(dst)
 for name in ['sources','artifacts']:
  if (DATA/name).exists():shutil.copytree(DATA/name,destination/name)
 hashes={str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob('*') if p.is_file()}
 (destination/'backup.json').write_text(json.dumps(hashes));print('Резервная копия:',destination)

def restore(source):
 source=source.resolve();hashes=json.loads((source/'backup.json').read_text())
 for name,digest in hashes.items():
  p=(source/name).resolve()
  if source not in p.parents or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Backup checksum mismatch')
 if any(DATA.iterdir()):raise ValueError('Restore only into an empty SPR_DATA directory')
 for name in ['metadata.sqlite','sources','artifacts']:
  p=source/name
  if p.is_dir():shutil.copytree(p,DATA/name)
  elif p.exists():shutil.copy2(p,DATA/name)
 # Rewrite only managed storage references after moving to a new root.
 with sqlite3.connect(DATA/'metadata.sqlite') as con:
  for ident,path,project,dataset in con.execute('SELECT id,path,project,dataset FROM files').fetchall():con.execute('UPDATE files SET path=? WHERE id=?',(str(DATA/'sources'/project/dataset/ident),ident))
  for ident,project,dataset,snapshot,status in con.execute('SELECT id,project,dataset,snapshot,status FROM runs').fetchall():
   snap=json.loads(snapshot)
   for f in snap['files']:f['path']=str(DATA/'sources'/project/dataset/f['id'])
   artifact=DATA/'artifacts'/project/dataset/ident
   con.execute('UPDATE runs SET artifact=?,snapshot=? WHERE id=?',(str(artifact) if artifact.exists() else None,json.dumps(snap),ident))
 for manifest in (DATA/'artifacts').rglob('manifest.json'):verify_manifest(manifest.parent)
 print('Восстановление и контрольные суммы проверены.')
