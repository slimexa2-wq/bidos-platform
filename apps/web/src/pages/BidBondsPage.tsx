import { useEffect, useMemo, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { RelatedWorkHistory } from './WorkspaceUI'
import { Drawer, FieldSections, Metrics, Outcome, Pager, PortfolioSync, SourceLocation, SourceRows } from '../components/PortfolioUI'
import { formatMoney, localToday, refundBond, usePortfolio } from '../services/portfolio'
import type { PortfolioBond } from '../services/portfolio'
import { BOND_SOURCE_FIELDS, BOND_TEMPLATE, bondDetails, displayValue, populatedDetailTemplate, RESULT_SOURCE_FIELDS, RESULT_TEMPLATE, resultDetails, withSourceFields } from '../services/detailFields'

export function BidBondsPage() {
  const { data, error, loading, refresh } = usePortfolio()
  const [tab, setTab] = useState('待退回'), [kind, setKind] = useState(''), [kw, setKw] = useState('')
  const [selected, setSelected] = useState(() => new URLSearchParams(location.search).get('select') || '')
  const [refund, setRefund] = useState<PortfolioBond | null>(null), [refundDate, setRefundDate] = useState(localToday())
  const [note, setNote] = useState(''), [saving, setSaving] = useState(false), [saveError, setSaveError] = useState(''), [message, setMessage] = useState('')
  const [page, setPage] = useState(1)
  const items = (data?.bonds || []).filter(x => !kind || x.kind === kind)
  const detailTemplates = useMemo(() => Object.fromEntries(['投标保证金', '履约保证金'].map(kind => {
    const records = (data?.bonds || []).filter(b => b.kind === kind)
    const fullTemplate = withSourceFields(BOND_TEMPLATE, records, BOND_SOURCE_FIELDS)
    return [kind, populatedDetailTemplate(fullTemplate, records.map(b => bondDetails(b, fullTemplate)))]
  })), [data?.bonds])
  const linkedResultTemplate = useMemo(() => {
    const results = data?.results || []
    const fullTemplate = withSourceFields(RESULT_TEMPLATE, results, RESULT_SOURCE_FIELDS)
    return populatedDetailTemplate(fullTemplate, results.map(r => resultDetails(r, fullTemplate)))
  }, [data?.results])
  const outstanding = items.filter(x => !['已退回', '无需退回', '未缴纳'].includes(x.status))
  const overdue = items.filter(x => x.status === '逾期未退')
  const unknownDue = outstanding.filter(x => !x.dueAt)
  const visible = items.filter(x => (tab === '全部' || tab === '待退回' ? tab === '全部' || outstanding.includes(x) : x.status === tab) && (!kw || `${x.name} ${x.client} ${x.owner} ${x.note} ${x.amountText} ${x.paidAt} ${x.dueAt} ${x.refundedAt} ${x.statusRaw} ${Object.values(x.sourceFields || {}).join(' ')}`.includes(kw)))
  const sum = (rows: PortfolioBond[]) => rows.length && rows.every(x => x.amount !== null) ? formatMoney(rows.reduce((n, x) => n + (x.amount || 0), 0)) : rows.length ? '金额待核对' : '0元'
  useEffect(() => setPage(1), [tab, kind, kw])
  const sel = data?.bonds.find(x => x.id === selected)
  const linkedProject = sel?.projectId ? data?.results.find(result => result.id === sel.projectId) : undefined
  const beginRefund = (b: PortfolioBond) => { setRefund(b); setRefundDate(localToday()); setNote(''); setSaveError('') }
  const save = async () => {
    if (!refund) return
    setSaving(true); setSaveError('')
    try { const r = await refundBond(refund, refundDate, note); setMessage(r.message); setRefund(null); await refresh() }
    catch (e) { setSaveError(e instanceof Error ? e.message : '保存失败') }
    finally { setSaving(false) }
  }
  return <section className="page portfolio-page"><PageHeader title="保证金台账" subtitle="按原表管理 · 人员确认退回" /><PortfolioSync at={data?.snapshot?.generatedAt || data?.updatedAt} refresh={refresh} error={error} />
    {message && <div className="callout success" role="status">{message}</div>}
    <div className="portfolio-toolbar"><select aria-label="保证金类型" value={kind} onChange={e => setKind(e.target.value)}><option value="">全部保证金</option><option>投标保证金</option><option>履约保证金</option></select><input type="search" aria-label="搜索保证金" value={kw} onChange={e => setKw(e.target.value)} placeholder="搜索项目、甲方或跟进人" /></div>
    {loading && <div className="skeleton">读取保证金子表中…</div>}
    {data && <><Metrics items={[{ label: '待退回', value: outstanding.length, note: sum(outstanding) }, { label: '逾期未退', value: overdue.length, note: sum(overdue), color: 'var(--red)' }, { label: '已退回', value: items.filter(x => x.status === '已退回').length, color: 'var(--green)' }, { label: '应退日期待补', value: unknownDue.length, note: '原表未登记结果公布日或合同到期日' }]} />
      <p className="portfolio-policy">投标保证金：结果公布后一个月内退回，超期在首页提醒。履约保证金：沿用原表合同到期日期。缺少结果公布日期时，暂不推算应退日期。</p>
      <div className="portfolio-tabbar">{['待退回', '逾期未退', '已退回', '全部'].map(s => <button key={s} className={tab === s ? 'active' : ''} onClick={() => setTab(s)}>{s} {s === '全部' ? items.length : s === '待退回' ? outstanding.length : items.filter(x => x.status === s).length}</button>)}</div>
      <div className="panel portfolio-table"><div className="table-wrap"><table><colgroup><col style={{ width: '29%' }} /><col style={{ width: '15%' }} /><col style={{ width: '13%' }} /><col style={{ width: '15%' }} /><col style={{ width: '15%' }} /><col style={{ width: '13%' }} /></colgroup><thead><tr><th>项目 / 甲方</th><th>类型 / 金额</th><th>应退日期</th><th>退回情况</th><th>跟进记录</th><th>操作</th></tr></thead><tbody>{visible.slice((page - 1) * 30, page * 30).map(x => <tr key={x.id} onClick={() => setSelected(x.id)}><td><b className="portfolio-name" title={x.name}>{x.name}</b><small className="portfolio-clamp">{displayValue(x.client)}</small></td><td>{displayValue(x.amountText)}<small>{x.kind}</small></td><td>{x.dueAt || (!['已退回', '无需退回', '未缴纳'].includes(x.status) && x.resultStatus === '待公布' ? '待结果公布' : '')}{x.dueAt && <small>{x.dueBasis}</small>}</td><td><Outcome value={x.status} />{x.overdueDays > 0 && <small className="portfolio-error">逾期{x.overdueDays}天</small>}</td><td>{x.owner}{x.sourceFields?.['预计时间'] && <small>预计：{x.sourceFields['预计时间']}</small>}{x.refundedAt && <small>{x.refundedAt}退回</small>}</td><td>{!['已退回', '无需退回', '未缴纳'].includes(x.status) && <button className="btn" onClick={e => { e.stopPropagation(); beginRefund(x) }}>已退回</button>}</td></tr>)}</tbody></table></div>{!visible.length && <p className="portfolio-empty">当前筛选没有记录</p>}<Pager total={visible.length} page={page} setPage={setPage} /></div>
    </>}
    {sel && !refund && <Drawer title={sel.name} onClose={() => setSelected('')}><div className="portfolio-detail-actions"><Outcome value={sel.status} />{!['已退回', '无需退回', '未缴纳'].includes(sel.status) && <button className="btn primary" onClick={() => beginRefund(sel)}>已退回</button>}</div><FieldSections sections={bondDetails(sel, detailTemplates[sel.kind])} /><RelatedWorkHistory projectId={sel.projectId} sourceKeys={sel.sources} /><SourceRows rows={sel.sourceRows} title="查看保证金子表原始记录" />{linkedProject && <section className="portfolio-followup"><h3>同项目完整资料</h3><FieldSections sections={resultDetails(linkedProject, linkedResultTemplate)} /><SourceRows rows={linkedProject.sourceRows} title="查看同项目的各来源原始记录" />{(linkedProject.sources || [linkedProject.source]).map(source => <SourceLocation key={`${source.file}/${source.sheet}/${source.row}`} source={source} />)}</section>}{!['已退回', '无需退回', '未缴纳'].includes(sel.status) && <section className="portfolio-followup"><h3>退回跟进</h3>{sel.dueAt ? <p>应退日期 {sel.dueAt}{sel.overdueDays > 0 ? ` · 逾期${sel.overdueDays}天` : ''}</p> : <p className="portfolio-policy">{sel.kind === '投标保证金' ? sel.resultStatus === '待公布' ? '结果待公布，公布后按一个月期限跟进退回。' : '尚无可靠的结果公布日期，暂不推算应退日期。' : '原表未记录合同到期日期，暂不推算应退日期。'}</p>}</section>}{sel.refundedAt && <p className="portfolio-policy">实际退回日期 {sel.refundedAt}</p>}{sel.note && <p className="portfolio-policy">备注：{sel.note}</p>}{sel.sources.map(s => <SourceLocation key={`${s.file}-${s.sheet}-${s.row}`} source={s} />)}</Drawer>}
    {refund && <Drawer title="确认已退回" onClose={() => { if (!saving) setRefund(null) }}><p style={{ lineHeight: 1.7 }}>{refund.name}</p><p className="portfolio-policy">{refund.kind} · {refund.amountText}</p><div className="portfolio-form"><label>退回日期<input type="date" value={refundDate} max={localToday()} onChange={e => setRefundDate(e.target.value)} required /></label><label>备注（选填）<textarea rows={4} maxLength={2000} value={note} onChange={e => setNote(e.target.value)} placeholder="需要补充的情况" /></label>{saveError && <p className="portfolio-error" role="alert">{saveError}</p>}<button className="btn primary" disabled={saving || !refundDate} onClick={save}>{saving ? '保存中…' : '保存并同步 Excel'}</button></div></Drawer>}
  </section>
}
