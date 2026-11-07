import React, {useEffect, useState} from 'react';
import {
  Activity, ArrowDownRight, ArrowLeftRight, ArrowRight, ArrowUpRight, CheckCircle2, ChevronRight,
  CircleHelp, Database, Download, FileText, LayoutDashboard, Layers3, LogOut, Play, Search,
  ShieldCheck, SlidersHorizontal, UploadCloud, X,
} from 'lucide-react';
import {DemoAccessNotice} from './OverviewDetails';
import './simple.css';

export type InterfaceMode = 'simple' | 'advanced';
export const simpleTabs = ['overview', 'applications', 'quality', 'data'] as const;
export type SimpleTab = typeof simpleTabs[number];
export const isSimpleTab = (tab: string | null): tab is SimpleTab => simpleTabs.includes(tab as SimpleTab);

type Source = 'BEFORE' | 'AFTER';
type Filters = Record<string, string | string[] | number>;
type Group = 'changed' | 'refused' | 'approved' | 'all';
type Transition = {DECISION_BEFORE: string; DECISION_AFTER: string; CNT: number};
type Selection = {
  discovered: number;
  valid_pairs: number;
  decision_changed: number;
  excluded: number;
  added: number;
  removed: number;
  unknown_before: number;
  unknown_after: number;
};
type Overview = {selection: Selection; transitions: Transition[]};
type Item = {id: string; name: string};
type Run = {
  id: string;
  created: number;
  status: string;
  progress?: number;
  error?: string;
  dataset_name?: string;
  snapshot?: {dataset_name?: string};
};
type Quality = {SOURCE: Source; STATUS: string; CNT: number};
type Application = {APP_ID: string; DECISION_BEFORE: string; DECISION_AFTER: string};
type Drill = (title: string, kind: string, filters?: Filters, extra?: {excluded?: boolean; paired?: boolean}) => void;

const sides = ['BEFORE', 'AFTER'] as const;
const titles: Record<SimpleTab, [string, string]> = {
  overview: ['Обзор решений', 'Сравните исходную и новую версии и откройте заявки, где изменился результат.'],
  applications: ['Заявки', 'Найдите заявку по номеру или посмотрите, у кого изменилось решение.'],
  quality: ['Качество данных', 'Проверьте, по каким заявкам удалось сопоставить ответы и где возникли проблемы.'],
  data: ['Данные для сравнения', 'Выберите набор, загрузите две версии ответов и запустите сравнение.'],
};
const groups: Record<Group, string> = {
  changed: 'Решение изменилось', refused: 'Новые отказы', approved: 'Новые одобрения', all: 'Все заявки',
};
const decisions: Record<string, string> = {
  APPROVE: 'Одобрено', REJECT: 'Отказано', VERIFICATION: 'На проверку',
  ABSENT: 'Нет ответа', UNKNOWN: 'Решение неизвестно',
};
export const simpleDecision = (value?: string) => value ? decisions[value] || value : '—';
const decisionColor = (value?: string) => value === 'APPROVE' ? 'positive' : value === 'REJECT' ? 'negative' : 'neutral';
const number = new Intl.NumberFormat('ru-RU');
const decimal = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1});
const fmt = (n: number) => number.format(n);
const plural = new Intl.PluralRules('ru-RU');
const counted = (n: number, forms: [string,string,string]) => {
  const category = plural.select(n);
  return fmt(n) + ' ' + forms[category==='one' ? 0 : category==='few' ? 1 : 2];
};
const applications = (n: number) => counted(n,['заявка','заявки','заявок']);
const filesInDataset = (n: number) => counted(n,['файл','файла','файлов'])+' в наборе';
const ratio = (n: number, base: number) => base ? 100 * n / base : 0;
const percent = (n: number, base: number) => base ? decimal.format(ratio(n, base)) + '%' : '—';
const known = (value: string) => Boolean(value) && value !== 'ABSENT' && value !== 'UNKNOWN';
const paired = (row: Transition) => known(row.DECISION_BEFORE) && known(row.DECISION_AFTER);
const sum = (rows: Transition[]) => rows.reduce((total, row) => total + row.CNT, 0);
const date = (seconds: number) => new Intl.DateTimeFormat('ru-RU', {dateStyle: 'medium', timeStyle: 'short'}).format(seconds * 1000);
const qualityLabels: Record<string, string> = {
  checksum_error: 'Контрольная сумма не совпала', missing: 'Ответ отсутствует', parse_error: 'Файл не прочитан', schema_error: 'Ошибка в данных',
  id_conflict: 'Номер заявки не совпал', duplicate: 'Повторный ответ',
};
const sideLabel: Record<Source, string> = {BEFORE: 'До изменений', AFTER: 'После изменений'};

