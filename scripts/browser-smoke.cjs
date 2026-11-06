// Run against a local instance. Credentials come from a local file, never stdout.
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const credentials = JSON.parse(fs.readFileSync(process.env.SPR_TEST_CREDENTIALS || 'var/local-access.json', 'utf8'));
  const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_CHANNEL ? {channel: process.env.BROWSER_CHANNEL} : {})});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    const base = process.env.BASE_URL || 'http://127.0.0.1:8765';
    for(let i=0;i<40;i++) {
      try { if((await page.request.get(base+'/api/health')).ok()) break; } catch {}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    await page.goto(base);
    await page.locator('input[name=name]').fill(credentials.name);
    await page.locator('input[name=password]').fill(credentials.password);
    await page.getByRole('button', {name: 'Войти в рабочую область'}).click();
    await page.getByRole('button', {name: 'Конструктор', exact: true}).click();
    const [started] = await Promise.all([
      page.waitForResponse(response => response.request().method()==='POST' && /\/api\/projects\/[^/]+\/runs$/.test(response.url())),
      page.getByRole('button', {name: 'Запустить обработку', exact: true}).click()
    ]);
    if(!started.ok()) throw new Error('Run submission failed: '+await started.text());
    const runid = (await started.json()).id;
    await page.waitForURL(url => url.searchParams.get('run') === runid);
    await page.getByText('● Завершён', {exact: true}).waitFor({timeout: 180000});
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
    await page.getByRole('button', {name: 'Обзор', exact: true}).click();
    await page.getByRole('button').filter({hasText: 'Обнаружено заявок'}).waitFor();
    await page.screenshot({path: 'tmp/browser-overview.png', fullPage: true});
    if (errors.length) throw new Error(errors.join('\n'));
    fs.writeFileSync('tmp/browser-report.json', JSON.stringify({url, passed: ['login', 'run through UI', 'overview drill', 'native source', 'pivot drill', 'refresh direct URL'], pageErrors: errors}, null, 2));
    console.log('Browser workflow passed; report: tmp/browser-report.json');
  } catch (error) {
    await page.screenshot({path:'tmp/browser-failure.png',fullPage:true});
    console.error('Visible errors:',await page.locator('[role=alert]').allTextContents());
    throw error;
  } finally {
    await browser.close();
  }
})().catch(e => {console.error(e); process.exitCode = 1});
