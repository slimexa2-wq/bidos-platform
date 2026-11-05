import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { calculateDiscountRate, displayValue, formatDiscountRate } from '../services/detailFields'
import { fiscalYear } from '../services/portfolio'
import type { SheetLocation, SourceDataRow } from '../services/portfolio'
import { SourceLocation, SourceRows } from '../components/PortfolioUI'
import { RelatedWorkHistory } from './WorkspaceUI'
import './BlindBidPage.css'

type Fields = Record<string, string>
type Row = { id: string; name: string; client: string; agency: string; region: string; type: string; deadline: string; date?: string; year?: string; limit?: string; month: string; row: number | string; status: string; result: string; published: boolean; winner: string; amount: string; ownMark: string; ownMarkMissing: boolean; ledgerConflict: boolean; resultUrl: string; sync: string; syncErrors: string[]; fields: Fields; ledger: Fields; cellNotes?: Record<string, string>; sourceRows?: SourceDataRow[]; source?: SheetLocation; sources?: SheetLocation[]; sourceWarnings?: string[]; readOnlyFields?: string[] }
type Candidate = { id: string; name: string; client: string; agency?: string; region: string; type?: string; amount?: string; budget: number | null; deadline: string; url: string; channel: string; publishedAt: string; datePrecision: string; expiresAt: string; keywords: string[]; fields?: Fields; highlights?: string[]; detailStatus?: string; detailMessage?: string }
type Workspace = { snapshot?: {generatedAt:string; intervalMinutes:number}; records: Row[]; candidates: Candidate[]; automation: { status?: string; lastSync?: string; message?: string; workbook?: string; intervalSeconds?: number; resultMonitor?: { status?: string; finishedAt?: string; searched?: number; updated?: number; errors?: string[] }; collection?: { lastRun?: string; status?: string; catalogCount?: number; total?: number; usable?: number; failed?: number; newCount?: number; channels?: { name: string; status: string; message?: string }[] } }; rules: { resultIntervalMinutes: number } }
const STATUS = ['待评估', '未报名', '报名未参与', '报名参与']
const TABS = ['新信息', '待评估', '报名参与', '报名未参与', '未报名', '台账']
const CHANNEL: Record<string, string> = { sichuan_gov_procurement: '四川政府采购网', sc_public_resource_trading: '四川省公共资源交易网', chengdu_public_resource_trading: '成都公共资源交易网' }
const FIELD_LABELS: Record<string, string> = { BidOS未参与原因: '补充未参与原因', BidOS备注: '补充备注', BidOS结果公布: '公布状态', BidOS中标金额原文: '中标金额', BidOS中标单位: '中标单位', BidOS结果公告网址: '结果公告网址', BidOS跟进人: '跟进人' }
const WINNER_KEYS = ['中标单位', 'BidOS中标单位']
const AMOUNT_KEYS = ['中标价格', '中标金额（万元）', '中标金额', 'BidOS中标金额原文']
const TENDER_AMOUNT_KEYS = ['限价(万元)', '限价（万元）', '限价', '最高限价', '最高限价（万元）', '最高限价（元）', '采购预算', '采购预算（万元）', '采购预算（元）', '预算金额', '预算金额（万元）', '预算金额（元）', '招标金额', '招标金额（万元）', '招标金额（元）']
const BLIND_DETAIL_GROUPS = [
    { title: '项目信息', fields: [
    { label: '甲方名称', keys: ['甲方名称'], fallback: 'client' },
    { label: '代理机构', keys: ['代理机构'], fallback: 'agency' },
    { label: '项目区域', keys: ['项目区域'], fallback: 'region' },
    { label: '项目类型', keys: ['项目类型', '业务类型'], fallback: 'type' },
    { label: '所属行业', keys: ['所属行业', '所属行业分类'] },
    { label: '甲方类型', keys: ['甲方类型'] },
    { label: '招标金额', keys: TENDER_AMOUNT_KEYS },
    { label: '招标时间', keys: ['招标时间'] },
    { label: '报名截止时间', keys: ['报名截止时间'], fallback: 'deadline' },
    { label: '开标时间', keys: ['开标时间'] },
    { label: '服务期限', keys: ['服务期限'] },
    { label: '报名费用', keys: ['报名费用', '报名费（元）'] },
    { label: '项目编号', keys: ['项目编号'] },
    { label: '采购方式', keys: ['采购方式', '项目类型（采购方式）'] },
    { label: '项目实施地点', keys: ['项目实施地点'] },
      { label: '项目情况简述', keys: ['项目情况简述'] },
  ] },
  { title: '投标资料', fields: [
    { label: '所属公司', keys: ['所属公司'] }, { label: '标书负责情况', keys: ['标书负责情况'] },
    { label: '资料份数', keys: ['资料份数'] }, { label: '报价类型', keys: ['报价类型'] },
    { label: '盲投排名', keys: ['盲投排名'] }, { label: '报价评分', keys: ['报价评分'] },
    { label: '是否踏勘', keys: ['是否踏勘'] }, { label: '系统录入', keys: ['系统录入'] }, { label: '人数', keys: ['人数'] },
  ] },
  { title: '费用与保证金', fields: [
    { label: '管理费（元/人/月）', keys: ['管理费（元/人/月）'] }, { label: '预估总价（万元）', keys: ['预估总价（万元）'] },
    { label: '打印费（元）', keys: ['打印费（元）'] }, { label: '中标服务费（元）', keys: ['中标服务费（元）'] },
    { label: '投标保证金（元）', keys: ['投标保证金（元）'] }, { label: '履约保证金（万元）', keys: ['履约保证金（万元）'] },
  ] },
  { title: '采购要求', fields: [
    { label: '资质要求', keys: ['资质要求'] }, { label: '业绩要求', keys: ['业绩要求'] },
    { label: '联合体投标', keys: ['联合体投标'] }, { label: '基本要求', keys: ['基本要求'] },
    { label: '其他要求', keys: ['其他要求'] },
  ] },
  { title: '跟进记录', fields: [
    { label: '参与单位', keys: ['参与单位'] },
    { label: '信息筛选人', keys: ['信息筛选人'] }, { label: '跟进人', keys: ['BidOS跟进人'] },
    { label: '未参与原因', keys: ['未参与原因', 'BidOS未参与原因'], onlyNonParticipant: true },
    { label: '备注', keys: ['备注', 'BidOS备注'] },
  ] },
] as const
const BLIND_OTHER_FIELD_EXCLUSIONS = new Set([
  ...BLIND_DETAIL_GROUPS.flatMap(section => section.fields.flatMap(group => [...group.keys])),
  ...WINNER_KEYS, ...AMOUNT_KEYS, ...TENDER_AMOUNT_KEYS, '折扣率', '报价折扣率', '序号', '项目名称', '甲方名称',
  '项目情况', '是否中标', '报名参与情况', '报名参与情况统计', 'BidOS项目ID', 'BidOS回写时间', 'BidOS中标单位',
  'BidOS中标金额原文', 'BidOS结果公布', 'BidOS跟进人', 'BidOS备注', 'BidOS未参与原因', 'BidOS结果公告网址',
])
const EMPTY_FILTERS = { region: '', type: '', result: '', sync: '', from: '', to: '' }
const VIEW_KEY = 'bidos.blind.return-view'
type ViewState = { tab: string; search: string; filters: typeof EMPTY_FILTERS; page: number; selected: string; sort: 'desc' | 'asc' }
const API = import.meta.env.VITE_API_BASE || '/api'

