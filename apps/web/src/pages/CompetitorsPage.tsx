import { useEffect, useMemo, useRef, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { RelatedWorkHistory } from './WorkspaceUI'
import { Breakdown, countBy, Drawer, Fields, FieldSections, HeaderFilter, Metrics, Outcome, Pager, PortfolioSync, ResultSummary, SourceLocation, SourceRows } from '../components/PortfolioUI'
import { fiscalYear, usePortfolio } from '../services/portfolio'
import type { CompetitorProject } from '../services/portfolio'
import { COMPETITOR_SOURCE_FIELDS, COMPETITOR_TEMPLATE, competitorDetails, displayValue, populatedDetailTemplate, RESULT_SOURCE_FIELDS, RESULT_TEMPLATE, resultDetails, withSourceFields } from '../services/detailFields'

const pct = (n: number, d: number) => d ? `${(n / d * 100).toFixed(1)}%` : '—'
const profileLabels: Record<string, string> = { credit_code: '统一社会信用代码', reg_status: '登记状态', reg_capital: '注册资本', industry: '行业', actual_controller: '实际控制人', risk_summary: '风险摘要', related_companies: '关联企业', shareholders: '股东' }
type CompetitorDetail = CompetitorProject & { companyName: string; companyId: string }
export function CompetitorsPage() {
  const { data, error, loading, refresh } = usePortfolio()
  const [selected, setSelected] = useState(() => new URLSearchParams(location.search).get('select') || '')
  const linkedExpiry = new URLSearchParams(location.search).get('view') === 'expiry'
  const [year, setYear] = useState(linkedExpiry ? '' : fiscalYear()), [region, setRegion] = useState(''), [business, setBusiness] = useState(''), [kw, setKw] = useState('')
  const [view, setView] = useState(linkedExpiry ? '到期' : '项目'), [page, setPage] = useState(1)
  const [detail, setDetail] = useState<CompetitorDetail | null>(null)
  const linkedOpened = useRef(false)
  useEffect(() => {
    if (!data || linkedOpened.current) return
    linkedOpened.current = true
    const id = new URLSearchParams(location.search).get('project')
    const companyId = new URLSearchParams(location.search).get('select')
    if (id) setDetail(data.competitors.flatMap(c => c.projects.map(p => ({ ...p, companyName: c.name, companyId: c.id }))).find(p => p.id === id && (!companyId || p.companyId === companyId)) || null)
  }, [data])
  const firms = data?.competitors || [], focus = firms.find(c => c.id === selected)
  const all = firms.flatMap(c => c.projects.map(p => ({ ...p, companyName: c.name, companyId: c.id })))
  const detailTemplate = useMemo(() => {
    const projects = (data?.competitors || []).flatMap(c => c.projects)
    const fullTemplate = withSourceFields(COMPETITOR_TEMPLATE, projects, COMPETITOR_SOURCE_FIELDS)
    return populatedDetailTemplate(fullTemplate, (data?.competitors || []).flatMap(c => c.projects.map(p => competitorDetails(p, c.name, fullTemplate))))
  }, [data?.competitors])
  const ownResultTemplate = useMemo(() => {
    const results = data?.results || []
    const fullTemplate = withSourceFields(RESULT_TEMPLATE, results, RESULT_SOURCE_FIELDS)
    return populatedDetailTemplate(fullTemplate, results.map(r => resultDetails(r, fullTemplate)))
  }, [data?.results])
  const years = [...new Set(all.map(p => p.year).filter(Boolean))].sort().reverse()
  const scope = all.filter(p => (!selected || p.companyId === selected) && (!year || p.year === year) && (!region || (p.region || '未填写') === region) && (!business || (p.business || '未填写') === business) && (!kw || `${p.name} ${p.client} ${p.companyName} ${p.winner} ${p.amount} ${Object.values(p.fields).join(' ')} ${Object.values(p.notes).join(' ')}`.includes(kw)))
  const expired = all.filter(p => p.expiring && (!selected || p.companyId === selected) && (!region || (p.region || '未填写') === region) && (!business || (p.business || '未填写') === business) && (!kw || `${p.name} ${p.client} ${p.companyName} ${p.winner} ${p.amount} ${Object.values(p.fields).join(' ')} ${Object.values(p.notes).join(' ')}`.includes(kw))), overlaps = scope.filter(p => p.ours)
  const prices = scope.filter(p => p.amount), won = scope.filter(p => p.result === '中标'), lost = scope.filter(p => p.result === '未中标')
  const visible = [...(view === '报价' ? prices : view === '重合' ? overlaps : view === '到期' ? expired : scope)].sort((a, b) => view === '到期' ? a.endAt.localeCompare(b.endAt) : b.date.localeCompare(a.date))
  useEffect(() => setPage(1), [selected, year, region, business, kw, view])
  const cols = countBy(scope, p => focus ? p.region : p.companyName)
  const profileKeys = new Set(firms.flatMap(c => Object.entries(c.profile).filter(([, value]) => value !== undefined && value !== null && String(value).trim()).map(([key]) => key)))
  const prof = Object.fromEntries(Object.entries(profileLabels).filter(([key]) => profileKeys.has(key)).map(([key, label]) => [label, focus?.profile[key] ?? '']))
  return <section className="page portfolio-page"><PageHeader title="竞对分析" subtitle="重点名单 · 公开项目 · 报价与到期机会" /><PortfolioSync at={data?.snapshot?.generatedAt || data?.updatedAt} refresh={refresh} error={error} />
    <div className="portfolio-toolbar"><select aria-label="选择重点竞对" value={selected} onChange={e => setSelected(e.target.value)}><option value="">全部重点竞对</option>{firms.map(c => <option value={c.id} key={c.id}>{c.name}</option>)}</select><select aria-label="竞对统计财年" value={year} onChange={e => setYear(e.target.value)}><option value="">全部财年</option>{years.map(y => <option key={y} value={y}>{y}财年</option>)}</select><input type="search" aria-label="搜索竞对项目" value={kw} onChange={e => setKw(e.target.value)} placeholder="搜索企业、项目或甲方" /></div>
    {loading && <div className="skeleton">读取重点竞对原表中…</div>}
    {data && <>
      <Metrics items={[{ label: selected ? '当前重点竞对' : '重点竞对', value: selected ? '1家' : `${firms.length}家` }, { label: '已收录项目', value: scope.length, note: '包含我方未参与的项目' }, { label: '中标项目', value: won.length, note: `已知结果样本中标率 ${pct(won.length, won.length + lost.length)}`, color: 'var(--green)' }, { label: '90天内到期', value: expired.length, note: '全部历史项目 · 预计日期另行标注', color: 'var(--orange)' }]} />
      <div className="portfolio-charts"><div className="panel portfolio-chart"><h2>{focus ? '活跃区域' : '重点竞对项目分布'}</h2><p>当前筛选范围 · 已收录样本</p><Breakdown values={cols} onSelect={k => { if (focus) setRegion(region === k ? '' : k); else setSelected(firms.find(c => c.name === k)?.id || '') }} /></div><div className="panel portfolio-chart"><h2>{focus ? '甲方分布' : '业务分布'}</h2><p>根据原表及项目名称中的明示业务</p><Breakdown values={countBy(scope, p => focus ? p.client : p.business)} onSelect={focus ? undefined : k => setBusiness(business === k ? '' : k)} /></div></div>
      <p className="portfolio-policy">中标率仅代表已收录且有明确结果的样本；金额保留原口径，不将单价与总价混算。预计到期按招采时间与服务期限推算，供提前跟进，合同日期有原始记录时优先使用。</p>
      <div className="portfolio-tabbar">{['项目', '报价', '重合', '到期', '企业关系'].map(v => <button key={v} className={view === v ? 'active' : ''} onClick={() => { setView(v); if (v === '到期') setYear('') }}>{v} {v === '项目' ? scope.length : v === '报价' ? prices.length : v === '重合' ? overlaps.length : v === '到期' ? expired.length : ''}</button>)}</div>
      {view === '到期' && <p className="portfolio-policy">到期机会按当前90天窗口展示，覆盖全部历史合同。</p>}
      {view === '企业关系' ? <div className="panel portfolio-section"><h2>{focus?.name || '企业关系'}</h2>{focus ? Object.keys(prof).length ? <Fields fields={prof} keepEmpty /> : <p className="portfolio-empty">当前没有已核实的企业关系资料</p> : <p className="portfolio-empty">请选择一家重点竞对查看登记资料与关联关系</p>}</div> : <>
        <div className="panel portfolio-table"><div className="table-wrap"><table><colgroup><col style={{ width: '27%' }} /><col style={{ width: '19%' }} /><col style={{ width: '13%' }} /><col style={{ width: '12%' }} /><col style={{ width: '16%' }} /><col style={{ width: '13%' }} /></colgroup><thead><tr><th>项目 / 甲方</th><th>重点竞对</th><th>招采时间</th><th><HeaderFilter label="结果 / 地区" ariaLabel="筛选地区" value={region} options={Object.keys(countBy(all, p => p.region))} onChange={setRegion} /></th><th>{view === '重合' ? '我方结果 / 中标单位' : '报价 / 金额原文'}</th><th>{view === '到期' ? '到期日期' : <HeaderFilter label="业务 / 期限" ariaLabel="筛选竞对业务" value={business} options={Object.keys(countBy(all, p => p.business))} onChange={setBusiness} />}</th></tr></thead><tbody>{visible.slice((page - 1) * 30, page * 30).map(p => <tr key={`${p.companyId}-${p.id}`} onClick={() => setDetail(p)}><td><b className="portfolio-name" title={p.name}>{p.name}</b><small className="portfolio-clamp">{displayValue(p.client)}</small></td><td><button className="portfolio-name" title={p.companyName} style={{ border: 0, background: 'none', textAlign: 'left', padding: 0, fontSize: 'inherit' }} onClick={e => { e.stopPropagation(); setSelected(p.companyId) }}>{p.companyName}</button></td><td className="portfolio-date">{displayValue(p.date || p.dateRaw)}</td><td><Outcome value={p.result} /><small>{displayValue(p.region)}</small></td><td>{view === '重合' ? <><Outcome value={p.ours?.result || ''} />{p.winner && <small>{p.winner}</small>}</> : <div className="portfolio-clamp" title={p.amount}>{displayValue(p.amount)}</div>}</td><td>{view === '到期' ? <>{p.endAt}<small>{p.endEstimated ? '预计 · ' : ''}剩余{p.daysLeft}天</small></> : <>{displayValue(p.business)}{p.duration && <small>{p.duration}</small>}</>}</td></tr>)}</tbody></table></div>{!visible.length && <p className="portfolio-empty">{view === '重合' ? '当前范围没有名称与甲方可唯一匹配的重合项目' : view === '到期' ? '当前范围没有90天内到期项目' : '当前筛选没有项目'}</p>}<Pager total={visible.length} page={page} setPage={setPage} /></div>
        {focus && <section className="panel portfolio-section"><h2>业务规模与价格参考</h2><p>{scope.length}个收录项目 · {new Set(scope.map(p => p.client).filter(Boolean)).size}家甲方 · {prices.length}项报价记录。规模按样本呈现，未知单位的金额不估算收入。</p>{Object.keys(prof).length > 0 && <details><summary>登记资料与关系</summary><Fields fields={prof} keepEmpty /></details>}</section>}
      </>}
      <section className="panel portfolio-section"><details><summary>数据来源与采集状态</summary><p className="portfolio-policy">重点名单取自桌面竞对统计表的“公司名称”，当前{firms.length}家。公开采集沿用已有渠道目录，每10分钟整理一次已采集结果，有明确中标单位才能写入竞对当月 Excel。</p><Fields fields={{ 最近采集: data.collection.lastRun ? new Date(data.collection.lastRun).toLocaleString('zh-CN') : '', 可读取渠道: data.collection.usable, 暂未读取渠道: data.collection.failed, 采集状态: data.collection.status, 最近整理: data.publicSync?.lastRun ? new Date(data.publicSync.lastRun).toLocaleString('zh-CN') : '', 结果整理状态: data.publicSync?.status, 下一次整理: data.publicSync?.nextRun ? new Date(data.publicSync.nextRun).toLocaleString('zh-CN') : '', 新增项目: data.publicSync?.added, 整理问题: data.publicSync?.error }} />{data.notes.map(n => <p key={n}>{n}</p>)}</details></section>
    </>}
    {detail && <Drawer title={detail.name} onClose={() => setDetail(null)}>
      <ResultSummary label="竞对结果" value={detail.result} notes={detail.notes} />
      <FieldSections sections={competitorDetails(detail, detail.companyName, detailTemplate)} />
      <RelatedWorkHistory projectId={detail.ours?.id} sourceKeys={detail.sources || [detail.source]} />
      <SourceRows rows={detail.sourceRows} />
      {detail.endAt && <section className="portfolio-followup"><h3>到期跟进</h3><p>{detail.endEstimated ? '预计到期' : '合同到期'} {detail.endAt}{detail.daysLeft !== null ? ` · ${detail.daysLeft < 0 ? `已到期${-detail.daysLeft}天` : `剩余${detail.daysLeft}天`}` : ''}</p>{detail.endEstimated && <small>按招采时间与服务期限推算</small>}</section>}
      {detail.ours && <section className="portfolio-followup"><h3>我方对应项目资料</h3><Outcome value={detail.ours.result} />{detail.ours.participants && <p>{detail.ours.participants}</p>}<FieldSections sections={resultDetails(detail.ours, ownResultTemplate)} /><SourceRows rows={detail.ours.sourceRows} title="查看我方项目的各来源原始记录" />{(detail.ours.sources || [detail.ours.source]).map(source => <SourceLocation key={`${source.file}/${source.sheet}/${source.row}`} source={source} />)}</section>}
      {(detail.sources || [detail.source]).map(source => <SourceLocation key={`${source.file}/${source.sheet}/${source.row}`} source={source} />)}
    </Drawer>}
  </section>
}