export function ModeSwitch({mode, onChange}: {mode: InterfaceMode; onChange: (mode: InterfaceMode) => void}) {
  return <div className="mode-switch" role="group" aria-label="Режим интерфейса">
    <button type="button" aria-pressed={mode === 'simple'} onClick={() => onChange('simple')}>
      <LayoutDashboard size={15} aria-hidden="true"/>Простой
    </button>
    <button type="button" aria-pressed={mode === 'advanced'} onClick={() => onChange('advanced')}>
      <SlidersHorizontal size={15} aria-hidden="true"/>Расширенный
    </button>
  </div>;
}

function DecisionBadge({value}: {value?: string}) {
  return <span className={'sw-decision sw-' + decisionColor(value)}>{simpleDecision(value)}</span>;
}

function groupFilters(group: Group, rows: Transition[]): Filters {
  if (group === 'all') return {};
  if (group === 'changed') return {DECISION_CHANGED: 1};
  const decision = group === 'refused' ? 'REJECT' : 'APPROVE';
  const before = [...new Set(rows.filter(row => paired(row) && row.DECISION_AFTER === decision
    && row.DECISION_BEFORE !== decision).map(row => row.DECISION_BEFORE))];
  return {DECISION_BEFORE: before, DECISION_AFTER: decision};
}

function comparison(rows: Transition[]) {
  const cohort = rows.filter(paired);
  const outcomes = (key: 'DECISION_BEFORE' | 'DECISION_AFTER') => {
    const approve = sum(cohort.filter(row => row[key] === 'APPROVE'));
    const reject = sum(cohort.filter(row => row[key] === 'REJECT'));
    return {approve, reject, other: sum(cohort) - approve - reject};
  };
  const changes = cohort.filter(row => row.DECISION_BEFORE !== row.DECISION_AFTER);
  return {
    cohort,
    base: sum(cohort), before: outcomes('DECISION_BEFORE'), after: outcomes('DECISION_AFTER'), changes,
    refused: sum(changes.filter(row => row.DECISION_AFTER === 'REJECT')),
    approved: sum(changes.filter(row => row.DECISION_AFTER === 'APPROVE')),
  };
}

function Delta({change}: {change: number}) {
  const rounded = Math.round(Math.abs(change) * 10) / 10;
  if (!rounded) return <span className="sw-delta sw-neutral">0 п. п.</span>;
  const Icon = change > 0 ? ArrowUpRight : ArrowDownRight;
  return <span className="sw-delta sw-neutral">
    <Icon size={14} aria-hidden="true"/>{change > 0 ? '+' : '−'}{decimal.format(rounded)} п. п.
  </span>;
}

function cohortFilters(cohort: Transition[], side: Source, decision: string | string[]): Filters {
  return {
    DECISION_BEFORE: [...new Set(cohort.map(row => row.DECISION_BEFORE))],
    DECISION_AFTER: [...new Set(cohort.map(row => row.DECISION_AFTER))],
    ['DECISION_' + side]: decision,
  };
}

function DecisionChart({data, open}: {data: ReturnType<typeof comparison>; open: Drill}) {
  const {base, before, after, cohort} = data;
  const metrics = ([
    {key: 'approve', label: 'Одобрено', tone: 'positive', decision: 'APPROVE'},
    {key: 'reject', label: 'Отказано', tone: 'negative', decision: 'REJECT'},
    {key: 'other', label: 'Другие решения', tone: 'neutral'},
  ] as const).filter(metric=>metric.key!=='other'||before.other>0||after.other>0);
  return <section className="sw-card sw-decision-chart" aria-labelledby="sw-decisions-title">
    <div className="sw-card-title">
      <div><h2 id="sw-decisions-title">Как изменились решения</h2><p>Одни и те же заявки с известным решением в обеих версиях.</p></div>
      <span className="sw-chip">{applications(base)} в базе</span>
    </div>
    {base ? <div className="sw-chart">
      {sides.map((side, i) => {
        const outcome = i === 0 ? before : after;
        return <div className="sw-chart-line" key={side}>
          <div className="sw-chart-label"><b>{sideLabel[side]}</b><span>{applications(base)}</span></div>
          <div className="sw-chart-track" aria-hidden="true">
            {metrics.filter(metric => outcome[metric.key]).map(metric =>
              <span key={metric.key} className={'sw-bar-' + metric.tone} style={{width: ratio(outcome[metric.key], base) + '%'}}/>)}
          </div>
          <div className="sw-chart-values">
            {metrics.map(metric => {
              const decisionSet = [...new Set(cohort.map(row => row['DECISION_' + side]))];
              const other = decisionSet.filter(value => value !== 'APPROVE' && value !== 'REJECT');
              const target = 'decision' in metric ? metric.decision : other;
              return <button key={metric.key} disabled={!outcome[metric.key]} className="sw-legend"
                onClick={() => open(metric.label + ' · ' + sideLabel[side].toLowerCase(), 'segments', cohortFilters(cohort,side,target))}>
                <span className={'sw-dot sw-bar-' + metric.tone}/><span>{metric.label}</span>
                <strong>{percent(outcome[metric.key], base)}</strong><span className="sw-legend-count">({fmt(outcome[metric.key])})</span>
              </button>;
            })}
          </div>
        </div>;
      })}
    </div> : <p className="sw-inline-empty">В этой выборке нет заявок с известным решением в обеих версиях.</p>}
    <details className="sw-explain">
      <summary><CircleHelp size={15} aria-hidden="true"/>Как читать проценты</summary>
      <p>Доля показывает, какая часть этих заявок получила указанное решение. Разница между долями измеряется в процентных пунктах. Например, рост с 20% до 23% составляет 3 п. п.</p>
    </details>
  </section>;
}

