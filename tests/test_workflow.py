"""Exercise HTTP authorization, durable jobs, recovery and backup together."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from spr import api, worker, backup as backups
from spr.db import Base, User, Member, Record, Run, uid
from spr.security import hash_password


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


def test_full_workflow_and_crash_recovery(system):
    client, project, sessions, data = system
    runid = enqueue(system)
    assert client.get(f'/api/runs/{runid}/overview').status_code == 409
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(4)))
    jobs = [j for j in claims if j]
    assert len(jobs) == 1
    worker.work(*jobs[0])
    result = client.get(f'/api/runs/{runid}').json()
    assert result['status'] == 'completed', result
    overview = client.get(f'/api/runs/{runid}/overview').json()
    assert set(overview['impact']) == {'PRODUCT', 'PARTNER', 'ROUTING_METHOD'}
    unchanged = client.post(f'/api/runs/{runid}/drill', json={'filters': {'DIFF_FLAG': 1, 'DECISION_CHANGED': 0}}).json()
    assert unchanged['total'] == overview['changed_without_decision']
    assert client.get(f'/api/runs/{runid}/factor-changes').status_code == 200
    exported_html = client.get(f'/api/runs/{runid}/export?format=html').text
    assert 'Метрика' in exported_html and 'Аргумент' in exported_html
    assert 'tab=pivot' in exported_html

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
        for path, in con.execute('SELECT path FROM files'):
            assert str(restored) in path
            assert __import__('pathlib').Path(path).exists()
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
        manifest = Path(s.get(Run, run).artifact) / 'manifest.json'
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
