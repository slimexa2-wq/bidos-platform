// UX-only synthetic HTTP fixture. No production imports, network calls, Excel/NAS/DB access.
const http = require('http'), fs = require('fs');
const LOG = process.env.BIDOS_FIXTURE_LOG || require('path').join(require('os').tmpdir(), 'bidos-ux-fixture-requests.jsonl');
const today = '2026-11-09', now = '2026-11-09T09:00:00+08:00';
let mode = 'normal', delay = 0, syncFail = false, workspace, portfolio, blind;
const source = (row) => ({ file: '演示项目汇总.xlsx', sheet: '2026项目', row, fingerprint: 'synthetic-' + row });
const project = (id, name, row, partStatus = '报名参与') => ({ id, year: '2026', blindId: id, blindPath: '/blind?tab=' + encodeURIComponent(partStatus) + '&select=' + id, name, client: '演示市机关事务中心', channel: '政府采购网', partStatus, resultStatus: '待公布', deadline: '2026-11-11 17:00', openingAt: '2026-11-16 09:30', owner: row === 2 ? '李敏' : '王川', progress: '报名材料准备中', plan: '核对公告并准备授权委托书', priority: '重点', limit: '320万元', url: '', source: source(row), sources: [source(row)], sourceRows: [{ source: source(row), fields: { 项目名称: name, 报名参与情况: partStatus, 负责人: row === 2 ? '李敏' : '王川', 报名截止时间: '2026-11-11 17:00' } }], fields: { 项目名称: name, 负责人: row === 2 ? '李敏' : '王川' }, work: { version: 1, digest: 'synthetic-v1', sync: '工作记录已保存（演示）', owner: row === 2 ? '李敏' : '王川', notes: '已核对公告中的资质要求', decision: '', materials: [{ id: 'm1', label: '报名授权委托书', done: false }, { id: 'm2', label: '营业执照复印件', done: true }], documentText: '', attachments: [], updatedAt: now, nextAction: row === 2 ? '向代理确认报名材料份数' : '准备报价方案', dueAt: row === 2 ? today : '2026-11-10' }, hasPlan: true, isHistorical: false });
function reset() { workspace = { updatedAt: now, snapshot: { generatedAt: now, intervalMinutes: 60 }, projects: [project('p1', '【演示】市民服务中心物业服务项目', 2), project('p2', '【演示】文化中心安保服务项目', 3), project('p3', '【演示】园区后勤服务项目', 4, '待评估')], tasks: [], planHistory: [], sourceDocuments: [], notes: ['此环境全部为临时合成数据'] }; portfolio = { updatedAt: now, snapshot: { generatedAt: now, intervalMinutes: 60 }, sources: [], results: [], bonds: [{ id: 'b1', kind: '投标保证金', name: workspace.projects[0].name, client: workspace.projects[0].client, date: '2026-09-01', paidAt: '2026-09-03', amount: 20000, amountText: '20,000元', amountRaw: '20000', status: '逾期未退', statusRaw: '已缴纳', publishedAt: '2026-10-01', dueAt: '2026-11-01', refundedAt: '', owner: '李敏', company: '演示公司', note: '已向财务发起退回核对', dueBasis: '结果公布后一个月', overdueDays: 8, sources: [source(2)], resultStatus: '未中标', projectId: 'p1', sourceFields: { 项目名称: workspace.projects[0].name, 退回情况: '已缴纳', 保证金金额: '20000' }, sourceRows: [] }], competitors: [], reminders: [], excludedNonParticipants: 0, notes: [], collection: { status: '演示环境未运行采集' }, resultPolicy: '以原表为准', channelPolicy: '演示' }; blind = { snapshot: { generatedAt: now, intervalMinutes: 60 }, records: workspace.projects.map((p, i) => ({ id: p.id, name: p.name, client: p.client, agency: '演示采购代理有限公司', region: i === 1 ? '绵阳' : '成都', type: i === 1 ? '安保服务' : '物业服务', deadline: p.deadline, date: p.openingAt, year: '2026', limit: p.limit, month: '11月', row: i + 2, status: p.partStatus, result: '待公布', published: false, winner: '', amount: '', ownMark: '', ownMarkMissing: false, ledgerConflict: false, resultUrl: '', sync: i === 1 ? '需处理' : '已同步', syncErrors: i === 1 ? ['Excel正在打开，请关闭该文件后重试'] : [], fields: { 项目名称: p.name, 甲方名称: p.client, 代理机构: '演示采购代理有限公司', 项目区域: i === 1 ? '绵阳' : '成都', 项目类型: i === 1 ? '安保服务' : '物业服务', 报名参与情况: p.partStatus, 报名截止时间: p.deadline, 限价: '320万元', 备注: '请在报名截止前确认材料份数' }, ledger: {}, source: source(i + 2), sources: [source(i + 2)], sourceRows: [] })), candidates: [{ id: 'c1', name: '【演示】新区办公区保洁项目', client: '演示新区管理局', agency: '演示代理', region: '成都', type: '物业服务', amount: '180万元', budget: 1800000, deadline: '2026-11-13 17:00', url: '', channel: 'sichuan_gov_procurement', publishedAt: now, datePrecision: 'day', expiresAt: '2026-11-10T09:00:00+08:00', keywords: ['物业'], fields: { 报名截止时间: '2026-11-13 17:00', 项目情况简述: '办公区日常保洁服务' }, highlights: ['需核实现场踏勘要求'] }], automation: { status: 'ok', lastSync: now, message: '演示数据快照', resultMonitor: { status: '演示环境未运行', searched: 0 }, collection: { lastRun: now, status: '演示环境未运行采集', total: 3, usable: 2, failed: 1, newCount: 1, channels: [{ name: '演示采购网', status: '失败', message: '演示连接超时，请稍后重试' }] } }, rules: { resultIntervalMinutes: 10 } }; }
function tasks() { let a = []; for (const p of workspace.projects)
    if (p.work.nextAction)
        a.push({ id: 'w-' + p.id, kind: 'work', title: p.name, detail: '工作跟进 · ' + p.work.nextAction + ' · 负责人 ' + p.work.owner, severity: 'warning', at: p.work.dueAt, path: '/registration?select=' + p.id, projectId: p.id }); for (const b of portfolio.bonds)
    if (b.status === '逾期未退')
        a.push({ id: 't-' + b.id, kind: 'bond', title: b.name, detail: '投标保证金 20,000元 · 逾期8天 · 负责人李敏', severity: 'critical', at: b.dueAt, path: '/bid-bonds?select=' + b.id, projectId: b.projectId }); a.push({ id: 'a-p3', kind: 'assessment', title: workspace.projects[2].name, detail: '待评估 · 请确认是否报名', severity: 'warning', at: '2026-11-11', path: '/evaluation?select=p3', projectId: 'p3' }); return a.sort((a, b) => (a.severity !== 'critical') - (b.severity !== 'critical') || (a.at || '9999').localeCompare(b.at || '9999')); }
