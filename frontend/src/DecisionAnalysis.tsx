import React, {useEffect, useState} from 'react';

const sides = ['BEFORE', 'AFTER'] as const;
const sideLabel = {BEFORE: 'До', AFTER: 'После'};
const statusLabels: Record<string, string> = {
  APPROVE: 'Одобрено', REJECT: 'Отказ', VERIFICATION: 'На проверку',
  ABSENT: 'Нет ответа', UNKNOWN: 'Не рассчитано',
};
export const decisionLabel = (value: string) => statusLabels[value] || value || '—';
const reasons: Record<string, string> = {
  STOP_FACTOR: 'Стоп-фактор', SCORE_CUTOFF: 'Порог Score не пройден',
  SCORE_PASS: 'Порог Score пройден', INCOMPLETE: 'Недостаточно данных',
  PENALTY_UNSET: 'Штраф не задан',
};
const directions: Record<string, string> = {le: '≤', lt: '<', ge: '≥', gt: '>'};
const show = (value: any) => value ?? '—';
const signed = (value: number | null | undefined) => value == null ? '—' : value > 0 ? '+' + value : String(value);

export const defaultAnalysis = {
  basis: 'historical', mode: 'hard', direction: '', penalty: '', side: 'either',
  minimum: '', maximum: '', near: false, distance: '', position: 'any',
  stop: '', factors: [] as string[], factor_match: 'any', reason: '',
  before: '', after: '', transition: '', product: '',
};
export type Analysis = typeof defaultAnalysis;

export function analysisPayload(settings: Analysis) {
  return Object.fromEntries(Object.entries(settings).map(([key, value]) => [
    key, ['minimum', 'maximum', 'distance', 'penalty'].includes(key) && value === '' ? null : value,
  ]));
}