function Dashboard({report, open, go, advanced}: {
  report: Overview;
  open: Drill;
  go: (tab: SimpleTab, group?: Group) => void;
  advanced: (tab: string) => void;
}) {
  const {selection, transitions} = report;
  const result = comparison(transitions);
  const changed = sum(result.changes);
  const rates = [
    {label:'Доля одобрений', key:'approve', Icon:CheckCircle2, decision:'APPROVE'},
    {label:'Доля отказов', key:'reject', Icon:ArrowDownRight, decision:'REJECT'},
  ] as const;
  return <>
    <section className="sw-metrics" aria-label="Ключевые показатели">
      {rates.map(({label,key,Icon,decision}) => {
        const change = ratio(result.after[key], result.base) - ratio(result.before[key], result.base);
        return <button className="sw-metric sw-metric-link" key={key} disabled={!result.after[key]}
          onClick={() => open(simpleDecision(decision)+' · после изменений','segments',cohortFilters(result.cohort,'AFTER',decision))}>
          <div className="sw-metric-label"><Icon size={17} aria-hidden="true"/>{label}<ArrowUpRight size={15} className="sw-metric-arrow" aria-hidden="true"/></div>
          <div className="sw-metric-number">{percent(result.after[key], result.base)}{result.base > 0 && <Delta change={change}/>}</div>
          <p>До изменений <b>{percent(result.before[key], result.base)}</b></p>
        </button>;
      })}
      <button className="sw-metric sw-metric-link" disabled={!changed} onClick={() => open(groups.changed, 'segments', {DECISION_CHANGED: 1})}>
        <div className="sw-metric-label"><ArrowLeftRight size={17} aria-hidden="true"/>Сменили решение<ArrowUpRight size={15} className="sw-metric-arrow" aria-hidden="true"/></div>
        <div className="sw-metric-number">{fmt(changed)}</div>
        <p>{result.base ? percent(changed, result.base) + ' заявок с решением в обеих версиях' : 'Нет сопоставимых решений'}</p>
      </button>
      <button className="sw-metric sw-metric-link" disabled={!selection.valid_pairs} onClick={() => open('Сопоставленные заявки','segments',{}, {paired:true})}>
        <div className="sw-metric-label"><Layers3 size={17} aria-hidden="true"/>Сопоставлено<ArrowUpRight size={15} className="sw-metric-arrow" aria-hidden="true"/></div>
        <div className="sw-metric-number">{fmt(selection.valid_pairs)}</div>
        <p>Из {fmt(selection.discovered)} найденных заявок</p>
      </button>
    </section>
    <div className="sw-dashboard-grid">
      <DecisionChart data={result} open={open}/>
      <section className="sw-card sw-review" aria-labelledby="sw-review-title">
        <div className="sw-card-title"><div><h2 id="sw-review-title">Кого затронули изменения</h2><p>От заявки можно перейти к оценкам и причинам отказа.</p></div></div>
        <div className="sw-review-list">
          {([
            ['refused', result.refused, ArrowDownRight, 'negative', 'Раньше решение было другим'],
            ['approved', result.approved, ArrowUpRight, 'positive', 'Раньше одобрения не было'],
          ] as const).map(([id,count,Icon,tone,hint]) => <button key={id} className="sw-review-row" disabled={!count} onClick={() => go('applications', id)}>
            <span className={'sw-review-icon sw-' + tone}><Icon size={19} aria-hidden="true"/></span>
            <span><b>{groups[id]}</b><small>{hint}</small></span>
            <strong>{fmt(count)}</strong><ChevronRight size={16} aria-hidden="true"/>
          </button>)}
          <button className="sw-review-row" onClick={() => go('quality')}>
            <span className={'sw-review-icon ' + (selection.excluded ? 'sw-warning' : 'sw-positive')}><ShieldCheck size={19} aria-hidden="true"/></span>
            <span><b>Качество данных</b><small>{selection.excluded ? 'Заявок вне сравнения' : selection.discovered ? 'Для всех заявок найдена пара' : 'В выборке нет заявок'}</small></span>
            <strong className={selection.excluded?'':'sw-quality-ok'}>{selection.excluded ? fmt(selection.excluded) : selection.discovered ? 'В норме' : 'Нет данных'}</strong><ChevronRight size={16} aria-hidden="true"/>
          </button>
        </div>
      </section>
    </div>
    <section className="sw-card sw-transitions" aria-labelledby="sw-transitions-title">
      <div className="sw-card-title"><div><h2 id="sw-transitions-title">Какие решения поменялись</h2><p>Нажмите на строку, чтобы увидеть заявки.</p></div>
        <button className="sw-link" onClick={() => go('applications','changed')}>К списку заявок<ArrowRight size={16} aria-hidden="true"/></button>
      </div>
      {result.changes.length ? <div className="sw-transition-list">
        {[...result.changes].sort((a,b) => b.CNT-a.CNT).map(row => <button className="sw-transition-row" key={JSON.stringify([row.DECISION_BEFORE,row.DECISION_AFTER])}
          onClick={() => open(simpleDecision(row.DECISION_BEFORE) + ' → ' + simpleDecision(row.DECISION_AFTER), 'segments', {
            DECISION_BEFORE: row.DECISION_BEFORE, DECISION_AFTER: row.DECISION_AFTER,
          })}>
          <DecisionBadge value={row.DECISION_BEFORE}/><ArrowRight size={16} aria-hidden="true"/><DecisionBadge value={row.DECISION_AFTER}/>
          <span className="sw-transition-count"><b>{applications(row.CNT)}</b></span>
          <ChevronRight size={16} aria-hidden="true"/>
        </button>)}
      </div> : <p className="sw-inline-empty">В сравнимой части выборки смены решений не найдено.</p>}
      <div className="sw-comparison-note">
        <span>Только в новой версии <b>{fmt(selection.added)}</b></span>
        <span>Только в исходной <b>{fmt(selection.removed)}</b></span>
        <span>Неизвестное решение до / после <b>{fmt(selection.unknown_before)} / {fmt(selection.unknown_after)}</b></span>
      </div>
    </section>
    <button className="sw-link sw-secondary-link" onClick={() => advanced('score')}>
      Посмотреть оценки по продуктам и смоделировать правила <ArrowUpRight size={16} aria-hidden="true"/>
    </button>
  </>;
}