function returnView(): ViewState | null {
  try {
    const params = new URLSearchParams(window.location.search)
    if (params.has('tab') || params.has('select')) {
      const page = Number(params.get('page'))
      return { tab: TABS.includes(params.get('tab') || '') ? params.get('tab')! : '台账', search: params.get('search') || '',
        filters: Object.fromEntries(Object.keys(EMPTY_FILTERS).map(k => [k, params.get(k) || ''])) as typeof EMPTY_FILTERS,
        page: Number.isInteger(page) && page > 0 ? page : 1, selected: params.get('select') || '', sort: params.get('sort') === 'asc' ? 'asc' : 'desc' }
    }
    const view = JSON.parse(window.sessionStorage.getItem(VIEW_KEY) || 'null')
    if (!view || !TABS.includes(view.tab)) return null
    return { tab: view.tab, search: typeof view.search === 'string' ? view.search : '',
      filters: Object.fromEntries(Object.keys(EMPTY_FILTERS).map(k => [k, typeof view.filters?.[k] === 'string' ? view.filters[k] : ''])) as typeof EMPTY_FILTERS,
      page: Number.isInteger(view.page) && view.page > 0 ? view.page : 1,
      selected: typeof view.selected === 'string' ? view.selected : '', sort: view.sort === 'asc' ? 'asc' : 'desc' }
  } catch { return null }
}

function noticeUrl(value: string) {
  try {
    const url = new URL(value.trim())
    return ['http:', 'https:'].includes(url.protocol) ? url.href : ''
  } catch { return '' }
}

