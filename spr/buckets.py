"""Bucket report v2: row denominators, typed keys, changed transitions only."""
import json


def bucket_report(mart):
    types = 'BUCKET_TYPE_' if getattr(mart, 'bucket_types', False) else 'TYPE_'
    fields = ', '.join(
        f"CASE WHEN {types}{side}='str' AND VALUE_{side}='' THEN 'null' ELSE {types}{side} END AS TYPE_{side}, "
        f"CASE WHEN {types}{side}='str' AND VALUE_{side}='' THEN 'NULL' ELSE ADJUSTED_{side} END AS ADJUSTED_{side}"
        for side in ('BEFORE', 'AFTER'))
    comparison = f'''WITH bucket_values AS (
        SELECT APP_ID,PATH,PRESENT_BEFORE,PRESENT_AFTER,{fields} FROM comparison
    )'''
    distribution = comparison + ''', values_by_side AS (
        SELECT APP_ID,PATH,'BEFORE' SOURCE,TYPE_BEFORE TYPE,ADJUSTED_BEFORE BUCKET
        FROM bucket_values WHERE PRESENT_BEFORE IS NOT NULL
        UNION ALL
        SELECT APP_ID,PATH,'AFTER',TYPE_AFTER,ADJUSTED_AFTER
        FROM bucket_values WHERE PRESENT_AFTER IS NOT NULL
    ), counts AS (
        SELECT PATH,SOURCE,TYPE,BUCKET,count(*) CNT,list_sort(list(DISTINCT APP_ID)) APP_IDS
        FROM values_by_side GROUP BY ALL
    )
    SELECT *,sum(CNT) OVER(PARTITION BY PATH,SOURCE) TOTAL FROM counts'''
    transitions = comparison + ''', counts AS (
        SELECT PATH,TYPE_BEFORE,TYPE_AFTER,ADJUSTED_BEFORE,ADJUSTED_AFTER,count(*) CNT,
               list_sort(list(DISTINCT APP_ID)) APP_IDS
        FROM bucket_values WHERE PRESENT_BEFORE IS NOT NULL AND PRESENT_AFTER IS NOT NULL GROUP BY ALL
    ), totals AS (
        SELECT *,sum(CNT) OVER(PARTITION BY PATH) TOTAL FROM counts
    )
    SELECT * FROM totals WHERE TYPE_BEFORE IS DISTINCT FROM TYPE_AFTER
                            OR ADJUSTED_BEFORE IS DISTINCT FROM ADJUSTED_AFTER'''

    def key(typ, value):
        return json.dumps([typ, value], ensure_ascii=False, separators=(',', ':'))

    result = []
    for transition, sql in ((False, distribution), (True, transitions)):
        for row in mart.query(sql):
            if transition:
                before, after = row['ADJUSTED_BEFORE'], row['ADJUSTED_AFTER']
                fields = dict(BUCKET_BEFORE=before, BUCKET_AFTER=after,
                              BUCKET_KEY_BEFORE=key(row['TYPE_BEFORE'], before),
                              BUCKET_KEY_AFTER=key(row['TYPE_AFTER'], after))
                argument = f'{before} → {after}'
            else:
                bucket = row['BUCKET']
                fields = dict(BUCKET=bucket, BUCKET_KEY=key(row['TYPE'], bucket))
                argument = str(bucket)
            result.append(dict(
                REPORT_VERSION=2, LABEL='BUCKET_TRANSITION' if transition else 'BUCKET_DISTRIBUTION',
                PATH=row['PATH'], SOURCE='AFTER' if transition else row['SOURCE'], ARGUMENT=row['PATH']+' · '+argument,
                CNT=row['CNT'], TOTAL=int(row['TOTAL']), METRIC=row['CNT']/row['TOTAL'] if row['TOTAL'] else 0,
                APP_IDS=row['APP_IDS'], UNIT='attribute_rows', **fields))
    return sorted(result, key=lambda row: (row['LABEL'], row['ARGUMENT'], row['SOURCE']))