function Applications({runId, rows, group, changeGroup, selectionKey, load, openApp}: {
  runId: string; rows: Transition[]; group: Group; selectionKey: string;
  changeGroup: (group: Group) => void;
  load: (filters: Filters, offset: number, limit: number) => Promise<{items: Application[]; total: number}>;
  openApp: (appid: string) => void;
}) {
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{key:string; items?:Application[]; total?:number; error?:string}>();
  const pageSize = 15;
  const filters = {...groupFilters(group, rows), ...(query ? {APP_ID: query} : {})};
  const key = JSON.stringify([runId, selectionKey, filters, offset]);
  useEffect(() => {
    let active = true;
    load(filters, offset, pageSize).then(result => {if (active) setPage({key, ...result});})
      .catch(error => {if (active) setPage({key, error:error.message});});
    return () => {active = false;};
  }, [key]);
  const current = page?.key === key ? page : undefined;
  const updateQuery = (value: string) => {setQuery(value);setOffset(0);};
  return <section className="sw-card sw-applications" aria-label="Список заявок">
    <div className="sw-app-tools">
      <div className="sw-group-switch" role="group" aria-label="Какие заявки показать">
        {(Object.keys(groups) as Group[]).map(id => <button key={id} aria-pressed={group === id}
          onClick={() => {changeGroup(id);setSearch('');updateQuery('');}}>{groups[id]}</button>)}
      </div>
      <form className="sw-search" onSubmit={event => {event.preventDefault();const term=search.trim();if(term)changeGroup('all');updateQuery(term);}}>
        <label><Search size={17} aria-hidden="true"/><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Номер заявки" aria-label="Номер заявки"/></label>
        <button type="submit">Найти</button>
      </form>
    </div>
    {query && <button className="sw-chip sw-search-chip" onClick={() => {setSearch('');updateQuery('');}}>Номер {query}<X size={14} aria-label="Очистить поиск"/></button>}
    {current?.error ? <p role="alert" className="sw-inline-empty">{current.error}</p> :
      !current ? <p role="status" className="sw-inline-empty">Загружаем заявки…</p> :
      !current.items?.length ? <p className="sw-inline-empty">В этой группе нет подходящих заявок.</p> :
      <>
        <div className="sw-table">
          <table>
            <thead><tr><th scope="col">Номер заявки</th><th scope="col">До изменений</th><th scope="col">После изменений</th></tr></thead>
            <tbody>{current.items.map(item => <tr key={item.APP_ID}>
              <th scope="row"><button className="sw-app-id" onClick={() => openApp(item.APP_ID)}>{item.APP_ID}<ChevronRight size={14} aria-hidden="true"/></button></th>
              <td><DecisionBadge value={item.DECISION_BEFORE}/></td><td><DecisionBadge value={item.DECISION_AFTER}/></td>
            </tr>)}</tbody>
          </table>
        </div>
        <div className="pagination">
          <button disabled={!offset} onClick={() => setOffset(offset-pageSize)}>Назад</button>
          <span aria-live="polite">{fmt(offset+1)}–{fmt(Math.min(offset+pageSize,current.total!))} из {fmt(current.total!)}</span>
          <button disabled={offset+pageSize >= current.total!} onClick={() => setOffset(offset+pageSize)}>Далее</button>
        </div>
      </>}
  </section>;
}

