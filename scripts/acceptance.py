"""Run the full open dataset and compare the exact set of affected applications."""
import argparse
import csv
import json
import os
import platform
import sys
from pathlib import Path
from statistics import median
from tempfile import mkdtemp
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['SPR_TRUSTED_LOCAL_CODE'] = '1'

from spr.analytics import Mart
from spr.config import MATERIALS, ROOT
from spr.pipeline import process
from spr.sources import documents, unique_files


def measure_readers(target):
    pivot_times, drill_times = [], []
    for _ in range(20):
        started = perf_counter()
        with Mart(target) as mart:
            rows = mart.pivot(['PREFIX', 'CLASS', 'SOURCE', 'DIFF_FLAG'], {'DIFF_FLAG': 1}, 250)
            pivot_times.append((perf_counter() - started) * 1000)
            started = perf_counter()
            mart.drill({key: value for key, value in rows[0].items() if key != 'CNT'}, 50)
            drill_times.append((perf_counter() - started) * 1000)

    def stats(values):
        return {'median': round(median(values), 2), 'p95': round(sorted(values)[18], 2)}

    return {'samples': 20, 'pivot_open_and_query_ms': stats(pivot_times), 'drill_query_ms': stats(drill_times)}


def main():
    parser = argparse.ArgumentParser(description='Полная проверка открытого набора по эталону')
    parser.add_argument('--output', type=Path, help='Дополнительный путь для JSON-отчёта')
    args = parser.parse_args()

    source = MATERIALS / 'data/RUN-20260827-0001'
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    files = [dict(name=p.name, path=str(p), source=side, format='json')
             for side, spec in manifest['srez'].items() for p in documents(source / spec['dir'])]
    checks = [dict(id=p.stem, name=p.stem, code=p.read_text(encoding='utf-8'), params={})
              for p in unique_files(MATERIALS / 'checks', ('.py',)) if p.name.startswith('check_')]
    temp = ROOT / 'tmp'
    temp.mkdir(exist_ok=True)
    target = Path(mkdtemp(prefix='acceptance-', dir=temp))
    profile = json.loads((ROOT / 'profiles/main.json').read_text(encoding='utf-8'))
    summary = process(files, profile, target, checks=checks)

    reference = next(ROOT.glob('ХАК*/ПАКЕТ/ОРГАНИЗАТОРАМ/ЭТАЛОН/expected_OPEN.csv'))
    with reference.open(encoding='utf-8-sig', newline='') as stream:
        expected = {row['APP_ID']: row['CHANGED'] for row in csv.DictReader(stream, delimiter=';')}
    wanted = {ident for ident, changed in expected.items() if changed == '1'}
    with Mart(target) as mart:
        actual = set(mart.app_ids(diff_flag=1))
        assert actual == wanted, {'false_positive': sorted(actual - wanted), 'false_negative': sorted(wanted - actual)}
        assert summary['valid_pairs'] == len(expected) == 2002 and summary['files'] == 4004, summary
        kinds = dict(mart.con.execute('SELECT CHANGE_TYPE, count(*) FROM comparison GROUP BY 1').fetchall())
        assert set(kinds) == {'SAME', 'VALUE_CHANGED', 'ATTRIBUTE_NEW', 'ATTRIBUTE_LOST'}, kinds
        pivot_rows = mart.pivot(['PRODUCT', 'SOURCE', 'DIFF_FLAG'], {}, 10000)
        for row in pivot_rows:
            filters = {key: value for key, value in row.items() if key != 'CNT'}
            assert mart.drill(filters)['total'] == row['CNT'], row

    results = json.loads((target / 'checks.json').read_bytes())
    errors = json.loads((target / 'check_errors.json').read_bytes())
    assert not errors and len(checks) == 5 and {row['CHECK_ID'] for row in results} == {c['id'] for c in checks}, errors
    report = {
        'dataset': source.name,
        'summary': summary,
        'reference': {'changed_apps': len(wanted), 'false_positive': len(actual - wanted),
                      'false_negative': len(wanted - actual)},
        'reference_ids_match': True,
        'change_types': kinds,
        'checks_selected': len(checks),
        'check_rows': len(results),
        'check_statuses': {status: sum(row['RESULT'] == status for row in results) for status in ('PASS', 'FAIL')},
        'check_errors': len(errors),
        'pivot_groups_verified': len(pivot_rows),
        'timings_seconds': json.loads((target / 'timings.json').read_bytes()),
        'environment': {'platform': platform.platform(), 'python': platform.python_version()},
        'reader_timings': measure_readers(target),
        'artifacts': target.relative_to(ROOT).as_posix(),
        'check_results': results,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    (target / 'acceptance-result.json').write_text(encoded, encoding='utf-8')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'check_results'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
