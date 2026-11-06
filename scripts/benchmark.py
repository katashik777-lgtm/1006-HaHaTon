"""Reproducible measurements; synthetic input is explicitly labelled."""
import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spr.config import MATERIALS, ROOT
from spr.pipeline import process
from spr.analytics import Mart


def measure(call, repeats=20):
    values = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        values.append(time.perf_counter() - start)
    return {'median_seconds': statistics.median(values), 'p95_seconds': sorted(values)[int(.95 * (len(values)-1))], 'samples': len(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--synthetic', type=int, default=0)
    parser.add_argument('--read-workers', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    files = []
    if args.synthetic:
        source = args.output / 'input'
        source.mkdir()
        profile = {'app_id': 'id', 'decision_path': 'decision', 'arrays': {'models': {'mode': 'key', 'keys': ['name']}}}
        for i in range(args.synthetic):
            for stage in ('BEFORE', 'AFTER'):
                after = stage == 'AFTER'
                doc = {'id': str(i), 'decision': 'REJECT' if after and i % 7 == 0 else 'APPROVE',
                       'score': (i % 100) / 100 + (.01 if after and i % 3 == 0 else 0),
                       'models': [{'name': 'pd', 'version': 'v2' if after and i % 5 == 0 else 'v1', 'value': i % 10}],
                       'flag': None if i % 2 else False}
                if after and i % 11 == 0:
                    doc['new'] = True
                path = source / f'{i}-{stage}.json'
                path.write_text(json.dumps(doc), encoding='utf-8')
                files.append({'name': f'{i}.json', 'source': stage, 'path': str(path), 'format': 'json'})
    else:
        source = MATERIALS / 'data/RUN-20260827-0001'
        manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
        for stage, spec in manifest['srez'].items():
            files.extend({'name': p.name, 'source': stage, 'path': str(p), 'format': 'json'} for p in sorted((source / spec['dir']).glob('*.json')))
        profile = json.loads((ROOT / 'profiles/main.json').read_text(encoding='utf-8'))
    target = args.output / 'artifacts'
    summary = process(files, profile, target,read_workers=args.read_workers)
    def query():
        mart = Mart(target)
        try:
            return mart.pivot(['KEY', 'SOURCE', 'DIFF_FLAG'], {})
        finally:
            mart.close()
    mart = Mart(target)
    try:
        report = {'dataset': f'synthetic-{args.synthetic}' if args.synthetic else 'open-RUN-20260827-0001',
                  'platform': platform.platform(), 'python': platform.python_version(), 'cpu': platform.processor(),
                  'logical_cpus': os.cpu_count(), 'polars_threads': __import__('polars').thread_pool_size(),
                  'read_workers': args.read_workers,
                  'cache': 'OS cache not purged; first pipeline run, subsequent warm queries',
                  'summary': summary, 'pipeline_seconds': json.loads((target / 'timings.json').read_text()),
                  'pivot_new_connection': measure(query),
                  'drill': measure(lambda: mart.drill({'DIFF_FLAG': 1})),
                  'artifact_bytes': sum(p.stat().st_size for p in target.iterdir()),
                  'limitations': ['Pipeline measured once; no pipeline p95', 'No browser time or peak RSS measurement', 'No claim of cold disk cache']}
    finally:
        mart.close()
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