function QualityView({rows, selection, open}: {rows?: Quality[]; selection: Selection; open: Drill}) {
  const count = (status: string, side: Source) => rows?.find(row => row.SOURCE === side && row.STATUS === status)?.CNT || 0;
  const issues = rows?.some(row => row.STATUS !== 'parsed' && row.CNT > 0);
  return <>
    <section className="sw-card sw-quality-summary">
      <div className={'sw-review-icon ' + (selection.excluded ? 'sw-warning' : 'sw-positive')}><ShieldCheck size={24} aria-hidden="true"/></div>
      <div><h2>{selection.excluded ? 'Некоторые заявки не вошли в сравнение' : selection.discovered ? 'Все заявки сопоставлены' : 'В выборке нет заявок'}</h2>
        <p>{fmt(selection.valid_pairs)} из {fmt(selection.discovered)} заявок имеют пригодные ответы в обеих версиях.</p></div>
      {selection.excluded > 0 && <button className="sw-link" onClick={() => open('Заявки вне сравнения','quality',{}, {excluded:true})}>Посмотреть заявки<ArrowRight size={16} aria-hidden="true"/></button>}
    </section>
    <section className="sw-card">
      <div className="sw-card-title"><div><h2>Проверка ответов</h2><p>Один номер заявки может встречаться в нескольких строках отчёта.</p></div></div>
      {rows ? <div className="sw-table"><table>
        <thead><tr><th scope="col">Что проверили</th>{sides.map(side => <th scope="col" key={side}>{sideLabel[side]}</th>)}</tr></thead>
        <tbody>{Object.entries({parsed:'Успешно прочитан',...qualityLabels}).map(([status,label]) => <tr key={status}>
          <th scope="row">{label}</th>{sides.map(side => {
            const n = count(status,side);
            return <td key={side}><button className="sw-app-id" disabled={!n}
              onClick={() => open(label,'quality',{STATUS:status,SOURCE:side})}>{fmt(n)}{n > 0 && <ChevronRight size={14} aria-hidden="true"/>}</button></td>;
          })}
        </tr>)}</tbody>
      </table>{rows.length > 0 && !issues && <p className="sw-inline-empty">Проблем с ответами не найдено.</p>}</div>
      : <p role="status" className="sw-inline-empty">Загружаем результаты проверки…</p>}
    </section>
  </>;
}

