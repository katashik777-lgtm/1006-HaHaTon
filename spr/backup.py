"""Run while API and worker are stopped to obtain one consistent generation."""
import sqlite3,shutil,json,hashlib
from pathlib import Path
from .config import DATA,DATABASE
from .pipeline import verify_manifest
from .paths import storage_ref,portable_snapshot,portable_origin
from sqlalchemy.engine import make_url

def backup(destination):
 destination=destination.resolve()
 if destination.exists():raise ValueError('Destination must not exist')
 if DATA==destination or DATA in destination.parents:raise ValueError('Backup outside live storage')
 if not DATABASE.startswith('sqlite'):raise ValueError('PostgreSQL: use documented pg_dump coordinated procedure')
 if Path(make_url(DATABASE).database).resolve()!=(DATA/'metadata.sqlite').resolve():raise ValueError('Backup supports only SPR_DATA/metadata.sqlite')
 if not (DATA/'metadata.sqlite').is_file():raise ValueError('Metadata database does not exist')
 destination.mkdir(parents=True,mode=0o700)
 with sqlite3.connect(DATA/'metadata.sqlite') as src,sqlite3.connect(destination/'metadata.sqlite') as dst:src.backup(dst)
 for name in ['sources','artifacts']:
  if (DATA/name).exists():shutil.copytree(DATA/name,destination/name)
 hashes={p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob('*') if p.is_file()}
 (destination/'backup.json').write_text(json.dumps(hashes));print('Резервная копия:',destination)

def restore(source):
 source=source.resolve();hashes={name.replace('\\','/'):digest for name,digest in json.loads((source/'backup.json').read_text(encoding='utf-8')).items()}
 actual={p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and p!=source/'backup.json'}
 if actual!=set(hashes):raise ValueError('Backup file inventory mismatch')
 if 'metadata.sqlite' not in hashes:raise ValueError('Backup has no metadata database')
 if any(p.is_symlink() for p in source.rglob('*')):raise ValueError('Backup must not contain symlinks')
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
  for ident,project,dataset in con.execute('SELECT id,project,dataset FROM files').fetchall():
   con.execute('UPDATE files SET path=? WHERE id=?',(storage_ref('sources',project,dataset,ident).as_posix(),ident))
  for ident,project,dataset,snapshot,status in con.execute('SELECT id,project,dataset,snapshot,status FROM runs').fetchall():
   snap=portable_snapshot(json.loads(snapshot),project,dataset)
   artifact=DATA/'artifacts'/project/dataset/ident
   con.execute('UPDATE runs SET artifact=?,snapshot=? WHERE id=?',(storage_ref('artifacts',project,dataset,ident).as_posix() if artifact.exists() else None,json.dumps(snap),ident))
  for ident,body in con.execute("SELECT id,body FROM records WHERE kind='dataset'").fetchall():
   con.execute('UPDATE records SET body=? WHERE id=?',(json.dumps(portable_origin(json.loads(body))),ident))
 for manifest in (DATA/'artifacts').rglob('manifest.json'):verify_manifest(manifest.parent)
 print('Восстановление и контрольные суммы проверены.')
