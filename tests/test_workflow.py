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
    assert result['summary']['changed_apps'] == 1
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