export function AnalysisControls({applied, apply, meta, total}: {
  applied: Analysis; apply: (a: Analysis) => void; meta: any; total?: number;
}) {
  const [draft, setDraft] = useState(applied);
  useEffect(() => setDraft(applied), [applied]);
  const field = <K extends keyof Analysis>(key: K, value: Analysis[K]) => setDraft(prev => ({...prev, [key]: value}));
  const select = (key: keyof Analysis, label: string, options: [string, string][]) =>
    <label>{label}<select aria-label={label} value={String(draft[key])} onChange={e => key==='mode' ? setDraft({...draft,mode:e.target.value,basis:'scenario'}) : field(key, e.target.value)}>
      {options.map(([id, text]) => <option value={id} key={id}>{text}</option>)}
    </select></label>;
  const decimal = (key: 'minimum' | 'maximum' | 'distance' | 'penalty', label: string, placeholder?: string) =>
    <label>{label}<input type="number" step="any" min={['distance', 'penalty'].includes(key) ? 0 : undefined}
      value={draft[key]} placeholder={placeholder} onChange={e => field(key, e.target.value)}/></label>;
  const valid = !draft.minimum || !draft.maximum || Number(draft.minimum) <= Number(draft.maximum);
  const decisions = (meta?.decisions || Object.keys(statusLabels)).map((id: string) => [id, decisionLabel(id)]);
  const active = [
    applied.product && 'Продукт ' + applied.product,
    applied.minimum && 'Score от ' + applied.minimum,
    applied.maximum && 'Score до ' + applied.maximum,
    applied.near && 'Близко к порогу',
    applied.stop && 'Стоп-фактор · ' + ({yes:'сработал', no:'не сработал', unknown:'неизвестно'}[applied.stop]),
    applied.factors.length > 0 && applied.factors.join(', ') + (applied.factor_match === 'all' ? ' · все' : ' · любой'),
    applied.reason && 'Причина: ' + applied.reason,
    applied.before && 'До: ' + decisionLabel(applied.before),
    applied.after && 'После: ' + decisionLabel(applied.after),
    applied.transition && ({changed:'Смена решения', unchanged:'Решение сохранилось', new_reject:'Новый отказ', left_reject:'Выход из отказа'}[applied.transition]),
  ].filter(Boolean);
  return <details className="panel common-analysis">
    <summary><b>Общие фильтры и сценарий</b> · {applied.basis === 'historical' ? 'Исторические решения' :
      applied.mode === 'hard' ? 'Сценарий · отсев' : 'Сценарий · штраф'} · <span aria-live="polite">{total ?? '…'} заявок</span>
      {!!active.length && <span className="filter-pills">{active.map((label, i) => <span className="tag" key={i}>{label}</span>)}</span>}
    </summary>
    <form className="form-pad" onSubmit={e => {e.preventDefault(); if (valid) apply(draft);}}>
      <div className="analysis-filters">
        {select('basis', 'Источник решений', [['historical', 'Исторические решения'], ['scenario', 'Расчёт выбранного сценария']])}
        {select('mode', 'Моделирование стоп-фактора', [['hard', 'Стоп-фактор сразу даёт отказ'], ['penalty', 'Стоп-фактор ухудшает Score']])}
        {select('direction', 'Правило одобрения', [['', 'Из профиля'], ...Object.entries(directions).map(([id, op]): [string,string] => [id, 'Score ' + op + ' порог'])])}
        {draft.mode === 'penalty' && decimal('penalty', 'Штраф за каждый стоп-фактор', meta?.profile_penalty == null ? 'Задайте штраф' : String(meta.profile_penalty))}
      </div>
      {draft.mode === 'penalty' && <p className="muted">Штрафы складываются в единицах Score. При правиле ≤ или &lt; штраф увеличивает балл, при ≥ или &gt; уменьшает.</p>}
      {applied.mode === 'penalty' && meta?.penalty == null && <p role="status">Штраф не задан. Укажите его здесь или в профиле для расчёта сценария.</p>}
      <div className="analysis-filters">
        {select('side', 'Сторона фильтров', [['either','Хотя бы одна сторона'], ['both','Обе стороны'], ['BEFORE','До'], ['AFTER','После']])}
        {select('product', 'Продукт', [['','Все'], ...(meta?.products || []).map((id: string) => [id,id])])}
        {decimal('minimum', 'Score от')}{decimal('maximum', 'Score до')}
        {select('stop', 'Стоп-фактор', [['','Любое состояние'],['yes','Сработал'],['no','Не сработал'],['unknown','Неизвестно']])}
        <label>Причина отказа<input value={draft.reason} onChange={e => field('reason', e.target.value)}/></label>
        {select('before', 'Решение до', [['','Все'], ...decisions])}
        {select('after', 'Решение после', [['','Все'], ...decisions])}
        {select('transition', 'Направление смены решения', [['','Все'],['changed','Смена решения'],['unchanged','Решение сохранилось'],['new_reject','Новый отказ'],['left_reject','Выход из отказа']])}
      </div>
      <details className="advanced-options">
        <summary>Выбрать стоп-факторы · {draft.factors.length}</summary>
        {select('factor_match', 'Совпадение стоп-факторов', [['any','Хотя бы один из выбранных'],['all','Все выбранные']])}
        <div className="factor-options">{(meta?.factors || []).map((name: string) =>
          <label className="inline" key={name}><input type="checkbox" checked={draft.factors.includes(name)}
            onChange={e => field('factors', e.target.checked ? [...draft.factors, name] : draft.factors.filter(v => v !== name))}/>{name}</label>)}
          {!meta?.factors?.length && <p>В прогоне нет именованных сработавших стоп-факторов.</p>}
        </div>
      </details>
      <label className="inline"><input type="checkbox" checked={draft.near} onChange={e => field('near', e.target.checked)}/>Близко к порогу</label>
      {draft.near && <div className="analysis-filters">{decimal('distance','Ширина зоны порога',String(meta?.distance ?? '0.1'))}
        {select('position','Положение относительно порога',[['any','Любое'],['below','Ниже'],['equal','Ровно на пороге'],['above','Выше']])}</div>}
      {!valid && <p role="alert">Нижняя граница Score не должна превышать верхнюю.</p>}
      <div className="actions"><button className="primary" disabled={!valid}>Применить выборку</button>
        <button type="button" onClick={() => apply({...defaultAnalysis, basis: applied.basis, mode: applied.mode, direction: applied.direction, penalty: applied.penalty})}>Сбросить фильтры</button>
      </div>
    </form>
  </details>;
}

