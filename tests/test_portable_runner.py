from spr.runner import execute, capability
from test_engine import artifact


def test_trusted_unicode_result(artifact, monkeypatch):
    monkeypatch.setenv('SPR_TRUSTED_LOCAL_CODE', '1')
    assert capability()
    code = '''
def run(mart, params):
    print("Проверка выполнена ✓")
    ids = mart.app_ids(diff_flag=1)
    return [dict(LABEL="Изменения", SOURCE="AFTER", ARGUMENT="балл", CNT=len(ids),
                 TOTAL=mart.total_apps(), METRIC=1, APP_IDS=ids, RESULT="FAIL", CONFIDENCE="HIGH")]
CHECK = {"entrypoint": run}
'''
    result = execute(code, {}, artifact[0])
    assert 'error' not in result, result
    assert result['rows'][0]['LABEL'] == 'Изменения'
    assert result['rows'][0]['APP_IDS'] == ['a', 'b']
    assert 'Проверка выполнена ✓' in result['stdout']


def test_trusted_exception(artifact, monkeypatch):
    monkeypatch.setenv('SPR_TRUSTED_LOCAL_CODE', '1')
    result = execute('raise ValueError("Ошибка проверки")', {}, artifact[0])
    assert result['error'] == 'Ошибка проверки'
    assert 'ValueError' in result['traceback']