type DataProps = {
  datasets: Item[]; dataset: string; changeDataset: (id: string) => void;
  files?: Record<Source,number>; fileError?: string; busy: boolean;
  createDataset: (name: string) => Promise<unknown>;
  upload: (source: Source, files: File[]) => void;
  start: () => void;
};
function DataSetup({datasets, dataset, changeDataset, files, fileError, busy, createDataset, upload, start}: DataProps) {
  const [name, setName] = useState('');
  const counts = sides.map(side => files?.[side] || 0);
  const missing = sides.filter((_,i) => counts[i] === 0).map(side => sideLabel[side]);
  return <div className="sw-setup">
    <section className="sw-card sw-step">
      <span className="sw-step-number">1</span>
      <div className="sw-step-content">
        <h2>Выберите набор данных</h2>
        <p>Обе версии ответов должны находиться в одном наборе.</p>
        {datasets.length > 0 && <label>Набор данных<select value={dataset} onChange={e=>changeDataset(e.target.value)} aria-label="Набор данных">
          {datasets.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}
        </select></label>}
        <form className="sw-create-dataset" onSubmit={async event => {event.preventDefault();if (name.trim() && await createDataset(name.trim())) setName('');}}>
          <label>Название нового набора<input value={name} onChange={e=>setName(e.target.value)} placeholder="Например, проверка новой стратегии" maxLength={200}/></label>
          <button disabled={busy || !name.trim()}>Создать набор</button>
        </form>
      </div>
    </section>
    <section className="sw-card sw-step">
      <span className="sw-step-number">2</span>
      <div className="sw-step-content">
        <h2>Добавьте ответы для обеих версий</h2>
        <p>Подойдут JSON, XML и CSV. Один файл содержит ответ по одной заявке.</p>
        {fileError && <p role="alert">{fileError}</p>}
        <div className="sw-upload-grid">{sides.map((side,i) => <label className="sw-upload" key={side}>
          <UploadCloud size={22} aria-hidden="true"/><b>{sideLabel[side]}</b>
          <span>{!dataset ? 'Сначала выберите набор' : files ? filesInDataset(counts[i]) : 'Проверяем загруженные файлы…'}</span>
          <input type="file" multiple accept=".json,.xml,.csv" disabled={!dataset || busy}
            aria-label={'Загрузить файлы · ' + sideLabel[side]}
            onChange={e=>{const selected = Array.from(e.currentTarget.files || []);e.currentTarget.value='';if (selected.length) upload(side,selected);}}/>
        </label>)}</div>
      </div>
    </section>
    <section className="sw-card sw-step">
      <span className="sw-step-number">3</span>
      <div className="sw-step-content">
        <h2>Сравните решения</h2>
        <p>{!dataset ? 'Сначала выберите или создайте набор данных.' : fileError ? 'Обновите страницу, чтобы повторить проверку файлов.' : !files ? 'Проверяем загруженные файлы…' : missing.length ? 'Добавьте ответы для версий: ' + missing.join(', ') + '.' : 'После обработки откроется обзор.'}</p>
        <button className="sw-primary" disabled={busy || !dataset || !files || missing.length > 0} onClick={start}>
          <Play size={17} aria-hidden="true"/>Запустить сравнение
        </button>
      </div>
    </section>
  </div>;
}

type WorkspaceProps = {
  tab: SimpleTab; navigate: (tab: SimpleTab) => void; projects: Item[]; project: string; changeProject: (id: string) => void;
  run?: Run; runId: string; runs: Run[]; changeRun: (id: string) => void; report?: Overview;
  quality?: Quality[]; reportError?: string; qualityError?: string; error?: string; notice?: string;
  dismissError: () => void; dismissNotice: () => void; canWrite: boolean;
  toggleMode: (mode: InterfaceMode) => void; advanced: (tab: string) => void; logout: () => void;
  open: Drill; openApp: (appid: string) => void; download: (format: 'xlsx'|'html'|'xml') => void;
  downloading: string; startDemo?: () => void; demoBusy: boolean; demo?: boolean; administrator?: boolean;
  selectionKey: string; selectionLabel: string; resetSelection: () => void;
  loadApps: (filters: Filters, offset: number, limit: number) => Promise<{items: Application[]; total: number}>;
  data: DataProps;
};

