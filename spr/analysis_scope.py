"""One application selection and score projection over an immutable published run."""
import json
from collections import defaultdict
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if __package__:
    from .bucket_config import adjusted
else:
    from bucket_config import adjusted

SIDES = ('BEFORE', 'AFTER')
DIRECTIONS = {'le': lambda d: d <= 0, 'lt': lambda d: d < 0,
              'ge': lambda d: d >= 0, 'gt': lambda d: d > 0}
REASONS = {'STOP_FACTOR': 'Стоп-фактор', 'SCORE_CUTOFF': 'Порог Score не пройден',
           'INCOMPLETE': 'Недостаточно данных', 'PENALTY_UNSET': 'Штраф не задан',
           'SCORE_PASS': 'Порог Score пройден', 'DIRECTION_UNSET': 'Направление порога не задано'}


def model_direction(config, model, override=''):
    direction = override or config.get('models', {}).get(model, {}).get('direction', config.get('direction'))
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError('Недопустимое правило порога в профиле')
    return direction


class AnalysisOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    basis: Literal['historical', 'scenario'] = 'historical'
    mode: Literal['hard', 'penalty'] = 'hard'
    direction: Literal['', 'le', 'lt', 'ge', 'gt'] = ''
    penalty: Decimal | None = Field(default=None, ge=0)
    side: Literal['BEFORE', 'AFTER', 'either', 'both'] = 'either'
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    near: bool = False
    distance: Decimal | None = Field(default=None, ge=0)
    position: Literal['any', 'below', 'equal', 'above'] = 'any'
    stop: Literal['', 'yes', 'no', 'unknown'] = ''
    factors: list[str] = Field(default_factory=list, max_length=500)
    factor_match: Literal['any', 'all'] = 'any'
    reason: str = ''
    before: str = ''
    after: str = ''
    transition: Literal['', 'changed', 'unchanged', 'new_reject', 'left_reject'] = ''
    product: str = ''

    @model_validator(mode='after')
    def validate_range(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError('Нижняя граница Score превышает верхнюю')
        return self

    @property
    def side_filter(self):
        return (self.minimum is not None or self.maximum is not None or self.near
                or bool(self.stop or self.factors or self.reason))

    @property
    def filtered(self):
        return self.side_filter or bool(self.before or self.after or self.transition or self.product)


def parse_options(value):
    try:
        return AnalysisOptions.model_validate_json(value) if isinstance(value, str) else AnalysisOptions.model_validate(value)
    except ValidationError as exc:
        raise ValueError('Некорректные параметры выборки. ' + exc.errors()[0]['msg']) from exc


def number(value):
    return Decimal(str(value)) if value is not None else None


def names(row):
    value = row.get('STOP_NAMES')
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except ValueError:
        return []
    return decoded if isinstance(decoded, list) and all(isinstance(v, str) for v in decoded) else []


def register_rows(mart, name, rows, columns):
    """Load a small derived relation in one call, with stable types even when empty."""
    projection = ', '.join(f'CAST({col} AS {typ}) AS {col}' for col, typ in columns.items())
    if rows:
        mart.con.execute(f'CREATE TEMP TABLE {name} AS SELECT {projection} FROM (SELECT unnest(?, recursive := true))', [rows])
    else:
        empty = ', '.join(f'CAST(NULL AS {typ}) AS {col}' for col, typ in columns.items())
        mart.con.execute(f'CREATE TEMP TABLE {name} AS SELECT {empty} WHERE false')


class AnalysisScope:
    def __init__(self, mart, options, valid_pairs_only=False):
        self.options = options
        self.valid_pairs_only = valid_pairs_only
        self.profile = json.loads((mart.path / 'profile.json').read_text(encoding='utf-8'))
        self.config = self.profile.get('decision_analysis', {})
        self.score_path = self.profile.get('trials_path', 'decisionResult.product_trials') + '.scoring.pd.value'
        self.penalty = options.penalty if options.penalty is not None else number(self.config.get('penalty'))
        self.scale_min = number(self.config.get('scale_min'))
        self.scale_max = number(self.config.get('scale_max'))
        self.distance = options.distance
        if self.distance is None:
            default = (self.scale_max - self.scale_min) / 10 if self.scale_min is not None and self.scale_max is not None else Decimal('.1')
            self.distance = number(self.config.get('near_distance', default))
        self.mart = mart
        self.rows, self.applications = self.select()
        self.install_views()

    def evaluate(self, row):
        opt = self.options
        direction = model_direction(self.config, row['MODEL'], opt.direction)
        score, cutoff = number(row['SCORE']), number(row['CUTOFF'])
        effective, penalty = score, None
        factors = names(row)
        stop = row['STOP']
        mandatory = self.config.get('mandatory_factors')
        blocked = stop if mandatory is None else bool(set(factors).intersection(mandatory))
        unknown_stop = row.get('STOP_UNKNOWN', True) or bool(stop and mandatory is not None and not factors)
        decision, reason = 'UNKNOWN', 'INCOMPLETE'
        if opt.mode == 'hard' and blocked:
            decision, reason = 'REJECT', 'STOP_FACTOR'
        elif direction is None:
            reason = 'DIRECTION_UNSET'
            if opt.mode == 'penalty':
                effective = None
        elif opt.mode == 'penalty' and self.penalty is None:
            effective, reason = None, 'PENALTY_UNSET'
        elif opt.mode == 'penalty' and unknown_stop:
            effective = None
        elif opt.mode == 'penalty' and stop:
            count = row.get('STOP_COUNT')
            if count is None:
                effective = None
            elif score is not None:
                penalty = self.penalty * count
                effective = score + penalty if direction in ('le', 'lt') else score - penalty
                if self.scale_min is not None:
                    effective = max(self.scale_min, effective)
                if self.scale_max is not None:
                    effective = min(self.scale_max, effective)
        if reason == 'INCOMPLETE' and effective is not None and cutoff is not None and not unknown_stop:
            decision, reason = ('APPROVE', 'SCORE_PASS') if DIRECTIONS[direction](effective - cutoff) else ('REJECT', 'SCORE_CUTOFF')
        value = effective if opt.basis == 'scenario' else score
        delta = value - cutoff if value is not None and cutoff is not None else None
        reject_reason = REASONS[reason] if opt.basis == 'scenario' and decision == 'REJECT' else row['REJECT_REASON'] if opt.basis == 'historical' else None
        selected_decision = (decision if opt.basis == 'scenario' else row['DECISION']) or 'UNKNOWN'
        return {**row, 'FACTORS': factors, 'RAW_SCORE': row['SCORE'], 'HISTORICAL': row['DECISION'],
                'EXPECTED': decision, 'REASON': reason, 'ACTIVE_REASON': reject_reason,
                'ACTIVE_DECISION': selected_decision, 'ACTIVE_SCORE': value,
                'EFFECTIVE_SCORE': float(effective) if effective is not None else None,
                'PENALTY': float(penalty) if penalty is not None else None, 'DIRECTION': direction,
                'DELTA': delta, 'PASSES_SCORE': direction is not None and delta is not None and DIRECTIONS[direction](delta)}

    def side_matches(self, row):
        if row is None:
            return False
        o = self.options
        score, delta = row['ACTIVE_SCORE'], row['DELTA']
        if o.minimum is not None and (score is None or score < o.minimum):
            return False
        if o.maximum is not None and (score is None or score > o.maximum):
            return False
        state = 'yes' if row['STOP'] else 'unknown' if row.get('STOP_UNKNOWN', True) else 'no'
        if o.stop and state != o.stop:
            return False
        if o.factors:
            selected, actual = set(o.factors), set(row['FACTORS'])
            if not (selected <= actual if o.factor_match == 'all' else selected & actual):
                return False
        if o.reason.casefold() not in (row['ACTIVE_REASON'] or '').casefold():
            return False
        return not o.near or (delta is not None and abs(delta) <= self.distance and
                              (o.position == 'any' or o.position == ('below' if delta < 0 else 'above' if delta > 0 else 'equal')))

    def select(self):
        m, o = self.mart, self.options
        quality = m.query("SELECT APP_ID,SOURCE,bool_or(STATUS <> 'missing') present,bool_or(STATUS = 'parsed') parsed FROM quality GROUP BY ALL")
        presence = defaultdict(dict)
        for row in quality:
            presence[row['APP_ID']][row['SOURCE']] = row
        segments = {r['APP_ID']:r for r in m.segments()}
        if self.valid_pairs_only:
            presence = {app:sides for app,sides in presence.items() if app in segments}
        extras = not self.valid_pairs_only and (m.path / 'unpaired_segments.parquet').exists()
        if extras:
            segments.update({r['APP_ID']:r for r in m.query('SELECT * FROM unpaired_segments')})
        sql = 'SELECT * FROM products'
        if extras:
            sql += ' UNION ALL BY NAME SELECT * FROM unpaired_products'
        pairs = defaultdict(dict)
        factors, products = set(), set()
        for raw in m.query(sql):
            row = self.evaluate(raw)
            factors.update(row['FACTORS'])
            products.add(row['PRODUCT'])
            pairs[row['APP_ID']].setdefault(row['PRODUCT'], {})[row['SOURCE']] = row
        self.factors, self.products = sorted(factors), sorted(products)
        decisions = set()
        selected_pairs, applications = [], []
        for app, sides in presence.items():
            segment = segments.get(app, {})
            all_pairs = pairs[app]
            candidates = []
            for product, pair in all_pairs.items():
                if o.product and product != o.product:
                    continue
                sources = SIDES if o.side in ('either', 'both') else (o.side,)
                matched = [self.side_matches(pair.get(source)) for source in sources]
                if o.side_filter and not (all(matched) if o.side == 'both' else any(matched)):
                    continue
                for source in SIDES:
                    if source in pair:
                        pair[source]['NEAR_SCORE_PASS'] = bool(o.near and source in sources
                                                             and matched[sources.index(source)]
                                                             and pair[source]['PASSES_SCORE'])
                deltas = [abs(pair[source]['DELTA']) for source, match in zip(sources, matched)
                          if match and pair[source]['DELTA'] is not None]
                candidates.append({'APP_ID': app, 'PRODUCT': product, **pair,
                                   'DISTANCE': min(deltas, default=None)})
            if (o.product or o.side_filter) and not candidates:
                continue
            statuses = {}
            for source in SIDES:
                if not sides.get(source, {}).get('present'):
                    status = 'ABSENT'
                elif o.basis == 'historical':
                    status = segment.get('DECISION_' + source) or 'UNKNOWN'
                else:
                    verdicts = {p[source]['ACTIVE_DECISION'] for p in candidates if source in p}
                    status = 'APPROVE' if 'APPROVE' in verdicts else 'REJECT' if verdicts == {'REJECT'} else 'UNKNOWN'
                statuses[source] = status
            before, after = statuses.values()
            decisions.update(statuses.values())
            changed = before not in ('ABSENT', 'UNKNOWN') and after not in ('ABSENT', 'UNKNOWN') and before != after
            transition = {'changed': changed, 'unchanged': before == after and before not in ('ABSENT', 'UNKNOWN'),
                          'new_reject': changed and after == 'REJECT', 'left_reject': changed and before == 'REJECT'}
            if (o.before and before != o.before or o.after and after != o.after
                    or o.transition and not transition[o.transition]):
                continue
            applications.append({'APP_ID': app, 'DECISION_BEFORE': before, 'DECISION_AFTER': after,
                                 'DECISION_CHANGED': int(changed),
                                 'PARSED_BEFORE':bool(sides.get('BEFORE',{}).get('parsed')),
                                 'PARSED_AFTER':bool(sides.get('AFTER',{}).get('parsed'))})
            selected_pairs.extend(candidates)
        self.decisions = sorted(decisions | {'APPROVE', 'REJECT', 'VERIFICATION', 'UNKNOWN', 'ABSENT'} | ({o.before, o.after} - {''}))
        selected_pairs.sort(key=lambda p: (p['DISTANCE'] if o.near and p['DISTANCE'] is not None else Decimal('Infinity'),
                                           p['APP_ID'], p['PRODUCT']))
        return selected_pairs, applications

    def install_views(self):
        m, o = self.mart, self.options
        register_rows(m, 'analysis_apps', self.applications,
                      {'APP_ID':'VARCHAR', 'DECISION_BEFORE':'VARCHAR', 'DECISION_AFTER':'VARCHAR', 'DECISION_CHANGED':'BIGINT', 'PARSED_BEFORE':'BOOLEAN', 'PARSED_AFTER':'BOOLEAN'})
        fields = {'APP_ID':'VARCHAR', 'SOURCE':'VARCHAR', 'PRODUCT':'VARCHAR', 'MODEL':'VARCHAR', 'PARTNER':'VARCHAR',
                  'SCORE':'DOUBLE', 'CUTOFF':'DOUBLE', 'DECISION':'VARCHAR', 'STOP':'BOOLEAN', 'STOP_UNKNOWN':'BOOLEAN',
                  'STOP_COUNT':'BIGINT', 'STOP_NAMES':'VARCHAR', 'REJECT_REASON':'VARCHAR', 'BUCKET':'VARCHAR'}
        projected = []
        for pair in self.rows:
            for source in SIDES:
                if source not in pair:
                    continue
                row = pair[source]
                score = row['ACTIVE_SCORE']
                bucket = adjusted(str(score), 'number', self.score_path, self.profile) if score is not None else 'NULL'
                projected.append({**{k:row.get(k) for k in fields},
                                  'SCORE':float(score) if score is not None else None,
                                  'DECISION':row['ACTIVE_DECISION'], 'REJECT_REASON':row['ACTIVE_REASON'], 'BUCKET':bucket})
        register_rows(m, 'analysis_products', projected, fields)
        for name in ('comparison', 'segments', 'quality', 'products'):
            m.con.execute(f'ALTER VIEW {name} RENAME TO original_{name}')
        m.con.execute('''CREATE TEMP VIEW segments AS SELECT a.*, s.* EXCLUDE
            (APP_ID,DECISION_BEFORE,DECISION_AFTER,DECISION_CHANGED)
            FROM analysis_apps a LEFT JOIN original_segments s USING (APP_ID)''')
        m.con.execute('CREATE TEMP VIEW products AS SELECT * FROM analysis_products')
        m.con.execute('CREATE TEMP VIEW quality AS SELECT q.* FROM original_quality q SEMI JOIN analysis_apps USING (APP_ID)')
        # Keep raw comparison columns and replace only the two displayed bucket values.
        comparison = 'SELECT c.* FROM original_comparison c'
        if o.filtered:
            comparison += ' SEMI JOIN analysis_apps USING (APP_ID)'
        project_trials = o.basis == 'scenario' or o.side_filter or o.product
        if project_trials:
            trials = self.score_path.removesuffix('.scoring.pd.value')
            m.con.execute('CREATE TEMP TABLE analysis_paths AS SELECT ? AS score, ? AS trial_prefix', [self.score_path, trials + '.'])
            m.con.execute("""CREATE TEMP TABLE analysis_trial_keys AS
                SELECT n.APP_ID,n.SOURCE,json_extract_string(n.KEY_JSON,'$[0]') AS TRIAL_KEY,
                       p.APP_ID IS NOT NULL AS SELECTED,p.BUCKET,p.SCORE,p.PRODUCT
                FROM normalized n SEMI JOIN analysis_apps USING(APP_ID)
                LEFT JOIN analysis_products p ON p.APP_ID=n.APP_ID AND p.SOURCE=n.SOURCE AND p.PRODUCT=n.VALUE
                WHERE n.PATH=?""", [trials + '.product_id'])
            join = """ LEFT JOIN analysis_trial_keys {alias} ON {alias}.APP_ID=c.APP_ID
                       AND {alias}.SOURCE='{source}' AND {alias}.TRIAL_KEY=json_extract_string(c.KEY_JSON,'$[0]')"""
            projection = 'c.*'
            if o.basis == 'scenario':
                projection += """ REPLACE (
                    CASE WHEN c.PATH=a.score AND b.SELECTED AND c.PRESENT_BEFORE IS NOT NULL THEN b.BUCKET ELSE c.ADJUSTED_BEFORE END AS ADJUSTED_BEFORE,
                    CASE WHEN c.PATH=a.score AND t.SELECTED AND c.PRESENT_AFTER IS NOT NULL THEN t.BUCKET ELSE c.ADJUSTED_AFTER END AS ADJUSTED_AFTER),
                    CASE WHEN c.PATH=a.score AND b.SELECTED AND b.SCORE IS NULL THEN 'null' ELSE c.TYPE_BEFORE END AS BUCKET_TYPE_BEFORE,
                    CASE WHEN c.PATH=a.score AND t.SELECTED AND t.SCORE IS NULL THEN 'null' ELSE c.TYPE_AFTER END AS BUCKET_TYPE_AFTER"""
            comparison = f'SELECT {projection} FROM ({comparison}) c CROSS JOIN analysis_paths a'
            comparison += join.format(alias='b', source='BEFORE') + join.format(alias='t', source='AFTER')
            comparison += ' WHERE NOT starts_with(c.PATH,a.trial_prefix) OR coalesce(b.SELECTED,false) OR coalesce(t.SELECTED,false)'
        m.con.execute('CREATE TEMP VIEW comparison AS ' + comparison)
        m.bucket_types = o.basis == 'scenario'
        membership = 'membership_base m'
        bucket = 'm.VALUE_ADJUSTED'
        trial_join = ''
        if project_trials:
            membership += ' JOIN comparison c USING (APP_ID,PATH,KEY_JSON) CROSS JOIN analysis_paths ap'
            bucket = "CASE m.SOURCE WHEN 'BEFORE' THEN c.ADJUSTED_BEFORE ELSE c.ADJUSTED_AFTER END"
            trial_join = ''' AND (NOT starts_with(m.PATH, ap.trial_prefix)
                              OR NOT k.SELECTED OR k.PRODUCT=p.PRODUCT)'''
            membership += ''' LEFT JOIN analysis_trial_keys k ON k.APP_ID=m.APP_ID AND k.SOURCE=m.SOURCE
                              AND k.TRIAL_KEY=json_extract_string(m.KEY_JSON,'$[0]')
                              AND starts_with(m.PATH, ap.trial_prefix)'''
        m.con.execute(f'''CREATE OR REPLACE VIEW membership AS
            SELECT m.* REPLACE ({bucket} AS VALUE_ADJUSTED),
                   p.PRODUCT,p.PARTNER,s.ROUTING_METHOD,a.DECISION_CHANGED
            FROM {membership}
            LEFT JOIN (SELECT DISTINCT APP_ID,SOURCE,PRODUCT,PARTNER FROM products) p
            ON p.APP_ID=m.APP_ID AND p.SOURCE=m.SOURCE {trial_join}
            LEFT JOIN original_segments s ON s.APP_ID=m.APP_ID
            JOIN analysis_apps a ON a.APP_ID=m.APP_ID''')

    def summary(self):
        result = self.mart.query("""SELECT count(*) discovered,
            count(*) FILTER (WHERE DECISION_BEFORE<>'ABSENT') present_before,
            count(*) FILTER (WHERE DECISION_AFTER<>'ABSENT') present_after,
            coalesce(sum(DECISION_CHANGED),0) decision_changed,
            count(*) FILTER (WHERE DECISION_BEFORE='ABSENT' AND PARSED_AFTER) added,
            count(*) FILTER (WHERE DECISION_AFTER='ABSENT' AND PARSED_BEFORE) removed,
            count(*) FILTER (WHERE DECISION_BEFORE='UNKNOWN') unknown_before,
            count(*) FILTER (WHERE DECISION_AFTER='UNKNOWN') unknown_after
            FROM analysis_apps""")[0]
        result.update(self.mart.query("""SELECT count(*) valid_pairs FROM original_segments
            SEMI JOIN analysis_apps USING (APP_ID)""")[0])
        for source in SIDES:
            result['valid_' + source.lower()] = self.mart.query(
                'SELECT count(DISTINCT APP_ID) n FROM quality WHERE SOURCE=? AND STATUS=\'parsed\'', [source])[0]['n']
        result['excluded'] = result['discovered'] - result['valid_pairs']
        result['changed_apps'] = self.mart.query('SELECT count(DISTINCT APP_ID) n FROM comparison WHERE DIFF_FLAG=1')[0]['n']
        return result

    def metadata(self):
        return {'factors': self.factors, 'products': self.products, 'decisions': self.decisions,
                'distance': float(self.distance), 'penalty': float(self.penalty) if self.penalty is not None else None,
                'profile_penalty': self.config.get('penalty')}

    def decision_page(self, offset, limit, passes_only=False):
        reasons = defaultdict(set)
        by_app = {row['APP_ID']: row for row in self.applications}
        for pair in self.rows:
            app = pair['APP_ID']
            states = by_app[app]
            for source in SIDES:
                row = pair.get(source)
                if not row or row['ACTIVE_DECISION'] != 'REJECT':
                    continue
                labels = [row['ACTIVE_REASON'] or 'Причина не указана']
                if self.options.basis == 'historical' or row['REASON'] == 'STOP_FACTOR':
                    labels += ['Стоп-фактор: ' + name for name in row['FACTORS']]
                change = ('Новый отказ' if states['DECISION_AFTER'] == 'REJECT' and states['DECISION_BEFORE'] not in ('REJECT', 'UNKNOWN', 'ABSENT')
                          else 'Выход из отказа' if states['DECISION_BEFORE'] == 'REJECT' and states['DECISION_AFTER'] not in ('REJECT', 'UNKNOWN', 'ABSENT')
                          else None)
                for label in labels:
                    reasons[(label, source, 'Все отказы')].add(app)
                    if change and ((change == 'Новый отказ') == (source == 'AFTER')):
                        reasons[(label, source, change)].add(app)
        passes = [p for p in self.rows if any(p[s]['NEAR_SCORE_PASS'] for s in SIDES if s in p)]
        rows = passes if passes_only else self.rows
        return {'items': rows[offset:offset + limit], 'total': len(rows), 'applications': len(self.applications),
                'near_pass_total': len(passes),
                **self.metadata(), 'reasons': [{'label': k[0], 'source': k[1], 'group': k[2], 'ids': sorted(ids)}
                            for k, ids in sorted(reasons.items())]}
