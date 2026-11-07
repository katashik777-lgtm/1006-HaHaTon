"""Exercise HTTP authorization, durable jobs, recovery and backup together."""
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from xml.etree.ElementTree import fromstring

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spr import api, worker, backup as backups
from spr.db import Base, User, Member, Record, Run, File, uid
from spr.security import hash_password
from spr.paths import storage_path


@pytest.fixture
def system(tmp_path, monkeypatch):
    data = tmp_path / 'live'
    data.mkdir()
    database = 'sqlite:///' + str(data / 'metadata.sqlite')
    engine = create_engine(database, connect_args={'check_same_thread': False, 'timeout': 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    for module in (api, worker):
        monkeypatch.setattr(module, 'Session', sessions)
        monkeypatch.setattr(module, 'DATA', data)
    monkeypatch.setattr(backups, 'DATA', data)
    monkeypatch.setattr(backups, 'DATABASE', database)
    with sessions.begin() as s:
        s.add_all([User(id='admin', name='admin', password=hash_password('workflow-password'), admin=1),
                   User(id='observer', name='observer', password=hash_password('workflow-password')),
                   User(id='outsider', name='outsider', password=hash_password('workflow-password'))])
    client = TestClient(api.app)
    assert client.post('/api/login', json={'name': 'admin', 'password': 'workflow-password'}).status_code == 200
    project = client.post('/api/projects', json={'name': 'Тест'}).json()['id']
    with sessions.begin() as s:
        s.add(Member(user_id='observer', project=project, role='observer'))
    yield client, project, sessions, data
    client.close()
    engine.dispose()


def record(client, project, kind, body):
    r = client.post(f'/api/projects/{project}/records', json={'kind': kind, 'name': kind, 'body': body})
    assert r.status_code == 200, r.text
    return r.json()['id']


def enqueue(system):
    client, project, sessions, data = system
    dataset = record(client, project, 'dataset', {})
    profile = record(client, project, 'profile', {'app_id': 'id', 'decision_path': 'decision'})
    for source, score in [('BEFORE', 1), ('AFTER', 2)]:
        response = client.post(f'/api/projects/{project}/upload', data={'dataset': dataset, 'source': source},
                               files=[('files', ('a.json', json.dumps({'id': 'a', 'decision': 'YES', 'score': score}), 'application/json'))])
        assert response.status_code == 200, response.text
    result = client.post(f'/api/projects/{project}/runs', json={'dataset': dataset, 'profile': profile})
    assert result.status_code == 200, result.text
    return result.json()['id']


def test_overview_groups_the_same_applications_instead_of_equal_counts(system):
    client, project, _, _ = system
    dataset = record(client, project, 'dataset', {})
    profile = record(client, project, 'profile', {'app_id': 'id', 'decision_path': 'decision'})
    for source, changed in [('BEFORE', 0), ('AFTER', 1)]:
        documents = [{'id': 'a', 'decision': 'APPROVE', 'left': changed, 'also_left': changed},
                     {'id': 'b', 'decision': 'APPROVE', 'right': changed}]
        files = [('files', (doc['id']+'.json', json.dumps(doc), 'application/json')) for doc in documents]
        response = client.post(f'/api/projects/{project}/upload', data={'dataset': dataset, 'source': source}, files=files)
        assert response.status_code == 200
    run = client.post(f'/api/projects/{project}/runs', json={'dataset': dataset, 'profile': profile}).json()['id']
    worker.work(*worker.claim())
    data = client.get(f'/api/runs/{run}/overview', params={'analysis': '{}'}).json()
    assert data['top_groups'] == [{'CNT': 1, 'PATHS': ['also_left','left']}, {'CNT': 1, 'PATHS': ['right']}]
    result = client.post(f'/api/runs/{run}/drill', json={
        'filters': {'path': data['top_groups'][0]['PATHS'], 'diff_flag': 1}, 'analysis': {}}).json()
    assert result['total'] == 1 and result['items'][0]['APP_ID'] == 'a'


def test_full_workflow_and_crash_recovery(system):
    client, project, sessions, data = system
    runid = enqueue(system)
    assert client.get(f'/api/runs/{runid}/overview').status_code == 409
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(4)))
    jobs = [j for j in claims if j]
    assert len(jobs) == 1
    with sessions.begin() as s:
        saved = s.get(Run, runid)
        saved.snapshot = {**saved.snapshot, 'files': [
            {**row, 'path': 'C:/old-project/' + row['path']} for row in saved.snapshot['files']
        ]}
        for entry in s.scalars(select(File)):
            entry.path = '/old-project/' + entry.path

    worker.work(*jobs[0])
    result = client.get(f'/api/runs/{runid}').json()
    assert result['status'] == 'completed', result
    assert result['summary']['valid_pairs'] == 1
    with sessions.begin() as s:
        s.get(Run, runid).artifact = 'C:/old-project/artifacts/old-run'
    overview = client.get(f'/api/runs/{runid}/overview').json()
    compact = client.get(f'/api/runs/{runid}/overview', params={'compact': True, 'analysis': '{}'}).json()
    assert set(compact) == {'selection', 'transitions'}
    assert compact['selection']['valid_pairs'] == 1
    assert compact['transitions'] == overview['transitions']
    files = client.get(f'/api/projects/{project}/files', params={'dataset': result['dataset'], 'summary': True}).json()
    assert files == {'BEFORE': 1, 'AFTER': 1}
    assert set(overview['impact']) == {'PRODUCT', 'PARTNER', 'ROUTING_METHOD'}
    unchanged = client.post(f'/api/runs/{runid}/drill', json={'filters': {'DIFF_FLAG': 1, 'DECISION_CHANGED': 0}}).json()
    assert unchanged['total'] == overview['changed_without_decision']
    assert client.get(f'/api/runs/{runid}/factor-changes').status_code == 200
    exported_html = client.get(f'/api/runs/{runid}/export?format=html').text
    assert 'Метрика' in exported_html and 'Аргумент' in exported_html
    assert 'tab=pivot' in exported_html
    assert '<html lang="ru">' in exported_html and 'Детали изменений' in exported_html
    checks_html = client.get(f'/api/runs/{runid}/export?format=checks-html')
    assert checks_html.status_code == 200 and 'scorify-checks-' in checks_html.headers['Content-Disposition']
    assert 'ОТЧЁТ ПРОВЕРОК' in checks_html.text and 'tab=checks' in checks_html.text
    assert 'Детали изменений' not in checks_html.text
    response = client.get(f'/api/runs/{runid}/export', params={'format': 'xlsx', 'dimensions': '["KEY"]'})
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames[:2] == ['Сводная таблица','Исходные данные']
    assert workbook['Сводная таблица']['G5'].value.startswith('=SUMPRODUCT(')
    assert workbook['Исходные данные']['I3'].value == 'CNT'
    assert workbook['Исходные данные'].auto_filter.ref.startswith('A3:I')
    assert dict(workbook['Обзор'].values)['Валидных пар'] == 1
    assert workbook['Заявки']['A4'].value == 'a'
    assert workbook['Заявки'].freeze_panes == 'A4'
    assert workbook['Заявки'].auto_filter.ref == 'A3:E4'
    assert workbook['Детали изменений']['B4'].value == 'score'
    assert workbook['Сводная']['A3'].value == 'KEY'
    filtered = client.get(f'/api/runs/{runid}/export', params={'format': 'xlsx', 'analysis': '{"product":"absent"}'})
    assert filtered.status_code == 200
    filtered_book = load_workbook(BytesIO(filtered.content))
    assert dict(filtered_book['Обзор'].values)['Валидных пар'] == 0
    assert filtered_book['Заявки'].max_row == 3
    xml = client.get(f'/api/runs/{runid}/export?format=xml')
    report = fromstring(xml.content)
    assert report.find('./facts/fact/CNT').text == '1'
    assert report.find('./statistics').get('unit') == 'attribute_rows'
    assert not report.findall('./statistics/bucket/RESULT')
    assert all(row.text in ('PASS','FAIL') for row in report.findall('./checks/check/RESULT'))
    facts = client.get(f'/api/runs/{runid}/export?format=json&flat=true').json()
    assert {row['SOURCE'] for row in facts} == {'BEFORE (MASTER)', 'AFTER (BETA)'}

    assert result['summary']['changed_apps'] == 1
    assert result['timings']['read_parse_normalize'] > 0
    assert result['timings']['total'] >= result['timings']['read_parse_normalize']
    reading = next(entry for entry in result['log'] if entry['stage'] == 'Чтение и нормализация')
    assert reading['duration_seconds'] > 0
    assert reading['detail']['files'] >= 1
    pivot = client.post(f'/api/runs/{runid}/pivot', json={'dimensions': ['KEY', 'SOURCE', 'DIFF_FLAG']}).json()
    for row in pivot['items']:
        filters = {k: v for k, v in row.items() if k != 'CNT'}
        drill = client.post(f'/api/runs/{runid}/drill', json={'filters': filters}).json()
        assert drill['total'] == row['CNT']
    detail = client.get(f'/api/runs/{runid}/apps/a').json()
    fileid = detail['quality'][0]['FILE_ID']
    assert client.get('/api/files/' + fileid).status_code == 200
    assert client.get(f'/api/runs/{runid}/export').status_code == 200
    assert client.get(f'/api/runs/{runid}/export?format=csv').content.startswith(b'\xef\xbb\xbf')
    exported = client.get(f'/api/runs/{runid}/export', params={'format': 'json', 'dimensions': '["KEY","SOURCE"]', 'filters': '{"KEY":"score"}'}).json()
    assert exported['filters'] == {'KEY': 'score'}
    assert len(exported['items']) == 2
    assert all(row['KEY'] == 'score' and row['CNT'] == 1 for row in exported['items'])
    assert client.get(f'/api/runs/{runid}/export', params={'format': 'json', 'filters': '{'}).status_code == 422
    # Files were published but SQL commit was lost: recover the same immutable run.
    with sessions.begin() as s:
        run = s.get(Run, runid)
        run.status, run.lease = 'running', 0
    worker.work(*worker.claim())
    assert client.get(f'/api/runs/{runid}').json()['status'] == 'completed'
    for name, read_code in [('observer', 200), ('outsider', 403)]:
        with TestClient(api.app) as other:
            other.post('/api/login', json={'name': name, 'password': 'workflow-password'})
            assert other.get(f'/api/runs/{runid}').status_code == read_code
            assert other.get('/api/files/' + fileid).status_code == read_code
            assert other.get(f'/api/runs/{runid}/export').status_code == read_code
            assert other.post(f'/api/runs/{runid}/retry', json={}).status_code == 403
    backup = data.parent / 'backup'
    backups.backup(backup)
    # A backup made on Windows uses backslashes in older backup.json files.
    manifest = backup / 'backup.json'
    manifest.write_text(json.dumps({k.replace('/', '\\'): v for k, v in json.loads(manifest.read_text()).items()}))
    restored = data.parent / 'restored'
    restored.mkdir()
    backups.DATA = restored
    backups.restore(backup)
    assert (restored / 'metadata.sqlite').exists()
    import sqlite3
    with sqlite3.connect(restored / 'metadata.sqlite') as con:
        for path, project_id, dataset_id, file_id in con.execute('SELECT path,project,dataset,id FROM files'):
            assert path == f'var/sources/{project_id}/{dataset_id}/{file_id}'
            assert storage_path(restored, 'sources', project_id, dataset_id, file_id).exists()
    (backup / 'unlisted.txt').write_text('not covered by backup manifest')
    with pytest.raises(ValueError, match='inventory'):
        backups.restore(backup)


