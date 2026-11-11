// Dirty in-memory drafts must warn before document unload, even off their editor route.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const URL = 'http://127.0.0.1:7521';
let browser;
(async () => {
  const response = await fetch('http://127.0.0.1:7522/__test__/control', { method: 'POST', body: JSON.stringify({ reset: true, mode: 'normal', delay: 0 }) });
  assert.equal((await response.json()).fixture, 'bidos-ux-synthetic');
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'], timeout: 120000 });
  const context = await browser.newContext();
  await context.addInitScript(() => {
    const write = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith('bidos:project-draft:')) throw new DOMException('Synthetic quota failure', 'QuotaExceededError');
      return write.call(this, key, value);
    };
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  let warnings = 0;
  page.on('dialog', async dialog => {
    if (dialog.type() === 'beforeunload') { warnings++; await dialog.dismiss(); }
    else { assert.equal(dialog.type(), 'confirm'); await dialog.accept(); }
  });
  const home = async () => {
    await page.locator('.sidebar').getByRole('link', { name: '首页', exact: true }).click();
    await page.getByRole('heading', { name: '工作台', exact: true }).waitFor();
  };
  const registration = async () => {
    await page.locator('.sidebar').getByRole('link', { name: '报名管理', exact: true }).click();
    await page.locator('.ws-projects button').filter({ hasText: '市民服务' }).click();
    await page.getByLabel('工作备注', { exact: true }).waitFor();
  };
  await page.goto(URL + '/registration?select=p1', { waitUntil: 'domcontentloaded' });
  assert.equal((await page.request.get(URL + '/api/workspace')).headers()['x-bidos-fixture'], 'ux-synthetic');
  await page.getByLabel('工作备注', { exact: true }).fill('in-memory work draft');
  await page.getByLabel('材料名称', { exact: true }).fill('in-memory material draft');
  await home();
  // A dismissed beforeunload dialog cancels navigation; do not wait a full
  // normal navigation timeout before checking the retained page and draft.
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 5000 }).catch(() => {});
  assert.equal(warnings, 1, 'Leaving the editor must retain its native unload protection');
  await registration();
  assert.equal(await page.getByLabel('工作备注', { exact: true }).inputValue(), 'in-memory work draft');
  assert.equal(await page.getByLabel('材料名称', { exact: true }).inputValue(), 'in-memory material draft');
  await page.getByRole('button', { name: '放弃草稿', exact: true }).nth(1).click();
  await home();
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 5000 }).catch(() => {});
  assert.equal(warnings, 2, 'Discarding one draft must keep other unpersisted drafts protected');
  await registration();
  await page.getByRole('button', { name: '放弃草稿', exact: true }).click();
  await home();
  await page.reload({ waitUntil: 'domcontentloaded' });
  assert.equal(warnings, 2, 'Discarding every unpersisted draft should remove the unload warning');
  console.log('PASS failed-storage drafts retain native unload protection across route unmounts until every draft is discarded');
  await browser.close();
})().catch(async error => { console.error(error); if (browser) await browser.close(); process.exitCode = 1; });