export function DecisionSummary({transitions, selection, open}: any) {
  if (!selection) return null;
  const known = (state: string) => state !== 'ABSENT' && state !== 'UNKNOWN';
  const counts = sides.map(side => {
    const values = new Map<string, number>();
    for (const row of transitions) {
      const state = row['DECISION_' + side];
      values.set(state, (values.get(state) || 0) + row.CNT);
    }
    const denominator = [...values].reduce((n, [state, count]) => known(state) ? n + count : n, 0);
    return {side, values, denominator};
  });
  const rate = ({values, denominator}: typeof counts[number]) => denominator ? 100 * (values.get('REJECT') || 0) / denominator : null;
  const before = rate(counts[0]), after = rate(counts[1]);
  const statuses = [...new Set(['APPROVE', 'REJECT', ...counts.flatMap(c => [...c.values.keys()].filter(known))])];
  const matrix = transitions.filter((r: any) => known(r.DECISION_BEFORE) && known(r.DECISION_AFTER));
  const paired = matrix.reduce((n: number, r: any) => n + r.CNT, 0);
  return <section className="panel"><div className="panel-head"><h2>Итоги по решениям</h2></div><div className="form-pad">
    <p>База долей по стороне — заявки с рассчитанным решением. Изменение доли отказов {before == null || after == null ? '—' : (after - before).toFixed(2) + ' п. п.'}</p>
    <div className="table-wrap"><table><thead><tr><th>Решение</th>{sides.map(s => <th key={s}>{sideLabel[s]}</th>)}</tr></thead>
      <tbody>{[...statuses,'ABSENT','UNKNOWN'].map(state => <tr key={state}><td>{decisionLabel(state)}</td>
        {counts.map(({side, values, denominator}) => {
          const n = values.get(state) || 0;
          return <td key={side}><button className="text" disabled={!n} onClick={() => open(decisionLabel(state), {['DECISION_' + side]: state})}>
            {n}{known(state) && denominator ? ' / ' + denominator + ' · ' + (n * 100 / denominator).toFixed(2) + '%' : ''}
          </button></td>;
        })}</tr>)}</tbody>
    </table></div>
    <p>Добавлено {selection.added} · удалено {selection.removed} · не рассчитано до / после {selection.unknown_before} / {selection.unknown_after}</p>
    <div className="table-wrap"><table><caption>Матрица переходов · {paired} заявок с решением на обеих сторонах</caption>
      <thead><tr><th>До / после</th>{statuses.map(state => <th key={state}>{decisionLabel(state)}</th>)}</tr></thead>
      <tbody>{statuses.map(b => <tr key={b}><th>{decisionLabel(b)}</th>{statuses.map(a => {
        const n = matrix.find((r: any) => r.DECISION_BEFORE === b && r.DECISION_AFTER === a)?.CNT || 0;
        return <td key={a}><button disabled={!n} className="text" onClick={() => open(decisionLabel(b) + ' → ' + decisionLabel(a), {DECISION_BEFORE:b, DECISION_AFTER:a})}>
          {n}{paired ? ' · ' + (n * 100 / paired).toFixed(2) + '%' : ''}
        </button></td>;
      })}</tr>)}</tbody>
    </table></div>
  </div></section>;
}