export function SimpleWorkspace({
  tab,navigate,projects,project,changeProject,run,runId,runs,changeRun,report,quality,reportError,qualityError,error,notice,
  dismissError,dismissNotice,canWrite,toggleMode,advanced,logout,open,openApp,download,downloading,startDemo,demoBusy,demo,administrator,selectionKey,selectionLabel,resetSelection,loadApps,data,
}: WorkspaceProps) {
  const [group,setGroup] = useState<Group>('changed');
  const go = (next: SimpleTab, filter?: Group) => {if (filter) setGroup(filter);navigate(next);};
  const hasRun = run?.id === runId;
  const completed = hasRun && run?.status === 'completed';
  const running = hasRun && ['queued','running'].includes(run?.status || '');
  const failed = hasRun && run?.status === 'failed';
  const navigation = [
    ['overview','Обзор',LayoutDashboard], ['applications','Заявки',FileText],
    ['quality','Качество',ShieldCheck], ['data','Данные',Database],
  ] as const;
  return <>
    <a className="skip-link" href="#simple-content">К содержимому</a>
    <header className="sw-header">
      <div className="sw-header-inner">
        <button className="sw-brand" onClick={() => navigate('overview')} aria-label="Скорифай · на обзор"><span><Layers3 size={20} aria-hidden="true"/></span>скорифай<b>.</b></button>
        <nav aria-label="Основная навигация" className="sw-nav">{navigation.filter(([id])=>id!=='data'||canWrite).map(([id,label,Icon]) =>
          <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => navigate(id)}><Icon size={17} aria-hidden="true"/>{label}</button>)}</nav>
        <div className="sw-header-actions"><ModeSwitch mode="simple" onChange={toggleMode}/>
          <button className="sw-icon-button" aria-label="Выйти" title="Выйти" onClick={logout}><LogOut size={18}/></button>
        </div>
      </div>
    </header>
    <main id="simple-content" className="sw-main" tabIndex={-1}>
      {demo&&<DemoAccessNotice onSwitch={logout}/>}
      <div className="sw-context">
        <div className="sw-project">
          {projects.length > 1 ? <select value={project} onChange={e=>changeProject(e.target.value)} aria-label="Проект">
            {projects.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>
            : <span>{projects[0]?.name || 'Рабочая область'}</span>}
          {tab !== 'data' && runs.length > 1 && <select value={runId} onChange={e=>changeRun(e.target.value)} aria-label="Сравнение">
            <option value="">Выберите сравнение</option>
            {runs.map(item=><option key={item.id} value={item.id}>{item.dataset_name || 'Сравнение'} · {date(item.created)}</option>)}
          </select>}
          {administrator&&<span className="sw-chip">Администратор</span>}
        </div>
        {completed && tab !== 'data' && <span className="sw-ready"><CheckCircle2 size={15} aria-hidden="true"/>Сравнение готово</span>}
      </div>
      <div className="sw-heading">
        <div><h1>{titles[tab][0]}</h1><p>{titles[tab][1]}</p></div>
        {tab === 'overview' && <div className="sw-report-actions">
          {canWrite && completed && <button className="sw-download" onClick={() => navigate('data')}><Play size={16} aria-hidden="true"/>Новое сравнение</button>}
          {startDemo && !running && <button className="sw-download" disabled={demoBusy} onClick={startDemo}><Play size={16} aria-hidden="true"/>Повторить демосравнение</button>}
          {completed && <>
            <button className="sw-download" disabled={!!downloading} onClick={() => download('xlsx')}><Download size={16} aria-hidden="true"/>{downloading === 'xlsx' ? 'Готовим Excel…' : 'Скачать Excel'}</button>
            <button className="sw-download" disabled={!!downloading} onClick={() => download('html')}><Download size={16} aria-hidden="true"/>{downloading === 'html' ? 'Готовим HTML…' : 'Скачать HTML'}</button>
            <button className="sw-download" disabled={!!downloading} onClick={() => download('xml')}><Download size={16} aria-hidden="true"/>{downloading === 'xml' ? 'Готовим XML…' : 'Скачать XML'}</button>
          </>}
        </div>}
      </div>
      {completed && tab !== 'data' && <div className="sw-report-line"><span>{run?.snapshot?.dataset_name || 'Выбранное сравнение'}</span>
        {run && <><span aria-hidden="true">·</span><time dateTime={new Date(run.created*1000).toISOString()}>{date(run.created)}</time></>}
        {selectionLabel && <span className="sw-chip">{selectionLabel} <button className="sw-link" onClick={resetSelection}>Вернуть полный обзор</button></span>}
      </div>}
      {error && <div className="sw-message sw-negative" role="alert">{error}<button className="sw-icon-button" onClick={dismissError} aria-label="Закрыть ошибку"><X size={17}/></button></div>}
      {notice && <div className="sw-message sw-positive" role="status">{notice}<button className="sw-icon-button" onClick={dismissNotice} aria-label="Закрыть уведомление"><X size={17}/></button></div>}
      {!projects.length ? <section className="sw-card sw-state"><ShieldCheck size={31} aria-hidden="true"/><h2>Проект пока недоступен</h2><p>Администратор может выдать доступ к рабочей области.</p></section>
      : tab === 'data' && canWrite ? <DataSetup {...data}/>
      : !completed ? <section className="sw-card sw-state">
        {running ? <Activity size={32} aria-hidden="true"/> : <Layers3 size={32} aria-hidden="true"/>}
        <h2>{running ? 'Сравниваем две версии' : failed ? 'Сравнение не завершилось' : runId && !hasRun ? 'Открываем сравнение…' : 'Здесь появятся результаты'}</h2>
        <p>{running ? 'Проверяем ответы и сопоставляем заявки. Обзор обновится автоматически.' : failed ? 'Вернитесь к данным или откройте подробности обработки.' : canWrite ? 'Выберите набор данных и запустите первое сравнение.' : 'Результаты появятся после публикации сравнения.'}</p>
        {running && <progress value={run?.progress || 0} max={100} aria-label="Ход сравнения"/>}
        {!running && canWrite && (!runId || hasRun) && <button className="sw-primary" onClick={() => navigate('data')}>Перейти к данным<ArrowRight size={16} aria-hidden="true"/></button>}
        {failed && canWrite && <button className="sw-link" onClick={() => advanced('builder')}>Подробности обработки<ArrowUpRight size={16} aria-hidden="true"/></button>}
      </section> : reportError ? <section className="sw-card sw-state" role="alert"><h2>Обзор не загрузился</h2><p>{reportError}</p></section>
      : !report ? <section className="sw-card sw-state" role="status"><Activity size={28} aria-hidden="true"/><h2>Загружаем результаты…</h2></section>
      : tab === 'overview' ? <Dashboard report={report} open={open} go={go} advanced={advanced}/>
      : tab === 'applications' ? <Applications key={runId+selectionKey} runId={runId} rows={report.transitions} group={group} changeGroup={setGroup}
        selectionKey={selectionKey} load={loadApps} openApp={openApp}/>
      : qualityError ? <p className="sw-message sw-negative" role="alert">{qualityError}</p>
      : <QualityView rows={quality} selection={report.selection} open={open}/>}
      <footer className="sw-footer"><span>Скорифай</span><span>Сравнение кредитных решений</span></footer>
    </main>
  </>;
}

