import React from 'react';
import {ArrowUpRight} from 'lucide-react';

type FieldGroup = {CNT: number; PATHS: string[]};
type ProductChange = {PRODUCT: string; PRODUCT_NAME?: string | null; FACTOR: string; APP_IDS: string[]; CNT: number};
type Drill = (title: string, kind: string, filters: Record<string,unknown>) => void;

const fieldTitles: Record<string,string> = {
  'decisionResult.available_offers.product_id': 'Код продукта в предложении',
  'decisionResult.available_offers.product_name': 'Название продукта в предложении',
  'decisionResult.available_offers.rank': 'Порядок показа предложения',
  'decisionResult.available_offers.type': 'Тип предложения',
  'decisionResult.decision': 'Итоговое решение по заявке',
  'decisionResult.product_trials.decision': 'Решение по отдельному продукту',
  'decisionResult.product_trials.rejectReason': 'Причина отказа по продукту',
  'decisionResult.product_trials.rejectReasonCode': 'Код причины отказа по продукту',
  'decisionResult.product_trials.cutOff.value': 'Порог одобрения по продукту',
  'decisionResult.product_trials.cutOff.name': 'Название правила порога',
  'decisionResult.product_trials.scoring.pd.value': 'Целевой балл по продукту',
  'decisionResult.product_trials.scoring.pd.modelName': 'Модель оценки по продукту',
  'decisionResult.product_trials.scoring.scoreModels.value': 'Балл отдельной модели по продукту',
  'decisionResult.scoring.value': 'Балл модели на уровне заявки',
  'decisionResult.product_trials.rules.hit': 'Срабатывание стоп-фактора',
  'decisionResult.product_trials.rules.name': 'Название стоп-фактора',
  'characteristics.score_okb': 'Оценка бюро кредитных историй',
};
const productTitles: Record<string,string> = {
  NOVYY_MFO: 'Новый МФО',
  NA_KARTU: 'На карту',
  PODPISKA: 'Подписка',
  KUPI_KUPI: 'Купи-купи',
};
const format = new Intl.NumberFormat('ru-RU');
const rules = new Intl.PluralRules('ru-RU');

export function fieldTitle(path: string) {
  return fieldTitles[path] || 'Поле «' + path + '»';
}

function countLabel(count: number, forms: [string,string,string]) {
  const form = rules.select(count);
  return format.format(count) + ' ' + forms[form === 'one' ? 0 : form === 'few' ? 1 : 2];
}

const applications = (count: number) => countLabel(count, ['заявка','заявки','заявок']);

function FieldLink({path, open}: {path: string; open: Drill}) {
  return <button className="text field-change-link" title={path}
    onClick={() => open(fieldTitle(path), 'facts', {path, diff_flag:1})}>
    {fieldTitle(path)}<ArrowUpRight size={14} aria-hidden="true"/>
  </button>;
}

export function FieldChanges({groups, ready, open, allFields}: {
  groups?: FieldGroup[]; ready: boolean; open: Drill; allFields: () => void;
}) {
  return <section className="panel overview-detail-panel">
    <div className="panel-head"><div><h2>Какие поля менялись чаще</h2>
      <p>Считаем заявки, в которых появилось, исчезло или изменилось поле.</p></div>
      <button className="text" onClick={allFields}>Все поля <ArrowUpRight size={15} aria-hidden="true"/></button>
    </div>
    <div className="overview-change-list">
      {!ready ? <p>Выберите завершённое сравнение.</p> : !groups ? <p>Загружаем изменения…</p> :
        !groups.length ? <p>В этой выборке поля совпадают.</p> : groups.slice(0,6).map(({PATHS,CNT}) =>
          <div className="overview-change-group" key={PATHS[0]}>
            <div className="overview-change-header">
              <b>{PATHS.length > 1 ? countLabel(PATHS.length,['поле','поля','полей'])+' изменились в одних и тех же заявках' :
                <FieldLink path={PATHS[0]} open={open}/>}</b>
              <button className="overview-count" onClick={() => open(
                PATHS.length > 1 ? 'Заявки с общими изменениями полей' : fieldTitle(PATHS[0]),
                'facts', {path:PATHS, diff_flag:1})}>
                {applications(CNT)} <ArrowUpRight size={14} aria-hidden="true"/>
              </button>
            </div>
            {PATHS.length > 1 && <ul className="overview-field-links">
              {PATHS.map(path => <li key={path}><FieldLink path={path} open={open}/></li>)}
            </ul>}
          </div>)}
    </div>
  </section>;
}

export function ProductChanges({items, ready, open}: {items?: ProductChange[]; ready: boolean; open: Drill}) {
  const products = new Map<string,{name:string; factors:ProductChange[]}>();
  for (const factor of items || []) {
    const code = factor.PRODUCT;
    const sourceName = factor.PRODUCT_NAME;
    const name = sourceName ? productTitles[sourceName] || sourceName : code ? 'Продукт с кодом '+code : 'Продукт без названия';
    if (!products.has(code)) products.set(code,{name,factors:[]});
    products.get(code)?.factors.push(factor);
  }
  return <section className="panel overview-detail-panel">
    <div className="panel-head"><div><h2>Что поменялось по продуктам</h2>
      <p>Здесь собраны заявки, у которых изменилось итоговое решение. Одна заявка может встречаться в нескольких строках.</p>
      <p>Порог — граница балла для одобрения. Стоп-фактор — условие, которое может привести к отказу.</p></div></div>
    <div className="overview-change-list">
      {!ready ? <p>Выберите завершённое сравнение.</p> : !items ? <p>Загружаем изменения по продуктам…</p> :
        !products.size ? <p>В этой выборке нет изменений по продуктам.</p> :
        [...products].map(([code,{name,factors}]) => <details className="overview-product" key={code}>
          <summary>{name}</summary>
          <p className="muted">В списке заявок откройте нужную, чтобы сравнить баллы, пороги и стоп-факторы до и после изменений.</p>
          {factors.map(factor => <button className="overview-factor" key={factor.FACTOR}
            onClick={() => open(name+' · '+factor.FACTOR, 'ids', {app_ids:factor.APP_IDS})}>
            <span>{factor.FACTOR}</span><b>{applications(factor.CNT)}</b><ArrowUpRight size={14} aria-hidden="true"/>
          </button>)}
        </details>)}
    </div>
  </section>;
}

export function DemoAccessNotice({onSwitch}: {onSwitch: () => void}) {
  return <div className="demo-access-notice">
    <p><b>Демонстрационный режим</b><span>Вы смотрите открытый набор. Для работы со своими данными войдите в личный аккаунт.</span></p>
    <button onClick={onSwitch}>Войти в свой аккаунт</button>
  </div>;
}