export function DecisionAnalysis({data, page, setPage, near, passesOnly, setPassesOnly, open, openGroup}: any) {
  if (!data) return <p role="status">Загружаем решения…</p>;
  const pageSize = 25;
  const cells = (r: any) => [r?.RAW_SCORE, r?.CUTOFF, signed(r?.DELTA),
    r?.FACTORS?.join(', ') || (r?.STOP ? 'Сработал' : r?.STOP_UNKNOWN ? 'Неизвестно' : '—'),
    decisionLabel(r?.HISTORICAL), decisionLabel(r?.EXPECTED), r?.EFFECTIVE_SCORE, r?.PENALTY, r ? reasons[r.REASON] : '—'];
  const headings = ['Исходный Score', 'Порог', 'Score − порог', 'Стоп-факторы', 'Историческое решение', 'Расчётное решение', 'Score сценария', 'Штраф', 'Основание сценария'];
  const table = (items: any[]) => <div className="table-wrap"><table><thead>
    <tr><th rowSpan={2}>Заявка / продукт</th>{sides.map(s => <th colSpan={headings.length} key={s}>{sideLabel[s]}</th>)}</tr>
    <tr>{sides.flatMap(s => headings.map(h => <th key={s+h}>{h}</th>))}</tr></thead>
    <tbody>{items.map((p: any) => <tr key={JSON.stringify([p.APP_ID,p.PRODUCT])}>
      <td><button onClick={() => open(p)}>{p.APP_ID} / {p.PRODUCT}</button></td>
      {sides.flatMap(s => cells(p[s]).map((value, i) => <td key={s+i}>{show(value)}</td>))}
    </tr>)}</tbody></table></div>;
  return <section className="panel"><div className="panel-head"><h2>Решения по продуктам</h2></div><div className="form-pad">
    <p>{data.applications} заявок в общей выборке · {data.total} пар «заявка — продукт» в списке</p>
    <details><summary>Причины отказов и их изменение</summary>
      <p>База — отказы по продуктам выбранных заявок. Одна заявка может входить в несколько групп причин.</p>
      <table><thead><tr><th>Группа</th><th>Сторона</th><th>Причина</th><th>Заявок</th></tr></thead>
        <tbody>{data.reasons.map((r: any) => <tr key={JSON.stringify([r.group,r.source,r.label])}>
          <td>{r.group}</td><td>{sideLabel[r.source as keyof typeof sideLabel]}</td><td>{r.label}</td>
          <td><button onClick={() => openGroup(r.label,r.ids)}>{r.ids.length}</button></td>
        </tr>)}</tbody>
      </table>
    </details>
    {near && <label className="inline"><input type="checkbox" checked={passesOnly} onChange={e=>setPassesOnly(e.target.checked)}/>
      Только проходящие по Score в зоне порога · {data.near_pass_total} пар
    </label>}
    {table(data.items)}
    {!data.items.length && <p>Нет продуктовых строк по выбранным фильтрам.</p>}
    <div className="pagination">
      <button disabled={!page} onClick={() => setPage(page - 1)}>Назад</button>
      <span>{data.total ? page * pageSize + 1 : 0}–{Math.min((page + 1) * pageSize, data.total)} из {data.total} пар</span>
      <button disabled={(page + 1) * pageSize >= data.total} onClick={() => setPage(page + 1)}>Далее</button>
    </div>
  </div></section>;
}

export function ApplicationScorecards({pairs, fields}: any) {
  if (!pairs?.length) return null;
  const metrics: [string, (r: any) => any][] = [
    ['Модель', r => r.MODEL], ['Исходный Score', r => r.RAW_SCORE],
    ['Штраф', r => r.PENALTY], ['Итоговый Score сценария', r => r.EFFECTIVE_SCORE],
    ['Порог', r => r.CUTOFF], ['Score − порог', r => signed(r.DELTA)],
    ['Правило одобрения', r => 'Score ' + directions[r.DIRECTION] + ' порог'],
    ['Стоп-факторы', r => r.FACTORS.join(', ') || (r.STOP ? 'Сработал' : r.STOP_UNKNOWN ? 'Неизвестно' : 'Нет')],
    ['Историческое решение', r => decisionLabel(r.HISTORICAL)], ['Расчётное решение', r => decisionLabel(r.EXPECTED)],
    ['Основание сценария', r => reasons[r.REASON]],
  ];
  return <section><h3>Скоркарта заявки</h3>
    {pairs.map((p: any) => <div className="table-wrap" key={p.PRODUCT}><table><caption>Продукт {p.PRODUCT}</caption>
      <thead><tr><th>Показатель</th><th>До</th><th>После</th><th>Разница</th></tr></thead>
      <tbody>{metrics.map(([label, read]) => {
        const b = p.BEFORE && read(p.BEFORE), a = p.AFTER && read(p.AFTER);
        return <tr key={label}><th>{label}</th><td>{show(b)}</td><td>{show(a)}</td><td>{typeof a === 'number' && typeof b === 'number' ? signed(Number((a - b).toPrecision(12))) : '—'}</td></tr>;
      })}</tbody>
    </table></div>)}
    {!!fields?.length && <details><summary>Атрибуты скоринга и бакеты</summary><div className="table-wrap"><table>
      <thead><tr><th>Атрибут</th><th>До · значение / бакет</th><th>После · значение / бакет</th></tr></thead>
      <tbody>{fields.map((r: any) => <tr key={r.PATH+r.KEY_JSON}><th>{r.PATH} {r.ARRAY_KEY}</th>
        {sides.map(s => <td key={s}>{show(r['VALUE_'+s])} / {show(r['ADJUSTED_'+s])}</td>)}
      </tr>)}</tbody>
    </table></div></details>}
  </section>;
}
