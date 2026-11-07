"""Compare bounded spawn workers without sending normalized rows through IPC."""
import hashlib
import json
import multiprocessing
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spr.config import MATERIALS, ROOT
from spr.normalize import parse, normalize
import orjson


def batch(task):
    paths, profile = task
    digest = hashlib.sha256()
    count = 0
    for path in paths:
        p = Path(path)
        rows = normalize(parse(p.read_bytes(), 'json', profile), p.stem, 'BEFORE', profile)
        digest.update(orjson.dumps(rows))
        count += len(rows)
    return count, digest.hexdigest()


if __name__ == '__main__':
    source = MATERIALS / 'data/RUN-20260827-0001'
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    paths = sorted(str(p) for spec in manifest['srez'].values() for p in (source / spec['dir']).glob('*.json'))
    assert paths, 'No input files found'
    profile = json.loads((ROOT / 'profiles/main.json').read_text(encoding='utf-8'))
    tasks = [(paths[i:i+32], profile) for i in range(0, len(paths), 32)]
    start = time.perf_counter()
    sequential = list(map(batch, tasks))
    sequential_time = time.perf_counter() - start
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
        parallel = list(pool.map(batch, tasks))
    parallel_time = time.perf_counter() - start
    assert sequential == parallel
    report = {'files': len(paths), 'rows': sum(r[0] for r in sequential),
              'sequential_seconds': sequential_time, 'spawn_4_seconds': parallel_time,
              'batch_size': 32, 'equivalent': True,
              'scope': 'Read, parse, normalize and hash; excludes dataframe construction, IPC of rows and Parquet publication. OS cache not purged.'}
    Path('tmp/benchmark-parsing.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
