// Run against a local instance initialized with the open dataset.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {performance} = require('node:perf_hooks');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const credentials = process.env.SPR_TEST_CREDENTIALS
    ? JSON.parse(fs.readFileSync(process.env.SPR_TEST_CREDENTIALS, 'utf8'))
    : {name:'admin', password:'admin'};
  fs.mkdirSync('tmp', {recursive:true});
  const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_CHANNEL ? {channel: process.env.BROWSER_CHANNEL} : {})});
  const context = await browser.newContext({viewport:{width:1440,height:1000}, acceptDownloads:true});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    const base = process.env.BASE_URL || 'http://127.0.0.1:8765';
    for(let i=0;i<40;i++) {
      try { if((await page.request.get(base+'/api/health')).ok()) break; } catch {}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    await page.goto(new URL('/?mode=advanced',base).href);
    await page.locator('input[name=name]').fill(credentials.name);
    await page.locator('input[name=password]').fill(credentials.password);
    await page.getByRole('button', {name: 'Войти', exact: true}).click();
    await page.getByRole('button', {name: 'Конструктор', exact: true}).click();
    await page.getByRole('button', {name: 'Выбрать все', exact: true}).click();
    const startedAt = performance.now();
    const [started] = await Promise.all([
      page.waitForResponse(response => response.request().method()==='POST' && /\/api\/projects\/[^/]+\/runs$/.test(response.url())),
      page.getByRole('button', {name: 'Запустить обработку', exact: true}).click()
    ]);
    if(!started.ok()) throw new Error('Run submission failed: '+await started.text());
    const runid = (await started.json()).id;
    await page.waitForURL(url => url.searchParams.get('run') === runid);
    await page.getByRole('button', {name:'Отменить',exact:true}).waitFor();
    const active = await (await page.request.get(base+'/api/runs/'+runid)).json();
    assert.ok(['queued','running'].includes(active.status), active.status);
    await page.reload();
    await page.getByRole('heading', {name:'Журнал прогона',exact:true}).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('run'),runid);
    await page.getByText('● Завершён', {exact: true}).waitFor({timeout: 180000});
    const runSeconds = (performance.now()-startedAt)/1000;
    const completed = await (await page.request.get(base+'/api/runs/'+runid)).json();
    assert.equal(completed.summary.valid_pairs,2002);
    assert.equal(completed.summary.changed_apps,502);
    assert.equal(completed.snapshot.checks.length,5);
    assert.ok(completed.log.some(entry=>entry.detail?.code));
    await page.getByText('История выполнения и технические подробности',{exact:true}).click();
    const normalization = page.locator('.journal-entry').filter({hasText:'Чтение и нормализация'});
    await normalization.getByText('Файлов обработано', {exact:false}).waitFor();
    assert.equal(await normalization.locator('pre, .cm-content').count(), 0);
    await page.getByText('Исполненный код этапа',{exact:true}).first().click();
    await page.locator('.journal-entry .cm-content').first().waitFor();
    await page.getByRole('button', {name: 'Обзор', exact: true}).click();
    await page.getByRole('button').filter({hasText: 'Обнаружено заявок'}).click();
    await page.locator('.application-row').first().click();
    await page.getByRole('heading', {name: 'Исходные документы'}).waitFor();
    await page.locator('.native-grid pre').first().filter({hasText: 'appData'}).waitFor();
    await page.getByRole('button', {name: 'Закрыть детали'}).click();
    await page.getByRole('button', {name: 'Сводная таблица', exact: true}).click();
    await page.locator('.pivot-table button.number').first().waitFor();
    await page.locator('.pivot-table button.number').first().click();
    await page.locator('.application-row').first().waitFor();
    await page.getByRole('button', {name: 'Закрыть детали'}).click();
    await page.getByLabel('Фильтр', {exact:true}).selectOption('1');
    await page.getByLabel('Колонки', {exact:true}).selectOption('SOURCE');
    const url = page.url();
    await page.reload();
    await page.locator('.pivot-table button.number').first().waitFor();
    if(await page.getByLabel('Фильтр', {exact:true}).inputValue()!=='1'||await page.getByLabel('Колонки', {exact:true}).inputValue()!=='SOURCE') throw new Error('Pivot layout was lost after refresh');
    if (page.url() !== url) throw new Error('Run/tab URL was lost after refresh');
    await page.screenshot({path: 'tmp/browser-pivot.png', fullPage: true});
    await page.getByRole('navigation').getByRole('button', {name: 'Проверки', exact: true}).click();
    const reportLink = await page.getByRole('link',{name:'Открыть по ссылке',exact:true}).getAttribute('href');
    assert.equal(new URL(reportLink,base).searchParams.get('run'),runid);
    const download = page.waitForEvent('download');
    await page.getByRole('link',{name:'Скачать отчёт проверок в HTML',exact:true}).click();
    await (await download).saveAs('tmp/browser-checks.html');
    const report = await context.newPage();
    await report.setContent(fs.readFileSync('tmp/browser-checks.html','utf8'));
    const detailLink = await report.locator('table a[href*="check="]').first().getAttribute('href');
    await report.goto(detailLink);
    await report.getByRole('dialog').locator('.application-row').first().waitFor();
    await report.close();
    await page.getByRole('button', {name: 'Обзор', exact: true}).click();
    await page.getByRole('button').filter({hasText: 'Обнаружено заявок'}).waitFor();
    await page.screenshot({path: 'tmp/browser-overview.png', fullPage: true});
    if (errors.length) throw new Error(errors.join('\n'));
    fs.writeFileSync('tmp/browser-report.json', JSON.stringify({
      url, runSeconds, validPairs:completed.summary.valid_pairs, changedApps:completed.summary.changed_apps,
      passed:['login','full run through UI','reload during processing','stage code','overview drill','native source',
        'pivot drill','pivot layout restored after reload','checks HTML download','check HTML link to applications'], pageErrors:errors,
    },null,2));
    console.log('Browser workflow passed; report: tmp/browser-report.json');
  } catch (error) {
    await page.screenshot({path:'tmp/browser-failure.png',fullPage:true});
    console.error('Visible errors:',await page.locator('[role=alert]').allTextContents());
    throw error;
  } finally {
    await browser.close();
  }
})().catch(e => {console.error(e); process.exitCode = 1});