function isMoneyAmount(value: unknown) {
  const text = String(value ?? '').trim().replace(/[,，]/g, '')
  return /^[￥¥]?\s*[+-]?\d+(?:\.\d+)?\s*(?:元|万元)?$/.test(text)
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API}${path}`, { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) })
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请稍后重试')
  return data as T
}
function dateText(value: string) {
  if (!value) return ''
  if (/^\d{5}(?:\.\d+)?$/.test(value)) return new Date(Date.UTC(1899, 11, 30) + Number(value) * 86400000).toISOString().slice(0, 10)
  const m = value.match(/(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})/)
  return m ? `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}` : value
}
function timeText(value?: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '' }
function dateTimeText(value: string) {
  const date = dateText(value)
  const time = value.match(/[T\s](\d{2}:\d{2})/)
  return time && time[1] !== '00:00' ? `${date} ${time[1]}` : date
}
function candidateRow(x: Candidate): Row {
  return { id: x.id, name: x.name, client: x.client, agency: x.agency || '', region: x.region, type: x.type || '',
    deadline: x.deadline, month: '', row: '', status: '新信息', result: '', published: false, winner: '', amount: '',
    ownMark: '', ownMarkMissing: false, ledgerConflict: false, resultUrl: '', sync: '', syncErrors: [], ledger: {},
    fields: { ...(x.fields || {}), '项目名称': x.name, '甲方名称': x.client, '项目区域': x.region, '招标公告网址': x.url,
      '招标金额': x.fields?.['招标金额'] || x.amount || (x.budget !== null ? `${x.budget}元` : ''), '招标时间': x.fields?.['招标时间'] || x.publishedAt } }
}
function periodBrief(value: string) { return value.match(/\d+\s*(?:日历天|天|年|个月|月)/)?.[0] || value }
function tenderAmount(fields: Fields) {
  const key = TENDER_AMOUNT_KEYS.find(k => fields[k]?.trim() && !/^[\/／—–-]+$/.test(fields[k].trim()))
  if (!key) return ''
  const value = fields[key].trim()
  const unit = key.match(/[（(](万元|元)[）)]/)?.[1]
  return value.split('\n').map(line => {
    if (/^(?:[^：:]+[：:]\s*)?[\d,.]+$/.test(line)) return unit ? `${line} ${unit}` : `${line}（单位未标注）`
    return line
  }).join('\n')
}
function firstPriceField(fields: Fields, keys: string[]) {
  return keys.map(key => fields[key]?.trim() || '').find(value => value && !/^[\/／—–-]+$/.test(value)) || ''
}
function discountRate(fields: Fields, limit: string, winningPrice: string) {
  const sourceLimit = firstPriceField(fields, ['限价(万元)', '限价（万元）', '限价', '最高限价', '最高限价（万元）', '最高限价（元）', '采购预算', '采购预算（万元）', '采购预算（元）', '预算金额', '预算金额（万元）', '预算金额（元）', '招标金额', '招标金额（万元）', '招标金额（元）']) || limit
  const sourcePrice = firstPriceField(fields, ['中标价格', '中标金额（万元）', '中标金额']) || winningPrice
  return calculateDiscountRate(sourceLimit, sourcePrice)
    || formatDiscountRate(firstPriceField(fields, ['折扣率', '报价折扣率']))
}
function channelMessage(value?: string) { if (!value) return ''; if (/验证码|安全验证|anti_bot/.test(value)) return '网站需要人工验证'; if (/timeout|timed out/i.test(value)) return '网站访问超时'; if (/ConnectError|connection/i.test(value)) return '网站连接失败'; if (/HTTPStatusError/.test(value)) return '网站未返回可读取页面，请核对网址或访问限制'; return value }
function Badge({ value }: { value: string }) {
  if (!value) return null
  const participation = ({ 新信息: 'purple', 待评估: 'amber', 报名参与: 'blue', 报名未参与: 'orange', 未报名: 'neutral' } as Record<string, string>)[value]
  const tone = participation || (['中标', '已同步', '已公布'].includes(value) ? 'green' : ['待公布', '待写入', '待补标注'].includes(value) ? 'amber' : ['需处理', '未中标'].includes(value) ? 'red' : 'neutral')
  return <span className={`blind-badge ${tone}${participation ? ' participation' : ''}`}>
    {participation && <svg className="blind-status-icon" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="8" cy="8" r="6" />
      {value === '待评估' ? <path d="M8 4.5v3.8l2.5 1.5" /> : value === '报名参与' ? <path d="m5 8 2 2 4-4" /> : value === '新信息' ? <path d="M8 4.5v7M4.5 8h7" /> : <path d="M4.5 8h7" />}
    </svg>}{value}
  </span>
}
function formatField(key: string, value: string) {
  if (/折扣率/.test(key)) return formatDiscountRate(value)
  if (/时间|日期/.test(key) && !/期限/.test(key)) return dateTimeText(value)
  if (TENDER_AMOUNT_KEYS.includes(key)) {
    const amount = tenderAmount({ [key]: value })
    return amount && /限价|预算/.test(key) ? `${key.replace(/[（(].*?[）)]/g, '')}：${amount}` : amount
  }
  if (key === '中标金额（万元）' && /^[+-]?\d+(?:\.\d+)?$/.test(value)) return `${value} 万元`
  const unit = key.match(/[（(](万元|元)[）)]/)?.[1]
  if (unit && /^[+-]?[\d,]+(?:\.\d+)?$/.test(value)) return `${value} ${unit}`
  return value
}

// 合并同义表头；不同内容仍分别保留，编辑只回写已有源列。
function FieldBlock({ label, keys, fields, draft, editing, onChange, fallback = '', keepEmpty = true, onNavigate, mirrorAliases = true, notes = {} }: {
  label: string; keys: string[]; fields: Fields; draft: Fields; editing: boolean;
  onChange: (keys: string[], value: string) => void; fallback?: string; keepEmpty?: boolean; onNavigate?: () => void; mirrorAliases?: boolean; notes?: Record<string, string>
}) {
  const values = keys.map(k => {
    const value = formatField(k, fields[k] || '').trim()
    return label === '报名费用' && /^[+-]?[\d,]+(?:\.\d+)?$/.test(value) ? `${value}元` : value
  }).filter(Boolean)
    .filter(v => !(/备注|原因/.test(label) && (displayValue(v) === '/' || v.startsWith('BidOS回写引擎接入验证'))))
    .filter((v, i, all) => all.findIndex(other => label.includes('中标金额') || label === '报名费用' ? other.replace(/\s/g, '') === v.replace(/\s/g, '') : other === v) === i)
  if (!editing && !values.length && displayValue(fallback) === '/' && !keepEmpty) return null
  const editKeys = values.length > 1 || !mirrorAliases ? keys : [keys.find(k => Boolean(fields[k])) || keys[0]]
  const fieldNotes = Array.from(new Set(keys.map(k => notes[k]).filter(Boolean)))
  return <div className={`blind-field ${/名称|概况|简述|备注|原因|网址|链接/.test(label) ? 'wide' : ''}`}>
    <span className="blind-field-label">{label}</span>
    {editing && label === '中标金额' && editKeys.includes('中标金额（万元）') && <small className="blind-field-hint">原表单位：万元</small>}
    {editing && keys.length ? editKeys.map(k => <label key={k} className="blind-field-editor">
      {editKeys.length > 1 && <span>{FIELD_LABELS[k] || k}</span>}
      <textarea aria-label={editKeys.length > 1 ? (FIELD_LABELS[k] || k) : label} value={draft[k] || ''}
        onChange={e => onChange(editKeys.length > 1 || !mirrorAliases ? [k] : keys, e.target.value)} rows={/简述|备注|原因/.test(label) ? 3 : 1} />
    </label>) : <div className="blind-field-value">{/公告/.test(label) ? (values.length ? values : [displayValue(fallback)]).map((v, i) => noticeUrl(v) ? <a key={i} href={noticeUrl(v)} target="_blank" onClick={onNavigate} rel="noopener noreferrer">查看公告 ↗</a> : <span key={i}>{v}</span>) : displayValue(values.length ? values.join('\n') : fallback)}{fieldNotes.map((note, i) => <small className="blind-field-note" key={i}>{note}</small>)}</div>}
  </div>
}

export function BlindBidPage() {
  const [restoredView] = useState(returnView)
  const [data, setData] = useState<Workspace | null>(null)
  const [tab, setTab] = useState(restoredView?.tab || '台账')
  const [search, setSearch] = useState(restoredView?.search || '')
  const [filters, setFilters] = useState(restoredView?.filters || EMPTY_FILTERS)
  const [page, setPage] = useState(restoredView?.page || 1)
  const [selected, setSelected] = useState(restoredView?.selected || '')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [showAutomation, setShowAutomation] = useState(false)
  const [showRegion, setShowRegion] = useState(false)
  const [sort, setSort] = useState<'desc' | 'asc'>(restoredView?.sort || 'desc')
  const previousFilters = useRef(JSON.stringify([tab, search, filters, sort]))
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Fields>({})
  const [base, setBase] = useState<Fields>({})
  const [draftOwnMark, setDraftOwnMark] = useState('')
  const [baseOwnMark, setBaseOwnMark] = useState('')
  const load = useCallback(async (force = false) => { setData(await request<Workspace>(`/blind/workspace${force ? '?refresh=true' : ''}`)); setError('') }, [])
  useEffect(() => {
    load().catch(e => setError(e.message))
    const timer = window.setInterval(() => load().catch(e => setError(e.message)), 30000)
    const selectedParam = new URLSearchParams(window.location.search).get('select')
    if (selectedParam) setSelected(selectedParam)
    return () => window.clearInterval(timer)
  }, [load])
  useEffect(() => {
    const signature = JSON.stringify([tab, search, filters, sort])
    if (signature !== previousFilters.current) setPage(1)
    previousFilters.current = signature
  }, [tab, search, filters, sort])
  useEffect(() => {
    // 外链返回时用地址恢复视图；内置浏览器可能为导航重建存储会话。
    const url = new URL(window.location.href)
    const values = { tab, search, ...filters, page: page > 1 ? String(page) : '', select: selected, sort: sort === 'asc' ? sort : '' }
    Object.entries(values).forEach(([key, value]) => value ? url.searchParams.set(key, value) : url.searchParams.delete(key))
    if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url)
  }, [tab, search, filters, page, selected, sort])
  useEffect(() => { if (notice) { const timer = window.setTimeout(() => setNotice(''), 5000); return () => window.clearTimeout(timer) } }, [notice])
  useEffect(() => {
    if (!showRegion) return
    const close = (event: MouseEvent) => { if (!(event.target as Element).closest('.blind-region-filter')) setShowRegion(false) }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [showRegion])
  const [year, setYear] = useState(fiscalYear())
  const allRows = data?.records || []
  const rows = useMemo(() => allRows.filter(r => !year || r.year === year), [allRows, year])
  const years = [...new Set(allRows.map(r => r.year).filter((v): v is string => Boolean(v)))].sort().reverse()
  const candidates = (data?.candidates || []).filter(x => new Date(x.expiresAt).getTime() > Date.now())
  const detailGroups = useMemo(() => {
    const withSource = (records: Row[]) => {
      const mapped = BLIND_DETAIL_GROUPS.map(section => ({ ...section, fields: section.fields.filter(group => {
        if (section.title === '跟进记录') return records.some(record => group.keys.some(key => [record.fields, ...(record.sourceRows || []).map(row => row.fields)].some(fields => key in fields)))
        return records.some(record => group.keys.some(key => [record.fields, ...(record.sourceRows || []).map(row => row.fields)].some(fields =>
          key === '打印费（元）' ? isMoneyAmount(fields[key]) : displayValue(fields[key]) !== '/')) || ('fallback' in group && displayValue(record[group.fallback]) !== '/'))
      }) })).filter(section => section.fields.length)
      const extraKeys = Array.from(new Set(records.flatMap(record => [record.fields, ...(record.sourceRows || []).map(row => row.fields)].flatMap(fields => Object.entries(fields)
        .filter(([key, value]) => !BLIND_OTHER_FIELD_EXCLUSIONS.has(key) && !/^BidOS/i.test(key) && !/网址|链接/.test(key) && displayValue(value) !== '/')
        .map(([key]) => key))))).sort((a, b) => a.localeCompare(b, 'zh-CN'))
      const extras = extraKeys.length ? [{ title: '其他原表字段', fields: extraKeys.map(key => ({ label: key, keys: [key] })) }] : []
      return [...mapped, ...extras]
    }
    return { records: withSource(data?.records || []), candidates: withSource((data?.candidates || []).map(candidateRow)) }
  }, [data?.records, data?.candidates])
  const selectedCandidate = candidates.find(x => x.id === selected)
  const selectedRow = allRows.find(r => r.id === selected) || (selectedCandidate ? candidateRow(selectedCandidate) : undefined)
  const selectedDiscount = selectedRow ? discountRate(selectedRow.fields, selectedRow.limit || '', selectedRow.amount || '') : ''
  const discountKeys = selectedRow ? ['折扣率', '报价折扣率'].filter(key => key in selectedRow.fields) : []
  const discountFields = selectedRow ? { ...selectedRow.fields } : {}
  if (selectedDiscount) {
    if (discountKeys.length) discountKeys.forEach(key => { discountFields[key] = selectedDiscount })
    else discountFields['折扣率'] = selectedDiscount
  }
  const hasResultColumn = !['待评估', '未报名'].includes(tab)
  const counts = Object.fromEntries(TABS.map(t => [t, t === '台账' ? rows.length : t === '新信息' ? candidates.length : rows.filter(r => r.status === t).length]))
  const options = (key: 'region' | 'type') => Array.from(new Set(rows.map(r => r[key]))).filter(Boolean).sort()
  const filtered = useMemo(() => rows.filter(r => {
    if (tab !== '台账' && tab !== '新信息' && r.status !== tab) return false
    if (search && !`${r.name} ${r.client} ${r.agency} ${r.winner} ${Object.values(r.fields).join(' ')}`.toLowerCase().includes(search.toLowerCase())) return false
    for (const key of ['region', 'type', 'result', 'sync'] as const) if (filters[key] && (key === 'result' && filters[key] === '待补标注' ? !r.ownMarkMissing : r[key] !== filters[key])) return false
    const deadline = dateText(r.deadline || r.date || '')
    if (filters.from && (!/^\d{4}-\d{2}-\d{2}$/.test(deadline) || deadline < filters.from)) return false
    if (filters.to && (!/^\d{4}-\d{2}-\d{2}$/.test(deadline) || deadline > filters.to)) return false
    return true
  }).sort((a, b) => {
    if (a.status === '待评估' && b.status !== '待评估') return -1
    if (b.status === '待评估' && a.status !== '待评估') return 1
    const da = dateText(a.deadline || a.date || ''), db = dateText(b.deadline || b.date || '')
    if (!da) return db ? 1 : 0
    if (!db) return -1
    return sort === 'desc' ? db.localeCompare(da) : da.localeCompare(db)
  }), [rows, tab, search, filters, sort])
  const fresh = candidates.filter(x => !search || `${x.name} ${x.client} ${x.agency || ''} ${x.channel} ${Object.values(x.fields || {}).join(' ')}`.toLowerCase().includes(search.toLowerCase()))
  const pageCount = Math.max(1, Math.ceil(filtered.length / 30))
  const pageRows = filtered.slice((Math.min(page, pageCount) - 1) * 30, Math.min(page, pageCount) * 30)
  const activeFilters = Boolean(search || (tab !== '新信息' && Object.values(filters).some(Boolean)))
  function setFilter(key: keyof typeof filters, value: string) { setFilters(prev => ({ ...prev, [key]: value })) }
  function updateDraft(keys: string[], value: string) { setDraft(prev => ({ ...prev, ...Object.fromEntries(keys.map(k => [k, value])) })) }
  function closeDrawer() { setSelected(''); setEditing(false); setError('') }
  function rememberView() {
    try { window.sessionStorage.setItem(VIEW_KEY, JSON.stringify({ tab, search, filters, page, selected, sort })) } catch { /* 返回时仍可读取台账。 */ }
  }
  async function sync() {
    setBusy('sync'); setError(''); setNotice('')
    try { const result = await request<{ status: string; message?: string }>('/blind/sync', {}); await load(true); setNotice(result.status === 'busy' ? result.message || '正在同步，稍后自动更新' : '同步完成') } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function promote(x: Candidate) {
    setBusy(x.id); setError(''); setNotice('')
    try {
      const updated = await request<Workspace>(`/blind/${x.id}/save`, { promote: true })
      setData(updated); setTab('待评估'); setSelected(x.id)
      const row = updated.records.find(r => r.id === x.id)
      if (row?.sync === '需处理') setError(row.syncErrors.join('；'))
      else setNotice(row?.sync === '已同步' ? '已进入待评估，并写入收集月份的 Excel' : '已进入待评估，等待 Excel 可写后同步')
    } catch (e) { setError((e as Error).message); await load() } finally { setBusy('') }
  }
  function openRow(r: Row) { setSelected(r.id); setEditing(false); setError('') }
  function startEditing(r: Row) { setDraft({ ...r.fields }); setBase({ ...r.fields }); setDraftOwnMark(r.ownMark); setBaseOwnMark(r.ownMark); setEditing(true) }
  async function save() {
    if (!selectedRow) return
    const edits = Object.entries(draft).filter(([key, value]) => value !== base[key]).map(([field, value]) => ({ field, value, baseValue: base[field] || '' }))
    const ownMark = draftOwnMark !== baseOwnMark ? { value: draftOwnMark, baseValue: baseOwnMark } : undefined
    if (!edits.length && !ownMark) { setEditing(false); return }
    setBusy(selectedRow.id); setError(''); setNotice('')
    try {
      const updated = await request<Workspace>(`/portfolio/blind/${selectedRow.id}/save`, { edits, ownMark })
      setData(updated)
      const row = updated.records.find(r => r.id === selectedRow.id)
      if (row?.sync === '需处理') { setError(row.syncErrors.join('；')); return }
      setEditing(false); setNotice(row?.sync === '已同步' ? '已保存到桌面 Excel' : '已保存，等待 Excel 可写后同步')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  const automation = data?.automation
  const participationKeys = selectedRow ? ['报名参与情况', '报名参与情况统计'].filter(k => k in selectedRow.fields) : []
  const rawDraftStatus = draft[participationKeys[0]] || draft[participationKeys[1]]
  const selectedStatus = selectedRow ? editing && STATUS.includes(rawDraftStatus) ? rawDraftStatus : selectedRow.status : ''
  const detailFields = selectedRow ? { ...selectedRow.fields } : {}
  if (!editing && selectedRow) {
    const legacyFeeText = detailFields['打印费（元）']
    if (displayValue(legacyFeeText) !== '/' && !isMoneyAmount(legacyFeeText)) {
      delete detailFields['打印费（元）']
      const reason = detailFields['未参与原因'] || ''
      const remark = detailFields['备注'] || ''
      if (displayValue(reason) === '/' && selectedStatus === '报名未参与') detailFields['未参与原因'] = legacyFeeText
      else if (displayValue(remark) === '/' && legacyFeeText !== reason) detailFields['备注'] = legacyFeeText
    }
    if (selectedStatus === '报名未参与' && detailFields['备注'] && detailFields['备注'] === detailFields['未参与原因']) delete detailFields['备注']
  }
  const sourceKeys = selectedRow ? Object.keys(selectedRow.fields).filter(k => /网址|链接/.test(k) && !/结果/.test(k)) : []
  const sourceLinks = selectedRow ? sourceKeys.filter((k, i) => noticeUrl(selectedRow.fields[k]) && sourceKeys.findIndex(key => noticeUrl(selectedRow.fields[key]) === noticeUrl(selectedRow.fields[k])) === i) : []
  const drawerErrors = Array.from(new Set([error, ...(selectedRow?.syncErrors || [])].filter(Boolean)))
  const hasResult = selectedRow && ['报名参与', '报名未参与'].includes(selectedStatus)
  return <section className="page blind-workbench">
    <div className="blind-heading">
      <div><h1>盲投信息</h1><div className="blind-automation"><span className={`blind-dot ${automation?.status === 'ok' ? 'ok' : ''}`} /><span>{automation?.status === 'ok' ? `数据快照 · ${timeText(data?.snapshot?.generatedAt || automation.lastSync)}` : automation?.message || '正在读取数据快照'}</span><button onClick={() => setShowAutomation(!showAutomation)} aria-expanded={showAutomation}>自动化详情 {showAutomation ? '▴' : '▾'}</button></div></div>
      <button className="btn ghost" onClick={sync} disabled={Boolean(busy)}>{busy === 'sync' ? '同步中…' : '刷新并同步'}</button>
    </div>
    {showAutomation && <div className="blind-auto-detail">
      <div><strong>新信息</strong><p>收集近24小时的公告，经既有关键词与反向规则筛选。同一项目去重；首次收集24小时内未进入待评估自动清理。</p></div>
      <div><strong>结果跟进</strong><p>报名参与、报名未参与每10分钟分批查证结果及后续变更。未报名停止跟进。是否中标以项目汇总表标注为准。</p>{automation?.resultMonitor && <p>最近一轮：{automation.resultMonitor.status}{automation.resultMonitor.finishedAt ? ` · ${timeText(automation.resultMonitor.finishedAt)}` : ''}{automation.resultMonitor.searched !== undefined ? ` · 主动查证 ${automation.resultMonitor.searched} 个项目` : ''}{automation.resultMonitor.errors?.length ? ' · 部分来源查证失败，下一轮重试' : ''}</p>}</div>
      <div><strong>双向同步</strong><p>桌面 Excel 为工作正本。每分钟读取表格变更；系统保存后立即尝试回写，Excel正在编辑时等待下一轮。冲突提示人工核对。</p></div>
      {automation?.collection && <div className="blind-channel-status"><strong>渠道采集{automation.collection.catalogCount ? ` · 目录 ${automation.collection.catalogCount} 个渠道` : ''}</strong><p>每天16:30收集 · 最近运行 {timeText(automation.collection.lastRun)} · {automation.collection.status} · 本轮新增 {automation.collection.newCount || 0}{automation.collection.total !== undefined ? ` · 已读取 ${automation.collection.usable || 0} / ${automation.collection.total} 个采集入口` : ''}</p>{automation.collection.channels?.map((c, i) => <p key={i}>{c.name}：{c.status}{c.message ? ` · ${channelMessage(c.message)}` : ''}</p>)}</div>}
    </div>}
    {error && !selectedRow && <div className="blind-message error" role="alert">{error}</div>}
    {notice && <div className="blind-toast" role="status">{notice}</div>}
    <div className="blind-tabs" role="tablist" aria-label="盲投参与状态">{TABS.map(t => <button role="tab" aria-selected={tab === t} className={tab === t ? 'active' : ''} key={t} onClick={() => { setTab(t); setShowRegion(false); if (['待评估', '未报名'].includes(t)) setFilter('result', '') }}>{t === '台账' ? '全部' : t}<b>{counts[t]}</b></button>)}</div>
    <div className="blind-toolbar">
      <input className="blind-search" aria-label="全字段搜索" placeholder="搜索项目、甲方、代理、中标单位或备注…" value={search} onChange={e => setSearch(e.target.value)} />
      {tab !== '新信息' && <><select aria-label="盲投财年" value={year} onChange={e => { setYear(e.target.value); setPage(1) }}><option value="">全部财年</option>{years.map(y => <option key={y} value={y}>{y}财年</option>)}</select><div className="blind-date-range"><span>截止 / 开标</span><input type="date" aria-label="起始日期" value={filters.from} onChange={e => setFilter('from', e.target.value)} /><span>至</span><input type="date" aria-label="结束日期" min={filters.from || undefined} value={filters.to} onChange={e => setFilter('to', e.target.value)} /></div></>}
      {activeFilters && <button className="blind-clear" onClick={() => { setSearch(''); setFilters(EMPTY_FILTERS) }}>清空</button>}
    </div>
    {data === null ? <div className="blind-empty">正在读取工作台…</div> : tab === '新信息' ? <>
      <div className="blind-queue-note">24小时内进入待评估，才写入收集月份的 Excel；未选信息自动清理。</div>
      {fresh.length === 0 && <div className="blind-empty">暂无符合条件的新信息</div>}
      {fresh.length > 0 && <div className="blind-table-wrap"><table className="blind-table blind-new-table"><thead><tr>
        <th>项目名称</th><th>甲方 / 代理</th><th>区域 / 类型</th><th>报名截止</th><th>招标金额 / 限价</th><th>操作</th>
      </tr></thead>{fresh.map(x => <tbody key={x.id} className="blind-new-project">
        <tr><td><button className="blind-project" title={x.name} onClick={() => { setSelected(x.id); setEditing(false); setError('') }}>{x.name}</button><small>{CHANNEL[x.channel] || x.channel}<br />{dateText(x.publishedAt)} 发布</small></td>
          <td><div className="blind-cell-client" title={x.client}>{displayValue(x.client)}</div>{x.agency && <small>代理：{x.agency}</small>}</td>
          <td>{displayValue(x.region)}<small>{displayValue(x.type)}</small></td>
          <td>{displayValue(dateText(x.fields?.['报名截止时间'] || x.deadline))}{(x.fields?.['报名截止时间'] || x.deadline).match(/[T\s](\d{2}:\d{2})/)?.[1] && <small>{(x.fields?.['报名截止时间'] || x.deadline).match(/[T\s](\d{2}:\d{2})/)?.[1]}</small>}</td>
          <td className="blind-tender-amount">{displayValue(tenderAmount(x.fields || {}) || x.amount || (x.budget !== null ? `${x.budget.toLocaleString('zh-CN')}元` : ''))}</td>
          <td><button className="blind-promote" disabled={Boolean(busy)} onClick={() => promote(x)}>{busy === x.id ? '写入中…' : '进入待评估'}</button><small>剩余 {Math.max(0, Math.ceil((new Date(x.expiresAt).getTime() - Date.now()) / 3600000))}h</small></td>
        </tr>
        <tr className="blind-review-row"><td colSpan={6}>
          {(x.highlights?.length || x.fields?.['服务期限']) ? <div><span>审核要点</span>{[x.fields?.['服务期限'] ? `服务 ${periodBrief(x.fields['服务期限'])}` : '', ...(x.highlights || [])].filter(Boolean).join(' · ')}</div> : x.fields?.['项目情况简述'] && <div><span>项目概况</span>{x.fields['项目情况简述']}</div>}
          {x.detailMessage && <div className="blind-attention">{x.detailMessage}</div>}
        </td></tr>
      </tbody>)}</table></div>}
    </> : <>
      <div className="blind-table-wrap"><table className="blind-table"><thead><tr>
        <th>项目名称</th><th>甲方 / 代理</th>
        <th className="blind-region-filter"><button className={`blind-header-button ${filters.region || filters.type ? 'filtered' : ''}`} aria-label="区域与类型筛选" aria-expanded={showRegion} onClick={() => setShowRegion(!showRegion)}>区域 / 类型 ▾{(filters.region || filters.type) && <i />}</button>
          {showRegion && <div className="blind-filter-popover"><label>区域<select aria-label="区域筛选" value={filters.region} onChange={e => setFilter('region', e.target.value)}><option value="">全部区域</option>{options('region').map(v => <option key={v}>{v}</option>)}</select></label><label>类型<select aria-label="类型筛选" value={filters.type} onChange={e => setFilter('type', e.target.value)}><option value="">全部类型</option>{options('type').map(v => <option key={v}>{v}</option>)}</select></label><div><button onClick={() => { setFilter('region', ''); setFilter('type', '') }}>清空</button><button onClick={() => setShowRegion(false)}>完成</button></div></div>}
        </th>
        <th><button className="blind-header-button" onClick={() => setSort(sort === 'desc' ? 'asc' : 'desc')}>报名截止 / 开标 {sort === 'desc' ? '↓' : '↑'}</button></th>
        <th>招标金额 / 限价</th>
        <th>参与状态</th>
        {hasResultColumn && <th><select className={filters.result ? 'filtered' : ''} aria-label="中标结果筛选" value={filters.result} onChange={e => setFilter('result', e.target.value)}><option value="">中标结果</option>{['中标', '未中标', '待公布', '待补标注'].map(v => <option key={v}>{v}</option>)}</select></th>}
        <th><select className={filters.sync ? 'filtered' : ''} aria-label="同步状态筛选" value={filters.sync} onChange={e => setFilter('sync', e.target.value)}><option value="">同步</option>{['已同步', '待写入', '需处理'].map(v => <option key={v}>{v}</option>)}</select></th>
      </tr></thead><tbody>{pageRows.map(r => <tr key={r.id} data-pid={r.id}>
        <td><button className="blind-project" title={r.name} onClick={() => openRow(r)}>{r.name}</button><small>{r.month}{r.row ? ` · 第${r.row}行` : ''}</small></td>
        <td><div className="blind-cell-client" title={r.client}>{displayValue(r.client)}</div>{r.agency && r.agency !== '/' && <small title={r.agency}>代理：{r.agency}</small>}</td>
        <td>{displayValue(r.region)}<small>{displayValue(r.type)}</small></td><td>{displayValue(dateText(r.deadline || r.date || ''))}{!r.deadline && r.date && <small>开标</small>}</td>
        <td className="blind-tender-amount">{displayValue(r.limit || tenderAmount(r.fields))}</td>
        <td><Badge value={r.status} />{r.status === '报名未参与' && !(r.fields['未参与原因'] || r.fields['BidOS未参与原因'] || r.fields['备注']) && <small className="blind-attention">请补未参与原因</small>}</td>
        {hasResultColumn && <td>{r.ownMarkMissing || r.result ? <Badge value={r.ownMarkMissing ? '待补标注' : r.result} /> : '/'}{['报名参与', '报名未参与'].includes(r.status) && r.winner && <small className="blind-cell-winner" title={r.winner}>{r.winner}</small>}</td>}
        <td><Badge value={r.sync} /></td>
      </tr>)}</tbody></table>{filtered.length === 0 && <div className="blind-empty">没有符合筛选条件的项目</div>}</div>
      <div className="blind-pagination"><span>共 {filtered.length} 条 · 每页30条</span><button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button><span>{Math.min(page, pageCount)} / {pageCount}</span><button disabled={page >= pageCount} onClick={() => setPage(p => p + 1)}>下一页</button></div>
    </>}
    {selectedRow && <div className="blind-drawer-backdrop" onClick={closeDrawer}><aside className="blind-drawer" aria-label="项目详情" role="dialog" aria-modal="true" onClick={e => e.stopPropagation()}>
      <div className="blind-drawer-head"><div>{editing ? <textarea className="blind-title-editor" aria-label="项目名称" value={draft['项目名称'] || ''} onChange={e => updateDraft(['项目名称'], e.target.value)} rows={2} /> : <h2>{selectedRow.name}</h2>}<p>{selectedCandidate ? `${selectedCandidate.channel} · 发布于 ${dateText(selectedCandidate.publishedAt)}` : `${selectedRow.month} · 原表第${selectedRow.row}行`}</p></div><button aria-label="关闭项目详情" onClick={closeDrawer}>×</button></div>
      <div className="blind-drawer-actions">
        {editing && participationKeys.length ? <select aria-label="参与状态" value={STATUS.includes(selectedStatus) ? selectedStatus : selectedRow.status} onChange={e => updateDraft(participationKeys, e.target.value)}>{STATUS.map(v => <option key={v}>{v}</option>)}</select> : <Badge value={selectedRow.status} />}
        <Badge value={selectedRow.sync} />
        {selectedCandidate ? <button className="btn" disabled={Boolean(busy)} onClick={() => promote(selectedCandidate)}>{busy === selectedCandidate.id ? '写入中…' : '进入待评估'}</button> : editing ? <><button className="btn" disabled={Boolean(busy)} onClick={save}>{busy ? '保存中…' : '保存到 Excel'}</button><button className="btn ghost" disabled={Boolean(busy)} onClick={() => setEditing(false)}>取消</button></> : <button className="btn" onClick={() => startEditing(selectedRow)}>编辑项目</button>}
        {!editing && sourceLinks.map((k, i) => <a key={k} href={noticeUrl(selectedRow.fields[k])} target="_blank" onClick={rememberView} rel="noopener noreferrer" title="在新标签页打开原公告，本页保持当前项目">{sourceLinks.length === 1 || i === 0 ? '招标原文' : FIELD_LABELS[k] || k} ↗</a>)}
      </div>
      {drawerErrors.map(v => <div className="blind-message error" role="alert" key={v}>{v}</div>)}
      {selectedCandidate?.detailMessage && <div className="blind-message blind-attention">{selectedCandidate.detailMessage}</div>}
      {hasResult && <section className="blind-result">
        <div className="blind-result-heading"><h3>中标结果</h3>{!editing && <Badge value={selectedRow.ownMarkMissing ? '待补标注' : selectedRow.result || (selectedRow.published ? '已公布' : '待公布')} />}{!editing && !selectedRow.published && <span>自动跟进中</span>}{editing && 'BidOS结果公布' in selectedRow.fields && <label>公布状态 <select aria-label="公布状态" value={draft['BidOS结果公布'] || ''} onChange={e => updateDraft(['BidOS结果公布'], e.target.value)}><option value=""></option><option>待公布</option><option>已公布</option></select></label>}{!editing && noticeUrl(selectedRow.resultUrl) && <a href={noticeUrl(selectedRow.resultUrl)} target="_blank" onClick={rememberView} rel="noopener noreferrer" title="在新标签页打开结果公告，本页保持当前项目">结果公告 ↗</a>}</div>
        {selectedRow.ownMarkMissing && <p className="blind-attention">请补项目汇总表的“是否中标”标注。</p>}
        {selectedRow.ledgerConflict && <p className="blind-attention">项目汇总表有多条同名记录，请核对。</p>}
        {selectedRow.sourceWarnings?.map(w => <p className="blind-attention" key={w}>{w}</p>)}
        {(editing || (selectedRow.published && !['流标', '废标', '重新招采'].includes(selectedRow.ownMark)) || displayValue(selectedRow.winner) !== '/' || displayValue(selectedRow.amount) !== '/') && <div className="blind-fields blind-result-fields">
          <FieldBlock label="中标单位" keys={WINNER_KEYS.filter(k => k in selectedRow.fields)} fields={selectedRow.fields} draft={draft} editing={editing} onChange={updateDraft} fallback={selectedRow.winner} notes={selectedRow.cellNotes} />
          <FieldBlock label="中标金额" keys={AMOUNT_KEYS.filter(k => k in selectedRow.fields)} fields={selectedRow.fields} draft={draft} editing={editing && !AMOUNT_KEYS.some(key => selectedRow.readOnlyFields?.includes(key))} onChange={(keys, value) => {
            // 万元列与公告金额原文使用各自单位，避免把原文直接写入数值列。
            if (keys.length === 1) updateDraft(keys, value)
            else {
              const main = keys.find(k => Boolean(selectedRow.fields[k])) || keys[0]
              const raw = value.trim()
              let source = raw, original = raw
              if (main === '中标金额（万元）' && /^[+-]?\d+(?:\.\d+)?$/.test(raw)) original = `${raw} 万元`
              if (main === 'BidOS中标金额原文') {
                const money = raw.match(/^([+-]?\d+(?:\.\d+)?)\s*(万元|元)$/)
                if (money) source = money[2] === '万元' ? money[1] : String(Number(money[1]) / 10000)
              }
              setDraft(prev => ({ ...prev, ...Object.fromEntries(keys.map(k => [k, k === '中标金额（万元）' ? source : original])) }))
            }
          }} fallback={selectedRow.amount} notes={selectedRow.cellNotes} />
          {(discountKeys.length > 0 || selectedDiscount) && <FieldBlock label="折扣率" keys={discountKeys.length ? discountKeys : ['折扣率']} fields={discountFields} draft={draft} editing={false} onChange={updateDraft} fallback={selectedDiscount} mirrorAliases={false} notes={selectedRow.cellNotes} />}
          {!editing && selectedRow.ledger['_row'] && '是否中标' in selectedRow.ledger && <FieldBlock label="是否中标（项目汇总表）" keys={['是否中标']} fields={selectedRow.ledger} draft={{}} editing={false} onChange={updateDraft} keepEmpty={false} notes={selectedRow.cellNotes} />}
          {editing && selectedStatus === '报名参与' && selectedRow.ledger['_row'] && <label className="blind-field"><span className="blind-field-label">汇总表标注</span><select aria-label="汇总表是否中标" value={draftOwnMark} onChange={e => setDraftOwnMark(e.target.value)}>{['', '是', '否', '入库', '暂未公示', '流标', '废标', '重新招采'].map(v => <option key={v} value={v}>{({ 是: '是 · 中标', 否: '否 · 未中标', 入库: '入库 · 计中标', 暂未公示: '待公布' } as Record<string, string>)[v] || v}</option>)}</select></label>}
        </div>}
      </section>}
      {(selectedCandidate ? detailGroups.candidates : detailGroups.records).map(section => {
        const groups = section.fields.filter(group => {
          if (group.label === '打印费（元）' && !isMoneyAmount(detailFields['打印费（元）'])) return false
          if (group.label === '备注' && selectedStatus === '报名未参与' && detailFields['备注'] === detailFields['未参与原因']) return false
          return !('onlyNonParticipant' in group) || selectedStatus === '报名未参与' || editing || group.keys.some(key => displayValue(detailFields[key]) !== '/')
        })
        const entries = groups.map(group => {
          const keys = [...group.keys].filter(key => key in detailFields && (group.label !== '项目类型' || !['企业', '机关', '酒店', '事业单位', '事业', '政府'].includes(detailFields[key])))
          const isRemarks = group.label === '备注' && !['未报名', '报名未参与'].includes(selectedStatus)
          const rawFallback = 'fallback' in group ? String(selectedRow[group.fallback] || '') : ''
          const fallback = /时间/.test(group.label) ? dateTimeText(rawFallback) : rawFallback
          // 读态按「本条记录自身」是否填写分组：原表没填的行不再逐行占位，避免一整片「/」。
          const filled = keys.some(key => displayValue(editing ? selectedRow.fields[key] : detailFields[key]) !== '/') || displayValue(fallback) !== '/'
          return { key: group.label, filled, node: <FieldBlock key={group.label} label={group.label} keys={keys} fields={editing ? selectedRow.fields : detailFields} draft={draft} editing={editing && !keys.some(key => selectedRow.readOnlyFields?.includes(key))} onChange={updateDraft} fallback={fallback} keepEmpty={!['备注', '跟进人'].includes(group.label)} mirrorAliases={!isRemarks && group.label !== '招标金额'} notes={selectedRow.cellNotes} /> }
        })
        const legacyValue = section.title === '跟进记录' ? Array.from(new Set(participationKeys.map(k => selectedRow.fields[k]).filter(v => v && !STATUS.includes(v)))).join('\n') : ''
        const legacy = section.title === '跟进记录' && (editing || displayValue(legacyValue) !== '/') ? <FieldBlock label="报名参与情况（原记录）" keys={[]} fields={{}} draft={{}} editing={false} onChange={updateDraft} keepEmpty={false} fallback={legacyValue} /> : null
        const shown = editing ? entries : entries.filter(x => x.filled)
        const hidden = editing ? [] : entries.filter(x => !x.filled)
        // 整段无内容：折成一行，点开仍是完整字段清单（不丢信息，编辑态本来就全量展开）。
        if (!editing && !shown.length && !legacy) return <details key={section.title} className="blind-detail-section blind-detail-folded">
          <summary>{section.title}<em>全 {entries.length} 项未填写</em></summary>
          <div className="blind-fields">{entries.map(x => x.node)}</div>
        </details>
        return <section key={section.title} className="blind-detail-section"><h3>{section.title}</h3><div className="blind-fields">
          {shown.map(x => x.node)}
          {legacy}
        </div>
        {hidden.length > 0 && <details className="blind-detail-rest"><summary>另 {hidden.length} 项未填写</summary><div className="blind-fields">{hidden.map(x => x.node)}</div></details>}
        </section>
      })}
      {editing && <><h3>公告链接</h3><div className="blind-fields">
        <FieldBlock label="招标公告" keys={sourceKeys.filter(k => /招标公告网址|公告网址|原文链接/.test(k))} fields={selectedRow.fields} draft={draft} editing={editing} onChange={updateDraft} onNavigate={rememberView} />
        {hasResult && <FieldBlock label="结果公告" keys={['BidOS结果公告网址'].filter(k => k in selectedRow.fields)} fields={selectedRow.fields} draft={draft} editing={editing} onChange={updateDraft} fallback={selectedRow.resultUrl} keepEmpty={false} onNavigate={rememberView} />}
      </div></>}
      {!editing && <RelatedWorkHistory projectId={selectedRow.id} sourceKeys={selectedRow.sources || (selectedRow.source ? [selectedRow.source] : [])} />}
      {!editing && <SourceRows rows={selectedRow.sourceRows} />}
      {!editing && selectedRow.sources?.map(source => <SourceLocation key={`${source.file}/${source.sheet}/${source.row}`} source={source} />)}
    </aside></div>}
  </section>
}
