import { useEffect, useMemo, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { RelatedWorkHistory } from './WorkspaceUI'
import { Breakdown, countBy, Drawer, Fields, FieldSections, HeaderFilter, Metrics, Outcome, Pager, PortfolioSync, ResultSummary, SourceLocation, SourceRows } from '../components/PortfolioUI'
import { fiscalYear, usePortfolio } from '../services/portfolio'
import { displayValue, populatedDetailTemplate, RESULT_SOURCE_FIELDS, RESULT_TEMPLATE, resultDetails, resultWinner, resultNotes, withSourceFields } from '../services/detailFields'

const colors = { 中标: '#16a475', 未中标: '#8ba9e5', 待公布: '#e7af52', 待确认: '#bbc4d5' }
const percent = (won: number, total: number) => total ? `${(won / total * 100).toFixed(1)}%` : '—'
const noteText = (notes: Record<string, string>) => Object.entries(notes).map(([key, value]) => key === '结果说明' || key === '备注' || key.startsWith('原表说明') ? value : `${key}：${value}`).join('；')
export function ResultsPage() {
  const { data, error, loading, refresh } = usePortfolio()
  const [year, setYear] = useState(fiscalYear())
  const [start, setStart] = useState(''), [end, setEnd] = useState('')
  const [channel, setChannel] = useState(''), [business, setBusiness] = useState('')
  const [kw, setKw] = useState(''), [result, setResult] = useState('全部')
  const [selected, setSelected] = useState(() => new URLSearchParams(location.search).get('select') || ''), [page, setPage] = useState(1)
  const [month, setMonth] = useState('')
  const items = data?.results || []
  const detailTemplate = useMemo(() => {
    const records = data?.results || []
    const fullTemplate = withSourceFields(RESULT_TEMPLATE, records, RESULT_SOURCE_FIELDS)
    return populatedDetailTemplate(fullTemplate, records.map(r => resultDetails(r, fullTemplate)))
  }, [data?.results])
  const supplements = data?.reconciliation?.unlinkedAuxiliary || []
  const years = [...new Set(items.map(x => x.year).filter(Boolean))].sort().reverse()
  const filteredItems = useMemo(() => items.filter(x => {
    if (year && x.year !== year) return false
    if (start && (!x.date || x.date < start)) return false
    if (end && (!x.date || x.date > end)) return false
    if (month && x.date.slice(0, 7) !== month) return false
    if (channel && (x.channel || '未填写') !== channel) return false
    if (business && (x.business || '未填写') !== business) return false
    return !kw || `${x.name} ${x.client} ${x.agency} ${x.winner} ${x.limit} ${x.amount} ${[x.fields, x.sourceFields, x.relatedFields, x.priceFields, x.cellNotes].flatMap(fields => Object.values(fields || {})).join(' ')} ${noteText(x.notes)}`.toLowerCase().includes(kw.toLowerCase())
  }), [items, year, start, end, month, channel, business, kw])
  const scope = filteredItems.filter(x => !/盲投项目信息/.test(x.source.file))
  const unconfirmed = filteredItems.filter(x => /盲投项目信息/.test(x.source.file))
  const visible = result === '月表待核对' ? unconfirmed : scope.filter(x => result === '全部' || x.result === result)
  useEffect(() => setPage(1), [year, start, end, month, channel, business, kw, result])
  const wins = scope.filter(x => x.result === '中标').length, losses = scope.filter(x => x.result === '未中标').length
  const pending = scope.length - wins - losses
  const months = Object.entries(countBy(scope.filter(x => x.date), x => x.date.slice(0, 7))).sort((a, b) => a[0].localeCompare(b[0]))
  const max = Math.max(1, ...months.map(x => x[1]))
  const sel = items.find(x => x.id === selected)
  const channels = countBy(items, x => x.channel), businesses = countBy(items, x => x.business)
  return <section className="page portfolio-page">
    <PageHeader title="结果复盘" subtitle="全部渠道 · 仅报名参与" />
    <PortfolioSync at={data?.snapshot?.generatedAt || data?.updatedAt} refresh={refresh} error={error} />
    <div className="portfolio-toolbar">
      <select aria-label="统计财年" value={year} onChange={e => { setYear(e.target.value); setMonth('') }}><option value="">全部财年</option>{years.map(y => <option key={y} value={y}>{y}财年</option>)}</select>
      <label>开标时间<input aria-label="起始时间" type="date" value={start} onChange={e => setStart(e.target.value)} />至<input aria-label="结束时间" type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
      <input type="search" aria-label="搜索结果项目" placeholder="搜索项目、甲方、代理、中标单位或备注" value={kw} onChange={e => setKw(e.target.value)} />
      {(channel || business || month) && <button className="btn ghost" onClick={() => { setChannel(''); setBusiness(''); setMonth('') }}>清除筛选</button>}
    </div>
    {loading && <div className="skeleton">读取原表中…</div>}
    {data && <>
      <Metrics items={[{ label: '参与项目', value: scope.length, note: '以投标项目汇总台账为准' }, { label: '中标率', value: percent(wins, wins + losses), note: `${wins}中标 / ${wins + losses}已出结果`, color: 'var(--green)' }, { label: '未中标', value: losses }, { label: '待公布 / 待确认', value: pending, note: `${scope.filter(x => x.result === '待公布').length}待公布 · ${scope.filter(x => x.result === '待确认').length}结果待确认`, color: 'var(--orange)' }]} />
      <div className="portfolio-charts">
        <div className="panel portfolio-chart"><h2>阶段中标率与参与情况</h2><p>{year ? `${Number(year) - 1}年12月—${year}年11月 · ` : ''}按开标月份 · 点击月份查看项目{month ? ` · 已选${month}` : ''}</p>
          {months.length ? <div className="portfolio-months">{months.map(([m, total]) => {
            const rs = scope.filter(x => x.date.startsWith(m)), won = rs.filter(x => x.result === '中标').length, lost = rs.filter(x => x.result === '未中标').length
            return <div className="portfolio-month" key={m}><button onClick={() => setMonth(month === m ? '' : m)} title={`${m}：参与${total}，中标${won}，未中标${lost}，未确认结果${total - won - lost}，中标率${percent(won, won + lost)}`}><span className="number">{total}项 · {percent(won, won + lost)}</span><div className="stack" style={{ height: `${total / max * 130}px` }}>{(['中标', '未中标', '待公布', '待确认'] as const).map(s => <i key={s} style={{ display: 'block', background: colors[s], height: `${rs.filter(x => x.result === s).length / total * 100}%` }} />)}</div><small>{year ? `${Number(m.slice(5))}月` : m}</small></button></div>
          })}</div> : <p className="portfolio-empty">当前范围暂无可分期数据</p>}
          <div className="portfolio-legend">{Object.entries(colors).map(([k, c]) => <span key={k}><i style={{ background: c }} />{k}</span>)}</div>
        </div>
        <div className="panel portfolio-chart"><h2>参与来源分布</h2><p>按原表“项目情况” · 点击筛选</p><Breakdown values={countBy(scope, x => x.channel)} selected={channel} onSelect={k => setChannel(channel === k ? '' : k)} /><p className="portfolio-policy">{data.channelPolicy}</p></div>
      </div>
      <p className="portfolio-policy">{data.resultPolicy} 汇总表“是否中标”决定我方结果，中标单位如实展示。业务按原表或项目名称中的明示词归类。</p>
      <div className="portfolio-tabbar">{['全部', '中标', '未中标', '待公布', '待确认', ...(unconfirmed.length ? ['月表待核对'] : [])].map(s => <button className={result === s ? 'active' : ''} key={s} onClick={() => setResult(s)}>{s} {s === '全部' ? scope.length : s === '月表待核对' ? unconfirmed.length : scope.filter(x => x.result === s).length}</button>)}</div>
      {result === '月表待核对' && <p className="portfolio-policy">这些月表记录标为报名参与，但尚未与汇总项目唯一对应，可能存在简称或日期差异。保留原记录供核对，暂不计入参与统计。</p>}
      <div className="panel portfolio-table"><div className="table-wrap"><table><colgroup><col style={{ width: '28%' }} /><col style={{ width: '12%' }} /><col style={{ width: '11%' }} /><col style={{ width: '10%' }} /><col style={{ width: '26%' }} /><col style={{ width: '13%' }} /></colgroup><thead><tr>
        <th>项目 / 甲方</th><th>开标时间</th><th><HeaderFilter label="参与来源" ariaLabel="筛选参与来源" value={channel} options={Object.keys(channels)} onChange={setChannel} /></th>
        <th>我方结果</th><th>中标单位 / 金额</th><th><HeaderFilter label="业务类型" ariaLabel="筛选业务类型" value={business} options={Object.keys(businesses)} onChange={setBusiness} /></th>
      </tr></thead><tbody>{visible.slice((page - 1) * 30, page * 30).map(x => <tr key={x.id} onClick={() => setSelected(x.id)}><td><b className="portfolio-name" title={x.name}>{x.name}</b><small className="portfolio-clamp">{displayValue(x.client)}</small></td><td className="portfolio-date">{displayValue(x.date)}</td><td>{displayValue(x.channel)}</td><td><Outcome value={x.result} /></td><td>{resultWinner(x) || x.amount ? <>{resultWinner(x) && <div className="portfolio-clamp" title={resultWinner(x)}>{resultWinner(x)}</div>}{x.amount && <small>{x.amount}</small>}</> : x.result === '待公布' || (x.winner && !resultWinner(x)) || ['流标', '废标', '重新招采'].includes(x.ownMark) ? null : '/'}</td><td>{displayValue(x.business)}</td></tr>)}</tbody></table></div>{!visible.length && <p className="portfolio-empty">当前筛选没有参与项目</p>}<Pager total={visible.length} page={page} setPage={setPage} /></div>
    </>}
    {supplements.length > 0 && <section className="panel portfolio-section portfolio-source-supplements"><details>
      <summary>源表补充记录（尚未唯一关联）· {supplements.length}条</summary>
      <p className="portfolio-policy">这些原表记录没有与结果台账项目可靠地唯一对应，保留在此供核查，不计入项目数和中标率。</p>
      {supplements.map((row, index) => <details className="portfolio-source-details" key={`${row.source.file}/${row.source.sheet}/${row.source.row}/${index}`}>
        <summary>{displayValue(row.fields['项目名称'])}{row.fields['甲方名称'] ? ` · ${row.fields['甲方名称']}` : ''} · {row.source.sheet} 第{row.source.row}行</summary>
        {row.source.section && <p className="portfolio-policy">原表分区：{row.source.section}</p>}
        {row.matchNote && <p className="portfolio-policy">{row.matchNote}</p>}
        <Fields fields={row.fields} notes={row.comments} />
        <SourceLocation source={row.source} />
      </details>)}
    </details></section>}
    {sel && <Drawer title={sel.name} onClose={() => setSelected('')}>
      <ResultSummary label="我方结果" value={sel.result} notes={resultNotes(sel)} />
      {sel.resultUrl && /^https?:\/\//.test(sel.resultUrl) && <p className="portfolio-policy"><a className="portfolio-field-link" href={sel.resultUrl} target="_blank" rel="noopener noreferrer">结果公告 ↗</a>{sel.publishedAt && ` · ${sel.publishedAt}`}</p>}
      <FieldSections sections={resultDetails(sel, detailTemplate)} />
      <RelatedWorkHistory projectId={sel.id} sourceKeys={sel.sources || [sel.source]} />
      <SourceRows rows={sel.sourceRows} />
      {(sel.sources || [sel.source]).map(source => <SourceLocation key={`${source.file}/${source.sheet}/${source.row}`} source={source} />)}
    </Drawer>}
  </section>
}