reset();
function send(res, status, obj) { res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*', 'x-bidos-fixture': 'ux-synthetic' }); res.end(JSON.stringify(obj)); }
http.createServer(async (req, res) => { let text = ''; for await (const x of req)
    text += x; const body = text ? JSON.parse(text) : {}, url = new URL(req.url, 'http://localhost'); const path = url.pathname; fs.appendFileSync(LOG, JSON.stringify({ time: new Date().toISOString(), method: req.method, path: req.url, body }) + '\n'); if (path === '/__test__/control') {
    if (body.reset)
        reset();
    if (body.mode !== undefined)
        mode = body.mode;
    if (body.delay !== undefined)
        delay = body.delay;
    if (body.syncFail !== undefined)
        syncFail = body.syncFail;
    return send(res, 200, { fixture: 'bidos-ux-synthetic', mode, delay, syncFail });
} if (delay)
    await new Promise(r => setTimeout(r, delay)); if (mode === 'permission')
    return send(res, 403, { detail: '没有权限访问此项目，请联系管理员' }); if (mode === 'failure')
    return send(res, 503, { detail: '数据服务暂时不可用，请稍后重试' }); if (['/api/sources/health', '/api/audit', '/api/feedback'].includes(path))
    return send(res, 200, []); if (path === '/api/workspace') {
    const w = structuredClone(workspace);
    w.tasks = tasks();
    if (mode === 'empty') {
        w.projects = [];
        w.tasks = [];
    }
    return send(res, 200, w);
} if (path.match(/^\/api\/workspace\/projects\/[^/]+\/work$/)) {
    const p = workspace.projects.find(p => p.id === path.split('/')[4]);
    if (mode === 'conflict')
        return send(res, 409, { detail: '工作记录已被其他人更新，请刷新后确认最新内容' });
    if (req.method === 'POST') {
        if (body.baseVersion !== p.work.version || body.baseDigest !== p.work.digest)
            return send(res, 409, { detail: '工作记录已被其他人更新，请刷新后确认最新内容' });
        p.work = { ...p.work, ...body, version: p.work.version + 1, digest: 'synthetic-v' + (p.work.version + 1), updatedAt: new Date().toISOString(), sync: '工作记录已保存（演示）' };
    }
    return send(res, 200, p.work);
} if (path === '/api/portfolio/workspace') {
    const p = structuredClone(portfolio);
    if (mode === 'empty')
        p.bonds = [];
    return send(res, 200, p);
} if (path.match(/^\/api\/portfolio\/bonds\/[^/]+\/refund$/)) {
    if (body.refundedAt > today)
        return send(res, 400, { detail: '已退回日期不能晚于今天' });
    const b = portfolio.bonds.find(b => b.id === path.split('/')[4]);
    b.status = '已退回';
    b.refundedAt = body.refundedAt;
    b.overdueDays = 0;
    b.note = body.note;
    return send(res, 200, { message: '已退回并同步（演示数据）' });
} if (/^\/api\/portfolio\/blind\/[^/]+\/save$/.test(path)) {
    const row = blind.records.find(row => row.id === path.split('/')[4]);
    if (!row) return send(res, 404, { detail: '演示项目不存在' });
    if ((body.edits || []).some(edit => (row.fields[edit.field] || '') !== edit.baseValue) || (body.ownMark && row.ownMark !== body.ownMark.baseValue))
        return send(res, 409, { detail: '演示项目已被更新，请核对后重试' });
    for (const edit of body.edits || []) row.fields[edit.field] = edit.value;
    if (body.ownMark) row.ownMark = body.ownMark.value;
    row.name = row.fields['项目名称'];
    row.status = row.fields['报名参与情况'] || row.fields['报名参与情况统计'] || row.status;
    row.sync = '已同步'; row.syncErrors = [];
    return send(res, 200, blind);
} if (path === '/api/portfolio/source/open')
    return send(res, 403, { detail: '演示环境未授权打开原表' }); if (path === '/api/blind/workspace')
    return send(res, 200, mode === 'empty' ? { ...blind, records: [], candidates: [] } : blind); if (path === '/api/blind/sync') {
    if (syncFail)
        return send(res, 409, { detail: 'Excel正在打开，请关闭该文件后重试' });
    blind.records.forEach(r => { r.sync = '已同步'; r.syncErrors = []; });
    return send(res, 200, { status: 'ok', message: '演示同步完成' });
} if (path === '/api/automation/status')
    return send(res, 200, { scheduler: { enabled: false, running: false, jobs: [] }, collection: blind.automation.collection, running: [], resultMonitor: {}, displaySnapshots: {} }); return send(res, 404, { detail: 'Synthetic fixture endpoint not implemented: ' + path }); }).listen(7522, '127.0.0.1', () => console.log('Synthetic fixtures only: http://127.0.0.1:7522'));
