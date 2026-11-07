import pytest
from spr.normalize import parse, normalize, frame, compare
from spr.analytics import Mart

@pytest.mark.parametrize('fmt,raw', [('json',b'{"score":0.42}'),('xml',b'<root><score>0.42</score></root>'),('csv',b'score\n0.42\n')])
def test_formats_and_same_bucket_change(tmp_path,fmt,raw):
 profile={'types':{'score':'number'},'buckets':{'score':[{'min':.3,'max':.6,'label':'(0.3 – 0.6]'}]}}
 before=normalize(parse(raw,fmt,profile),'a','BEFORE',profile)
 after=normalize({'score':.58},'a','AFTER',profile)
 comparison=compare(frame(before+after),['a'])
 row=comparison.to_dicts()[0]
 assert row['ADJUSTED_BEFORE']==row['ADJUSTED_AFTER']=='(0.3 – 0.6]'
 assert row['CHANGE_TYPE']=='VALUE_CHANGED' and row['DIFF_FLAG']==1
 comparison.write_parquet(tmp_path/'comparison.parquet')
 with Mart(tmp_path) as m:
  report=m.bucket_report()
  assert len(report)==2
  assert all(r['CNT']==r['TOTAL']==1 and r['APP_IDS']==['a'] and r['METRIC']==1 for r in report)
  assert report is m.bucket_report()

def test_default_intervals_and_strings():
 rows=normalize({'score':.42,'category':'A'},'a','BEFORE',{})
 assert rows[0]['ADJUSTED_VALUE']=='(0.4 – 0.6]'
 assert rows[1]['ADJUSTED_VALUE']=='A'

def test_product_stop_count():
 from spr.pipeline import product_rows
 doc={'decisionResult':{'product_trials':[{'product_id':'p','rules':[{'hit':True},{'hit':False},{'hit':True}]}]}}
 rows=product_rows('a',doc,doc,{})
 assert all(r['STOP'] and r['STOP_COUNT']==2 for r in rows)


def test_reference_distribution(tmp_path):
 profile={'buckets':{'score':[{'min':.3,'max':.6,'label':'A'},{'min':.6,'max':.9,'label':'B'}]}}
 rows=[]
 for i,(b,a) in enumerate([(.42,.58),(.55,.70),(.73,.75),(.60,.60)],1):
  rows+=normalize({'score':b},f'app-{i}','BEFORE',profile)+normalize({'score':a},f'app-{i}','AFTER',profile)
 compare(frame(rows),[f'app-{i}' for i in range(1,5)]).write_parquet(tmp_path/'comparison.parquet')
 with Mart(tmp_path) as m:report=m.bucket_report()
 transition=[r for r in report if r['LABEL']=='BUCKET_TRANSITION']
 assert len(transition)==1
 assert (transition[0]['CNT'],transition[0]['TOTAL'],transition[0]['METRIC'],transition[0]['APP_IDS'])==(1,4,.25,['app-2'])
 counts={(r['SOURCE'],r['BUCKET']):r['CNT'] for r in report if r['LABEL']=='BUCKET_DISTRIBUTION'}
 assert counts=={('BEFORE','A'):3,('BEFORE','B'):1,('AFTER','A'):2,('AFTER','B'):2}


def test_exact_decimal_boundary():
 from spr.bucket_config import adjusted
 profile={'bucket_config':{'score':{'width':.2,'threshold':.6,'radius':.1}}}
 assert adjusted('0.60','number','score',profile)=='(0.5 – 0.6]'
 assert adjusted('0.6001','number','score',profile)=='(0.6 – 0.7]'
 assert adjusted('0012','str','score',profile)=='0012'


def test_threshold_zone_is_contiguous_off_the_grid():
    from spr.bucket_config import adjusted
    profile = {'bucket_config':{'score':{'width':.2,'threshold':.55,'radius':.1}}}
    assert adjusted('0.59','number','score',profile) == '(0.55 – 0.65]'
    assert adjusted('0.61','number','score',profile) == '(0.55 – 0.65]'
    assert adjusted('0.6501','number','score',profile) == '(0.65 – 0.8]'


def test_empty_value_and_literal_null_have_distinct_bucket_keys(tmp_path):
    rows = normalize({'a':''},'a','BEFORE',{}) + normalize({'a':'NULL'},'a','AFTER',{})
    compare(frame(rows),['a']).write_parquet(tmp_path/'comparison.parquet')
    with Mart(tmp_path) as mart:
        report = mart.bucket_report()
    transition = next(r for r in report if r['LABEL']=='BUCKET_TRANSITION')
    assert transition['BUCKET_KEY_BEFORE'] == '["null","NULL"]'
    assert transition['BUCKET_KEY_AFTER'] == '["str","NULL"]'