def test_cancel_and_invalid_filters(system):
    client, project, sessions, data = system
    runid = enqueue(system)
    assert client.post(f'/api/runs/{runid}/cancel', json={}).status_code == 200
    assert worker.claim() is None
    retry = client.post(f'/api/runs/{runid}/retry', json={}).json()['id']
    worker.work(*worker.claim())
    response = client.post(f'/api/runs/{retry}/pivot', json={'dimensions': ['bad']})
    assert response.status_code == 422
    for invalid in [{'dimensions': 5}, {'filters': []}]:
        assert client.post(f'/api/runs/{retry}/pivot', json=invalid).status_code == 422
    assert client.post(f'/api/runs/{retry}/pivot', json={'limit': -1}).status_code == 200


def test_shared_analysis_filters_and_score_projection(system):
    client, project, sessions, _ = system
    dataset = record(client, project, 'dataset', {})
    trials = 'decisionResult.product_trials'
    score_path = trials + '.scoring.pd.value'
    profile = record(client, project, 'profile', {
        'app_id': 'id', 'decision_path': 'decisionResult.decision',
        'arrays': {trials: {'mode': 'key', 'keys': ['product_id']}},
        'bucket_width': .2, 'decision_analysis': {'direction': 'ge'},
    })
    for side in ('BEFORE', 'AFTER'):
        files = []
        for i in range(28):
            score = .85 if side == 'AFTER' else .65
            factors = [{'name': 'A', 'hit': i < 2}, {'name': 'B', 'hit': i == 0}]
            doc = {'id': str(i), 'decisionResult': {'decision': 'APPROVE', 'product_trials': [
                {'product_id': '1', 'decision': 'APPROVE', 'scoring': {'pd': {'value': score}},
                 'cutOff': {'value': .6}, 'rules': factors}
            ]}}
            files.append(('files', (f'{i}.json', json.dumps(doc), 'application/json')))
        res = client.post(f'/api/projects/{project}/upload', data={'dataset':dataset, 'source':side}, files=files)
        assert res.json() == {'stored':28}
    run = client.post(f'/api/projects/{project}/runs', json={'dataset':dataset, 'profile':profile}).json()['id']
    worker.work(*worker.claim())
    root = '/api/runs/' + run
    with sessions() as s:
        from pathlib import Path
        saved = s.get(Run, run)
        assert saved.artifact == f'var/artifacts/{project}/{dataset}/{run}'
        assert all(f['path'].startswith('var/sources/') for f in saved.snapshot['files'])
        manifest = storage_path(system[3], 'artifacts', project, dataset, run) / 'manifest.json'
        original = manifest.read_bytes()

    def get(path, options):
        response = client.get(root + path, params={'analysis':json.dumps(options)})
        assert response.status_code == 200, response.text
        return response.json()

    def post(path, options, **body):
        response = client.post(root + path, json={'analysis':options, **body})
        assert response.status_code == 200, response.text
        return response.json()

    page = post('/decision-analysis', {}, offset=25)
    assert page['total'] == 28 and len(page['items']) == 3
    assert post('/decision-analysis', {'factors':['A','B']})['total'] == 2
    options = {'basis':'scenario', 'mode':'penalty', 'penalty':'.1', 'factors':['A','B'],
               'factor_match':'all', 'side':'AFTER'}
    overview = get('/overview', options)
    assert overview['transitions'] == [{'DECISION_BEFORE':'REJECT','DECISION_AFTER':'APPROVE','CNT':1}]
    row = post('/decision-analysis', options)['items'][0]
    assert row['AFTER']['EFFECTIVE_SCORE'] == .65 and row['AFTER']['PENALTY'] == .2
    assert row['AFTER']['HISTORICAL'] == row['AFTER']['EXPECTED'] == 'APPROVE'
    hard = post('/decision-analysis', {**options, 'mode':'hard'})['items'][0]
    assert hard['AFTER']['EXPECTED'] == 'REJECT' and hard['AFTER']['REASON'] == 'STOP_FACTOR'
    assert row['BEFORE']['REASON'] == 'SCORE_CUTOFF'
    transition = next(r for r in get('/checks', options) if r['PATH'] == score_path and r['LABEL'] == 'BUCKET_TRANSITION')
    assert (transition['CNT'], transition['TOTAL'], transition['APP_IDS']) == (1,1,['0'])
    assert (transition['BUCKET_BEFORE'], transition['BUCKET_AFTER']) == ('(0.4 – 0.6]', '(0.6 – 0.8]')
    pivot = post('/pivot', options, filters={'PATH':score_path}, dimensions=['SOURCE','VALUE_ADJUSTED'])['items']
    assert {r['VALUE_ADJUSTED'] for r in pivot} == {transition['BUCKET_BEFORE'], transition['BUCKET_AFTER']}
    for r in pivot:
        drill = post('/drill', options, filters={k:v for k,v in r.items() if k!='CNT'})
        assert drill == {'total':1,'items':[{'APP_ID':'0'}]}
    card = get('/apps/0', options)
    attribute = next(r for r in card['comparison'] if r['PATH'] == score_path)
    assert (attribute['VALUE_BEFORE'], attribute['VALUE_AFTER'], attribute['DIFF_FLAG']) == ('0.65','0.85',1)
    assert [r['CNT'] for r in get('/quality-summary', options)] == [1,1]
    assert get('/quality-summary', {'minimum':'1'}) == []
    assert client.post(root+'/decision-analysis', json={'analysis':{'minimum':'0.9','maximum':'0.8'}}).status_code == 422
    assert manifest.read_bytes() == original


def test_upload_batch_is_atomic(system):
    client, project, _, _ = system
    dataset = record(client, project, 'dataset', {})
    result = client.post(f'/api/projects/{project}/upload', data={'dataset':dataset,'source':'BEFORE'},
                         files=[('files',('ok.json','{}','application/json')),('files',('bad.txt','{}','text/plain'))])
    assert result.status_code == 422
    assert client.get(f'/api/projects/{project}/files', params={'dataset':dataset}).json() == []
