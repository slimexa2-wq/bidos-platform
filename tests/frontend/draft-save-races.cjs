// Real React editors, synthetic HTTP data only. Delay responses across keyed remounts.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const URL = 'http://127.0.0.1:7521';
const key = scope => `bidos:project-draft:v1:p1:${scope}`;
let browser;
async function freshPage() {
  const response = await fetch('http://127.0.0.1:7522/__test__/control', { method: 'POST', body: JSON.stringify({ reset: true, mode: 'normal', delay: 0 }) });
  assert.equal((await response.json()).fixture, 'bidos-ux-synthetic');
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  await page.goto(URL + '/registration?select=p1', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('工作备注', { exact: true }).waitFor();
  assert.equal((await page.request.get(URL + '/api/workspace')).headers()['x-bidos-fixture'], 'ux-synthetic');
  return page;
}
async function select(page, name) {
  await page.locator('.ws-projects button').filter({ hasText: name }).click();
  await page.locator('.ws-work h2').filter({ hasText: name }).waitFor();
}
async function roundTrip(page) { await select(page, '文化中心'); await select(page, '市民服务'); }
async function pauseSave(page, button) {
  let release, arrived, fulfilled, rejectFulfillment;
  let first = true;
  const pattern = '**/api/workspace/projects/p1/work';
  const blocked = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { arrived = resolve; });
  const completed = new Promise((resolve, reject) => { fulfilled = resolve; rejectFulfillment = reject; });
  const handler = async route => {
    if (!first || route.request().method() !== 'POST') return route.continue();
    first = false;
    try {
      const response = await route.fetch();
      arrived();
      await blocked;
      await route.fulfill({ response });
      fulfilled();
    } catch (error) { rejectFulfillment(error); }
  };
  await page.route(pattern, handler);
  await page.getByRole('button', { name: button, exact: true }).click();
  await started;
  return async () => {
    const refresh = page.waitForResponse(response => response.url().includes('/api/workspace?refresh=true'));
    release();
    // Keep interception active until the held request is handled. Removing the
    // final route early lets Chromium continue that request before fulfillment.
    await completed;
    await page.unroute(pattern, handler);
    await refresh;
  };
}
async function stored(page, scope) { return page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), key(scope)); }
async function saveAgain(page, button) {
  const response = page.waitForResponse(response => response.url().includes('/projects/p1/work') && response.request().method() === 'POST');
  await page.getByRole('button', { name: button, exact: true }).click();
  assert.equal((await response).status(), 200, 'Newer edits must save against their own preceding successful write');
}
(async () => {
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'], timeout: 120000 });
  let page = await freshPage();
  await page.getByLabel('工作备注', { exact: true }).fill('first saved note');
  let finish = await pauseSave(page, '保存工作记录');
  await roundTrip(page);
  await page.getByLabel('工作备注', { exact: true }).fill('newer unsaved note');
  await finish();
  assert.equal((await stored(page, 'work:followup'))?.notes, 'newer unsaved note', 'An old response must not delete a newer shared draft');
  await roundTrip(page);
  assert.equal(await page.getByLabel('工作备注', { exact: true }).inputValue(), 'newer unsaved note');
  await page.reload({ waitUntil: 'domcontentloaded' });
  assert.equal(await page.getByLabel('工作备注', { exact: true }).inputValue(), 'newer unsaved note');
  await saveAgain(page, '保存工作记录');
  console.log('PASS delayed work save preserves newer edits after switch and reload');
  await page.context().close();

  page = await freshPage();
  finish = await pauseSave(page, '完成本次跟进');
  await roundTrip(page);
  await finish();
  await page.waitForFunction(() => document.querySelector('#next-action').value === '');
  assert((await page.getByLabel('工作备注', { exact: true }).inputValue()).includes('【跟进完成 '));
  assert.equal(await stored(page, 'work:followup'), null);
  console.log('PASS completed work save synchronizes the remounted editor');
  await page.context().close();

  page = await freshPage();
  finish = await pauseSave(page, '完成本次跟进');
  await roundTrip(page);
  await page.getByLabel('负责人', { exact: true }).fill('new owner during completion');
  await page.getByLabel('工作备注', { exact: true }).fill('new notes during completion');
  await finish();
  assert.equal(await page.getByLabel('负责人', { exact: true }).inputValue(), 'new owner during completion');
  assert.equal(await page.getByLabel('下一步', { exact: true }).inputValue(), '', 'Editing owner must not resurrect a completed follow-up');
  assert((await page.getByLabel('工作备注', { exact: true }).inputValue()).includes('new notes during completion'));
  assert((await page.getByLabel('工作备注', { exact: true }).inputValue()).includes('【跟进完成 '));
  await saveAgain(page, '保存工作记录');
  await page.reload({ waitUntil: 'domcontentloaded' });
  assert.equal(await page.getByLabel('下一步', { exact: true }).inputValue(), '');
  assert((await page.getByLabel('工作备注', { exact: true }).inputValue()).includes('【跟进完成 '));
  console.log('PASS editing owner and notes during completion retains newer edits, completed history and cleared reminder');
  await page.context().close();

  page = await freshPage();
  await page.getByLabel('工作备注', { exact: true }).fill('older pending save');
  finish = await pauseSave(page, '保存工作记录');
  await roundTrip(page);
  const refreshed = page.waitForResponse(response => response.url().includes('/api/workspace?refresh=true'));
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await refreshed;
  await page.getByRole('button', { name: '刷新', exact: true }).waitFor({ state: 'visible' });
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent === '刷新')?.disabled);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '放弃草稿', exact: true }).click();
  await page.getByLabel('工作备注', { exact: true }).fill('newer completed save');
  const newerRefresh = page.waitForResponse(response => response.url().includes('/api/workspace?refresh=true'));
  await saveAgain(page, '保存工作记录');
  await newerRefresh;
  await page.waitForFunction(() => !document.querySelector('#work-notes').disabled);
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent === '刷新')?.disabled);
  const refreshPattern = /\/api\/workspace\?refresh=true$/;
  await page.route(refreshPattern, route => route.fulfill({ status: 503, contentType: 'application/json', headers: { 'x-bidos-fixture': 'ux-synthetic' }, body: JSON.stringify({ detail: 'Synthetic read failure after old response' }) }));
  await finish();
  assert.equal(await page.getByLabel('工作备注', { exact: true }).inputValue(), 'newer completed save', 'An older response must not replace newer saved work even when the following read fails');
  await page.unroute(refreshPattern);
  await page.getByLabel('工作备注', { exact: true }).fill('newest unsaved after newer save');
  const current = await stored(page, 'work:followup');
  assert.equal(current.notes, 'newest unsaved after newer save');
  assert.equal(current.baseline.version, 3, 'An older response must not regress an already advanced baseline');
  await saveAgain(page, '保存工作记录');
  console.log('PASS older save cannot regress a baseline advanced by a newer successful save');
  await page.context().close();

  page = await freshPage();
  await page.getByLabel('材料名称', { exact: true }).fill('old pending material');
  finish = await pauseSave(page, '保存清单');
  await roundTrip(page);
  await page.getByLabel('材料名称', { exact: true }).fill('new pending material');
  await page.locator('details').filter({ hasText: '从公告正文识别材料要求' }).locator('summary').click();
  await page.getByLabel('报名要求原文', { exact: true }).fill('new announcement text');
  await finish();
  assert.equal((await stored(page, 'materials'))?.label, 'new pending material');
  await roundTrip(page);
  assert.equal(await page.getByLabel('材料名称', { exact: true }).inputValue(), 'new pending material');
  await page.locator('details').filter({ hasText: '从公告正文识别材料要求' }).locator('summary').click();
  assert.equal(await page.getByLabel('报名要求原文', { exact: true }).inputValue(), 'new announcement text');
  await saveAgain(page, '保存清单');
  console.log('PASS delayed materials save preserves newer fields after switch');
  await page.context().close();

  page = await freshPage();
  finish = await pauseSave(page, '保存清单');
  await page.getByLabel('材料名称', { exact: true }).fill('material typed during save');
  await finish();
  assert.equal(await page.getByLabel('材料名称', { exact: true }).inputValue(), 'material typed during save');
  await roundTrip(page);
  assert.equal(await page.getByLabel('材料名称', { exact: true }).inputValue(), 'material typed during save');
  await saveAgain(page, '保存清单');
  console.log('PASS materials typed during save remain editable and recoverable');
  await browser.close();
})().catch(async error => { console.error(error); if (browser) await browser.close(); process.exitCode = 1; });
