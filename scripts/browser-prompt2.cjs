/* Real browser uploads, with the normal API, worker and saved artifacts. Use a fresh local workspace. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.BASE_URL || 'http://127.0.0.1:8765';
const scorePath = 'decisionResult.product_trials.scoring.pd.value';
const trials = 'decisionResult.product_trials';
const ids = ['app-1','app-2','app-3','app-4'];
const scores = [[.42,.58],[.55,.70],[.73,.75],[.60,.60]];
const sideIndex = side => side === 'BEFORE' ? 0 : 1;
const credentials = process.env.SPR_TEST_CREDENTIALS
  ? JSON.parse(fs.readFileSync(process.env.SPR_TEST_CREDENTIALS, 'utf8'))
  : {name:'admin', password:'admin'};

function document(id, side) {
  const index = Math.max(ids.indexOf(id), 0);
  const score = scores[index][sideIndex(side)];
  const decision = index === 1 ? 'REJECT' : 'APPROVE';
  return {
    appData:{application_id:id}, code:'0012', text:'Кириллица; "кавычки", запятая', empty:'',
    ['only_' + side]:'есть только в одном срезе',
    decisionResult:{decision, product_trials:['1','2'].map(product_id => ({
      product_id, decision, scoring:{pd:{modelName:'demo', value:product_id === '1' ? score : .91}},
      cutOff:{value:.6}, rejectReason: decision === 'REJECT' ? 'Историческая причина' : '',
      rules:[{name:'Фактор A', hit:product_id === '1' && [1,2].includes(index)},
             {name:'Фактор B', hit:product_id === '1' && index === 1}],
    }))},
  };
}
function xml(key, value) {
  if (Array.isArray(value)) return value.map(x => xml(key,x)).join('');
  if (typeof value === 'object') return '<'+key+'>'+Object.entries(value).map(([k,v])=>xml(k,v)).join('')+'</'+key+'>';
  const escaped = String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
  return '<'+key+'>'+escaped+'</'+key+'>';
}
function file(fmt, delimiter, id, side) {
  const doc = document(id,side);
  const quote = s => '"'+String(s).replaceAll('"','""')+'"';
  const raw = fmt === 'json' ? JSON.stringify(doc) : fmt === 'xml' ? xml('root',doc)
    : Object.keys(doc).join(delimiter)+'\n'+Object.values(doc).map(v=>quote(typeof v==='object'?JSON.stringify(v):v)).join(delimiter)+'\n';
  return {name:id+'.'+fmt,mimeType:'text/plain',buffer:Buffer.from(raw)};
}
const pathOf = response => new URL(response.url()).pathname;
async function json(response) {
  assert.ok(response.ok(), await response.text());
  return response.json();
}

(async () => {
  const browser = await chromium.launch({channel:process.env.BROWSER_CHANNEL||'chrome',headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const nav = name => page.getByRole('navigation').getByRole('button',{name,exact:true}).click();
  const button = name => page.getByRole('button',{name,exact:true});
  const label = name => page.getByLabel(name,{exact:true});
  const response = (end, method='GET') => page.waitForResponse(r=>pathOf(r).endsWith(end)&&r.request().method()===method);
  const close = () => button('Закрыть детали').click();
  const drawer = page.getByRole('dialog');
  const common = page.locator('.common-analysis').first();
  const controls = async () => {
    if (await common.getAttribute('open') === null) await common.locator('summary').first().click();
  };
  const apply = async () => {
    const result = response('/overview');
    await button('Применить выборку').click();
    return json(await result);
  };
  const openTab = async (name, endpoint) => {
    const result = response(endpoint);
    await nav(name);
    return json(await result);
  };
  const upload = async (side, files) => {
    await label('Срез').selectOption(side);
    const result = response('/upload','POST');
    await page.locator('input[type=file][multiple]').setInputFiles(files);
    assert.equal((await json(await result)).stored, Math.min(files.length,100));
    await page.getByText('Сохранено '+files.length+' ответов. Запустите обработку для валидации.',{exact:true}).waitFor();
  };
  const run = async () => {
    await nav('Конструктор');
    await button('Выбрать все').click();
    const result = response('/runs','POST');
    await button('Запустить обработку').click();
    const {id} = await json(await result);
    await page.waitForURL(url=>url.searchParams.get('run')===id);
    await page.getByText('● Завершён',{exact:true}).waitFor({timeout:90000});
    return id;
  };
  const scoreRows = async () => {
    await nav('Проверки');
    await page.getByRole('textbox',{name:'Проверка, атрибут или бакет',exact:true}).fill(scorePath);
    await page.locator('tbody tr').filter({hasText:scorePath}).first().waitFor();
  };
  try {
    await page.goto(base);
    await page.locator('input[name=name]').fill(credentials.name);
    await page.locator('input[name=password]').fill(credentials.password);
    await button('Войти').click();
    const formats = [['json',';'],['xml',';'],['csv',';'],['csv',',']];
    const snapshots = [];
    for (const [format, delimiter] of formats) {
      await nav('Данные и профили');
      await label('Редактируемый профиль').selectOption({label:'main'});
      const editor = page.locator('.json-editor');
      await page.waitForFunction(()=>document.querySelector('.json-editor').value.includes('bucket_width'));
      const profile = JSON.parse(await editor.inputValue());
      profile.delimiter = delimiter;
      profile.types = {[trials+'.product_id']:'string',[scorePath]:'number',[trials+'.cutOff.value']:'number',[trials+'.rules.hit']:'boolean'};
      profile.buckets = {[scorePath]:[{min:.3,max:.6,label:'(0.3 – 0.6]'},{min:.6,max:.9,label:'(0.6 – 0.9]'}]};
      profile.decision_analysis = {direction:'ge'};
      await editor.fill(JSON.stringify(profile,null,2));
      const saved = response('/records','POST');
      await button('Сохранить профиль').click();
      await json(await saved);
      await label('Название датасета').fill('Проверка импорта '+format+' '+delimiter+' '+Date.now());
      const created = response('/records','POST');
      await button('Создать датасет').click();
      await json(await created);
      for (const side of ['BEFORE','AFTER']) await upload(side,ids.map(id=>file(format,delimiter,id,side)));
      const id = await run();
      const quality = await openTab('Качество данных','/quality-summary');
      assert.deepEqual(quality.map(r=>[r.SOURCE,r.STATUS,r.CNT]),[['AFTER','parsed',4],['BEFORE','parsed',4]]);
      await page.getByText(/Проверка завершена без нарушений/).waitFor();
      const published = await json(await page.request.get(base+'/api/runs/'+id+'/checks'));
      assert.ok(published.some(r=>r.CHECK_ID));
      assert.ok(!published.some(r=>r.RESULT==='ERROR'),JSON.stringify(published.filter(r=>r.RESULT==='ERROR')));
      const details = await Promise.all(ids.map(async app=>json(await page.request.get(base+'/api/runs/'+id+'/apps/'+app))));
      const stripAddress = r => Object.fromEntries(Object.entries(r).filter(([key])=>!key.startsWith('ADDRESS')));
      snapshots.push({
        buckets:published.filter(r=>r.REPORT_VERSION===2).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))),
        comparison:details.map(d=>d.comparison.map(stripAddress)),
        products:details.map(d=>d.products),
      });
      const result = published.find(r=>r.PATH===scorePath&&r.LABEL==='BUCKET_TRANSITION'&&r.ARGUMENT.includes('(0.3 – 0.6] → (0.6 – 0.9]'));
      assert.equal(result.CNT,1); assert.equal(result.TOTAL,8); assert.deepEqual(result.APP_IDS,['app-2']);
      console.log('Imported and compared',format,delimiter,id);
    }
    for (const snapshot of snapshots.slice(1)) assert.deepEqual(snapshot,snapshots[0]);

    await scoreRows();
    await page.getByRole('combobox',{name:'Тип отчёта',exact:true}).selectOption('transition');
    await page.getByRole('row').filter({hasText:'(0.3 – 0.6] → (0.6 – 0.9]'}).getByRole('button').click();
    await drawer.locator('.application-row').first().waitFor();
    assert.deepEqual(await drawer.locator('.application-row b').allTextContents(),['app-2']);
    await close();

    await nav('Скоркарты');
    await controls();
    await label('Продукт').selectOption('1');
    await label('Источник решений').selectOption('scenario');
    await label('Моделирование стоп-фактора').selectOption('hard');
    const hard = await apply();
    assert.equal(hard.selection.discovered,4);
    await button('app-3 / 1').click();
    await drawer.getByRole('heading',{name:'Скоркарта заявки'}).waitFor();
    assert.match(await drawer.getByRole('row').filter({hasText:'Расчётное решение'}).innerText(),/Отказ\s+Отказ/);
    await drawer.locator('.native-grid pre').first().waitFor();
    await close();

    await label('Моделирование стоп-фактора').selectOption('penalty');
    await label('Штраф за каждый стоп-фактор').fill('0.1');
    await apply();
    await button('app-3 / 1').click();
    assert.match(await drawer.getByRole('row').filter({hasText:'Расчётное решение'}).innerText(),/Одобрено\s+Одобрено/);
    assert.match(await drawer.getByRole('row').filter({hasText:'Итоговый Score сценария'}).innerText(),/0\.63\s+0\.65/);
    await close();
    await button('app-2 / 1').click();
    assert.match(await drawer.getByRole('row').filter({hasText:'Основание сценария'}).innerText(),/Порог Score не пройден/);
    await close();

    await scoreRows();
    const bucket = page.getByRole('row').filter({hasText:scorePath+' · (0.3 – 0.6]'});
    await bucket.getByRole('button',{name:/3 \/ 4 · 75.00%/}).last().click();
    await drawer.locator('.application-row').first().waitFor();
    assert.deepEqual(await drawer.locator('.application-row b').allTextContents(),['app-1','app-2','app-4']);
    await drawer.getByRole('button',{name:/app-1/}).click();
    await drawer.getByText('Значение изменилось',{exact:true}).first().waitFor();
    await close();

    await controls();
    await page.getByText('Выбрать стоп-факторы · 0',{exact:true}).click();
    await label('Фактор A').check();
    await label('Фактор B').check();
    await label('Совпадение стоп-факторов').selectOption('any');
    assert.equal((await apply()).selection.discovered,2);
    await label('Совпадение стоп-факторов').selectOption('all');
    assert.equal((await apply()).selection.discovered,1);

    const rerunChecks = response('/scoped-checks','POST');
    await button('Запустить проверки Python для выборки').click();
    const scopedChecks = await json(await rerunChecks);
    assert.ok(scopedChecks.length);
    assert.ok(!scopedChecks.some(r=>r.RESULT==='ERROR'),JSON.stringify(scopedChecks.filter(r=>r.RESULT==='ERROR')));
    assert.ok(scopedChecks.every(r=>r.APP_IDS.every(id=>id==='app-2')));

    await nav('Сводная таблица');
    await page.locator('.pivot-table button.number').first().click();
    await drawer.locator('.application-row').first().waitFor();
    await drawer.locator('.application-row').first().waitFor();
    assert.deepEqual(await drawer.locator('.application-row b').allTextContents(),['app-2']);
    await close();
    const filteredQuality = await openTab('Качество данных','/quality-summary');
    assert.deepEqual(filteredQuality.map(r=>r.CNT),[1,1]);
    await nav('Сценарии');
    await button('app-2 / 1').waitFor();
    assert.equal(await button('app-3 / 1').count(),0);
    await page.getByText('Причины отказов и их изменение',{exact:true}).click();
    await page.getByRole('row').filter({hasText:'Порог Score не пройден'}).first().getByRole('button').click();
    await drawer.locator('.application-row').first().waitFor();
    assert.deepEqual(await drawer.locator('.application-row b').allTextContents(),['app-2']);
    await close();
    await nav('Обзор');
    await page.getByRole('heading',{name:'Итоги по решениям'}).waitFor();
    const matrix = page.getByRole('table',{name:/Матрица переходов/});
    await matrix.getByRole('button',{name:'1 · 100.00%',exact:true}).click();
    await drawer.locator('.application-row').first().waitFor();
    assert.deepEqual(await drawer.locator('.application-row b').allTextContents(),['app-2']);
    await close();

    await controls();
    await button('Сбросить фильтры').click();
    await label('Продукт').selectOption('1');
    await label('Близко к порогу').check();
    await label('Положение относительно порога').selectOption('equal');
    assert.equal((await apply()).selection.discovered,1);
    await nav('Скоркарты');
    await button('app-4 / 1').waitFor();
    await page.getByText('Только проходящие по Score в зоне порога · 1 пар',{exact:true}).waitFor();
    await controls();
    await label('Правило одобрения').selectOption('gt');
    await apply();
    await page.getByText('Только проходящие по Score в зоне порога · 0 пар',{exact:true}).waitFor();
    await label('Score от').fill('0.9');await label('Score до').fill('0.4');
    assert.ok(await button('Применить выборку').isDisabled());

    await nav('Данные и профили');
    await upload('AFTER',[{name:'broken.json',mimeType:'application/json',buffer:Buffer.from('{broken')},file('json',';','added','AFTER')]);
    const finalRun = await run();
    await nav('Качество данных');
    await page.getByText(/Найдены нарушения или несопоставленные ответы/).waitFor();
    await page.getByRole('row').filter({hasText:'Ошибка разбора'}).getByRole('button',{name:/1 \/ 6/}).click();
    await drawer.getByRole('button',{name:/broken/}).click();
    await drawer.getByText('{broken',{exact:true}).waitFor();
    await close();
    assert.equal((await json(await page.request.get(base+'/api/runs/'+finalRun+'/overview?analysis=%7B%7D'))).selection.added,1);
    if (process.env.SPR_BROWSER_OPEN_DATASET) {
      const source = process.env.SPR_BROWSER_OPEN_DATASET;
      const manifest = JSON.parse(fs.readFileSync(path.join(source,'manifest.json'),'utf8'));
      await nav('Данные и профили');
      await page.locator('.json-editor').fill(fs.readFileSync(path.join(__dirname,'../profiles/main.json'),'utf8'));
      const saved = response('/records','POST');
      await button('Сохранить профиль').click();
      await json(await saved);
      await label('Название датасета').fill('Открытый набор '+Date.now());
      const created = response('/records','POST');
      await button('Создать датасет').click();
      await json(await created);
      for (const side of ['BEFORE','AFTER']) {
        const folder = path.join(source,manifest.srez[side].dir);
        const files = fs.readdirSync(folder).filter(name=>/^I\d+\.json$/.test(name)).map(name=>path.join(folder,name));
        assert.equal(files.length,2002);
        await upload(side,files);
      }
      const publicRun = await run();
      const publicMeta = await json(await page.request.get(base+'/api/runs/'+publicRun));
      assert.equal(publicMeta.summary.valid_pairs,2002);
      assert.equal(publicMeta.summary.attributes,289533);
      await nav('Скоркарты');
      await controls();
      await label('Продукт').selectOption('5123');
      await label('Сторона фильтров').selectOption('BEFORE');
      await label('Источник решений').selectOption('scenario');
      await label('Правило одобрения').selectOption('ge');
      await label('Моделирование стоп-фактора').selectOption('penalty');
      await label('Штраф за каждый стоп-фактор').fill('0.1');
      await label('Близко к порогу').check();
      await label('Положение относительно порога').selectOption('equal');
      await apply();
      await button('I32556094 / 5123').click();
      await drawer.getByRole('heading',{name:'Скоркарта заявки'}).waitFor();
      assert.match(await drawer.getByRole('row').filter({hasText:'Итоговый Score сценария'}).innerText(),/0\.55/);
      assert.match(await drawer.getByRole('row').filter({hasText:'Расчётное решение'}).innerText(),/Одобрено/);
      await close();
      console.log('Open dataset imported in the browser',publicRun,publicMeta.summary.valid_pairs,publicMeta.summary.attributes);
    }
    assert.deepEqual(errors,[]);
    console.log('Browser workflow passed',finalRun);
  } catch (err) {
    await page.screenshot({path:path.join(process.env.TMPDIR||'/tmp','scorify-prompt2-browser-failure.png'),fullPage:true});
    console.error('Visible errors:',await page.getByRole('alert').allTextContents());
    throw err;
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1});
