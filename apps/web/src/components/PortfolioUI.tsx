import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { StatusPill } from './StatusPill'
import { openPortfolioSource } from '../services/portfolio'
import type { SheetLocation, SourceDataRow } from '../services/portfolio'
import type { DetailSection } from '../services/detailFields'
import { displayValue, formatDiscountRate, sourceDate } from '../services/detailFields'
import './PortfolioUI.css'

export function Outcome({ value }: { value: string }) {
  if (!value.trim()) return <span className="portfolio-missing">/</span>
  const severity = value === '中标' || value === '已退回' ? 'success' : value === '未中标' || value === '逾期未退' ? 'critical' : value === '待公布' || value === '待退回' || value === '待确认' ? 'warning' : 'neutral'
  return <StatusPill severity={severity}>{value === '中标' || value === '已退回' ? '✓ ' : value === '逾期未退' ? '! ' : ''}{value}</StatusPill>
}
export function PortfolioSync({ at, refresh, error }: { at?: string; refresh: () => unknown; error: string }) {
  return <div className="portfolio-sync"><span><i className={`dot ${error ? 'critical' : 'success'}`} /> {error ? `读取失败：${error}` : at ? `数据快照 · ${new Date(at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}` : '正在读取数据快照'}</span><button className="btn ghost" onClick={() => refresh()}>刷新</button></div>
}
export function Metrics({ items }: { items: { label: string; value: ReactNode; note?: string; color?: string }[] }) {
  return <div className="portfolio-metrics">{items.map(x => <div key={x.label}><span>{x.label}</span><b style={{ color: x.color }}>{x.value}</b>{x.note && <small>{x.note}</small>}</div>)}</div>
}
export function Drawer({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => { const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }; const prior = document.body.style.overflow; document.body.style.overflow = 'hidden'; document.addEventListener('keydown', key); return () => { document.body.style.overflow = prior; document.removeEventListener('keydown', key) } }, [onClose])
  return <div className="portfolio-backdrop" onClick={onClose}><aside className="portfolio-drawer" role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}><header><h2>{title}</h2><button className="icon-btn" aria-label="关闭详情" onClick={onClose}>×</button></header>{children}</aside></div>
}
export function SourceLocation({ source, url }: { source: SheetLocation; url?: string }) {
  const [error, setError] = useState('')
  return <div className="portfolio-source"><span>{source.file} · {source.sheet}{source.row ? ` · 第${source.row}行` : ''}</span><div>{source.fingerprint && <button className="btn ghost" onClick={async () => { try { await openPortfolioSource(source); setError('') } catch (e) { setError(String(e instanceof Error ? e.message : e)) } }}>打开原表</button>}{url && /^https?:\/\//.test(url) && <a className="btn ghost" href={url} target="_blank" rel="noopener noreferrer">公告原文 ↗</a>}</div>{error && <small className="portfolio-error">{error}</small>}</div>
}
export function HeaderFilter({ label, ariaLabel, value, options, onChange }: { label: string; ariaLabel: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return <select className={`portfolio-header-filter${value ? ' filtered' : ''}`} aria-label={ariaLabel} title={`${label}：${value || '全部'}`} value={value} onChange={e => onChange(e.target.value)}>
    <option value="">{label}</option>{options.map(option => <option key={option} value={option}>{option}</option>)}
  </select>
}
const internalField = (key: string) => /^BidOS/i.test(key.trim()) || ['source_fingerprint', 'source_row', 'project_id', 'record_id', 'sync_id', 'fingerprint'].includes(key.trim().toLowerCase())
function FieldValue({ name, value }: { name: string; value: unknown }) {
  const text = value === undefined || value === null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)
  const parts = text.split(/(https?:\/\/[^\s<>"，。；（）]+)/g)
  return <>{parts.map((part, index) => {
    if (!/^https?:\/\//i.test(part)) return part
    try {
      const url = new URL(part)
      return <a key={index} className="portfolio-field-link" href={url.href} target="_blank" rel="noopener noreferrer" title={part}>{/公告|招标|招采/.test(name) ? '查看公告' : url.hostname} ↗</a>
    } catch { return part }
  })}</>
}
export function ResultSummary({ label, value, notes }: { label: string; value: string; notes: Record<string, string> }) {
  const entries = Object.entries(notes).filter(([key, text]) => !internalField(key) && displayValue(text) !== '/' && !text.startsWith('BidOS回写引擎接入验证'))
  return <div className="portfolio-result-summary">
    <div className="portfolio-detail-actions"><span>{label}</span><Outcome value={value} /></div>
    {entries.length > 0 && <div className="portfolio-detail-notes" aria-label="项目备注"><span className="portfolio-detail-note-label">备注</span><div className="portfolio-detail-note-content">{entries.map(([key, text]) => <p key={key}>{!['备注', '结果说明'].includes(key) && <span>{key}：</span>}<FieldValue name={key} value={text} /></p>)}</div></div>}
  </div>
}
export function Fields({ fields, title, notes = {}, keepEmpty = false, blankWhenMissing = [] }: { fields: Record<string, unknown>; title?: string; notes?: Record<string, string>; keepEmpty?: boolean; blankWhenMissing?: string[] }) {
  const entries = Object.entries(fields).filter(([k, v]) => !internalField(k) && (keepEmpty || (v !== '' && v !== null && v !== undefined) || notes[k]))
  return entries.length ? <>{title && <h3>{title}</h3>}<dl className="portfolio-fields">{entries.map(([k, v]) => <div key={k}><dt>{k}</dt><dd><FieldValue name={k} value={displayValue(v) === '/' && (blankWhenMissing.includes(k) || notes[k]) ? '' : displayValue(v)} />{notes[k] && <small className="portfolio-field-note">{notes[k]}</small>}</dd></div>)}</dl></> : null
}
export function SourceRows({ rows, title = '查看关联的各来源原始记录' }: { rows?: SourceDataRow[]; title?: string }) {
  if (!rows || rows.length < 2) return null
  return <details className="portfolio-source-details portfolio-source-rows">
    <summary>{title} · {rows.length}条</summary>
    {rows.map((row, index) => <section className="portfolio-source-row" key={`${row.source.file}/${row.source.sheet}/${row.source.row}/${index}`}>
      <p>{row.source.file} · {row.source.sheet}{row.source.row ? ` · 第${row.source.row}行` : ''}</p>
      {row.source.section && <small>原表分区：{row.source.section}</small>}
      {row.matchNote && <p className="portfolio-policy">{row.matchNote}</p>}
      <Fields fields={Object.fromEntries(Object.entries(row.fields || {}).map(([key, value]) => [key,
        /时间|日期/.test(key) ? sourceDate({ [key]: value }, key) || value
          : /折扣率|报价折扣率/.test(key) ? formatDiscountRate(value, /投标项目汇总.*\.xlsx/.test(row.source.file) && /2025年未参与/.test(row.source.sheet)) || value
            : value]))} notes={row.comments} />
    </section>)}
  </details>
}
export function FieldSections({ sections }: { sections: DetailSection[] }) {
  return <>{sections.map(section => {
    const pairs = Object.entries(section.fields).filter(([key]) => !internalField(key))
    const isFilled = ([key, value]: [string, string]) => displayValue(value) !== '/' || Boolean(section.notes?.[key])
    // 按「本条记录自身」是否填写分组：整段无内容不再铺满「/」。
    const filled = pairs.filter(isFilled), empty = pairs.filter(pair => !isFilled(pair))
    const block = (entries: [string, string][]) => <Fields fields={Object.fromEntries(entries)} notes={section.notes} keepEmpty blankWhenMissing={section.blankWhenMissing} />
    if (!filled.length) {
      if (section.collapsed) return null
      return <details className="portfolio-detail-section portfolio-detail-folded" key={section.title}><summary>{section.title}<em>全 {empty.length} 项未填写</em></summary>{block(empty)}</details>
    }
    const rest = empty.length ? <details className="portfolio-detail-rest"><summary>另 {empty.length} 项未填写</summary>{block(empty)}</details> : null
    return section.collapsed
      ? <details className="portfolio-source-details" key={section.title}><summary>{section.title}</summary>{block(filled)}{rest}</details>
      : <section className="portfolio-detail-section" key={section.title}><h3>{section.title}</h3>{block(filled)}{rest}</section>
  })}</>
}
export function Breakdown({ values, onSelect, selected }: { values: Record<string, number>; onSelect?: (key: string) => void; selected?: string }) {
  const [expanded, setExpanded] = useState(false)
  const rows = Object.entries(values).sort((a, b) => b[1] - a[1]); const max = Math.max(1, ...rows.map(x => x[1]))
  return <><div className="portfolio-breakdown">{rows.length ? (expanded ? rows : rows.slice(0, 8)).map(([key, n]) => <button type="button" key={key} title={`${key}：${n}项`} className={selected === key ? 'selected' : ''} onClick={() => onSelect?.(key)} disabled={!onSelect}><span>{key || '未填写'}</span><div><i style={{ width: `${n / max * 100}%` }} /></div><b>{n}</b></button>) : <p className="portfolio-empty">暂无数据</p>}</div>{rows.length > 8 && <button className="btn ghost" style={{ marginTop: 14, fontSize: 11 }} onClick={() => setExpanded(x => !x)}>{expanded ? '收起' : `展开其余${rows.length - 8}项`}</button>}</>
}
export function countBy<T>(items: T[], key: (x: T) => string): Record<string, number> { const c: Record<string, number> = {}; for (const x of items) { const k = key(x) || '未填写'; c[k] = (c[k] || 0) + 1 } return c }
export function Pager({ total, page, setPage, size = 30 }: { total: number; page: number; setPage: (p: number) => void; size?: number }) {
  const pages = Math.max(1, Math.ceil(total / size))
  return <footer className="portfolio-pager"><span>{total}条 · 每页{size}条</span><div><button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>{Math.min(page, pages)} / {pages}</span><button className="btn" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</button></div></footer>
}