type Detail = {
  segments?: Application[];
  scorecards?: {PRODUCT:string; BEFORE?: ProductDecision; AFTER?: ProductDecision}[];
  quality?: {SOURCE:Source; STATUS:string}[];
};
type ProductDecision = {
  ACTIVE_DECISION: string;
  RAW_SCORE?: number;
  EFFECTIVE_SCORE?: number;
  CUTOFF?: number;
  ACTIVE_REASON?: string;
  FACTORS?: string[];
};
export function SimpleApplicationDetail({detail, openAdvanced, scenario}: {
  detail?: Detail; openAdvanced: () => void; scenario: boolean;
}) {
  if (!detail) return <p role="status" className="sw-inline-empty">Открываем заявку…</p>;
  const application = detail.segments?.[0];
  return <div className="sw-drawer-content">
    {application && <section className="sw-card sw-application-result"><h3>Итоговое решение</h3>
      <div><span><small>До изменений</small><DecisionBadge value={application.DECISION_BEFORE}/></span><ArrowRight size={19} aria-hidden="true"/>
        <span><small>После изменений</small><DecisionBadge value={application.DECISION_AFTER}/></span></div>
    </section>}
    {detail.quality?.some(row=>row.STATUS!=='parsed') && <section className="sw-card"><h3>Что с данными</h3><ul>
      {detail.quality.filter(row=>row.STATUS!=='parsed').map((row,i)=><li key={row.SOURCE+i}>{sideLabel[row.SOURCE]} · {qualityLabels[row.STATUS] || row.STATUS}</li>)}
    </ul></section>}
    {Boolean(detail.scorecards?.length) && <details className="sw-card sw-product-details">
      <summary>Оценки и причины по продуктам</summary>
      {detail.scorecards?.map(product => <section key={product.PRODUCT}>
        <h4>Продукт {product.PRODUCT}</h4>
        <div className="sw-product-columns">{sides.map(side=>{
          const row = product[side];
          return <div key={side}><b>{sideLabel[side]}</b>
            {row ? <><DecisionBadge value={row.ACTIVE_DECISION}/>
              <p>Балл <strong>{(scenario ? row.EFFECTIVE_SCORE : row.RAW_SCORE) ?? '—'}</strong> · порог <strong>{row.CUTOFF ?? '—'}</strong></p>
              {row.ACTIVE_REASON && <p>Причина: {row.ACTIVE_REASON}</p>}
              {!!row.FACTORS?.length && <p>Сработали правила: {row.FACTORS.join(', ')}</p>}
            </> : <p>Ответа по продукту нет.</p>}
          </div>;
        })}</div>
      </section>)}
    </details>}
    <button className="sw-link" onClick={openAdvanced}>Открыть поля и исходные документы<ArrowUpRight size={16} aria-hidden="true"/></button>
  </div>;
}
