import { useCallback, useEffect, useState } from 'react'

export type SheetLocation = { file: string; sheet: string; row: number | string; fingerprint?: string; section?: string }
export type SourceDataRow = { source: SheetLocation; fields: Record<string, string>; comments?: Record<string, string>; matchNote?: string }
export type PortfolioResult = {
  id: string; name: string; client: string; date: string; publishedAt: string; year: string
  channel: string; business: string; company: string; agency: string; participants: string
  result: string; ownMark: string; winner: string; amount: string; limit: string
  source: SheetLocation; url: string; resultUrl: string; notes: Record<string, string>; fields: Record<string, string>; cellNotes?: Record<string, string>
  sourceFields?: Record<string, string>; relatedFields?: Record<string, string>; priceFields?: Record<string, string>; priceSource?: SheetLocation; sources?: SheetLocation[]; auxiliaryFields?: Record<string, string>
  auxiliarySourceRows?: SourceDataRow[]; sourceRows?: SourceDataRow[]
}
export type PortfolioBond = {
  id: string; kind: string; name: string; client: string; date: string; paidAt: string
  amount: number | null; amountText: string; amountRaw: string; status: string; statusRaw: string
  publishedAt: string; dueAt: string; refundedAt: string; owner: string; company: string; note: string
  dueBasis: string; overdueDays: number; sources: SheetLocation[]; resultStatus?: string; projectId?: string
  sourceFields?: Record<string, string>; cellNotes?: Record<string, string>; sourceRows?: SourceDataRow[]
}
export type CompetitorProject = {
  id: string; name: string; client: string; date: string; dateRaw: string; year: string; region: string; business: string
  result: string; winner: string; amount: string; duration: string; endAt: string; endEstimated: boolean
  daysLeft: number | null; expiring: boolean; url: string; source: SheetLocation; ours: PortfolioResult | null
  fields: Record<string, string>; notes: Record<string, string>; sources?: SheetLocation[]
  sourceFields?: Record<string, string>; cellNotes?: Record<string, string>; sourceRows?: SourceDataRow[]
}
export type FocusCompetitor = {
  id: string; name: string; projects: CompetitorProject[]; profile: Record<string, unknown>
  regions: Record<string, number>; clients: Record<string, number>; businesses: Record<string, number>
}
export type PortfolioReminder = { id: string; kind: string; title: string; detail: string; severity: 'critical' | 'warning'; at: string; path: string }
export type Portfolio = {
  updatedAt: string; snapshot?: {generatedAt:string; intervalMinutes:number}; sources: string[]; results: PortfolioResult[]; bonds: PortfolioBond[]
  competitors: FocusCompetitor[]; reminders: PortfolioReminder[]; excludedNonParticipants: number; notes: string[]
  reconciliation?: { unlinkedAuxiliary?: SourceDataRow[] }
  collection: { lastRun?: string; status?: string; usable?: number; failed?: number; total?: number }
  resultPolicy: string; channelPolicy: string
  publicSync?: { lastRun?: string; status?: string; added?: number; parsed?: number; scope?: string; error?: string; enabled?: boolean; nextRun?: string }
}
async function request<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `读取失败 (${r.status})`) }
  return r.json()
}
export const getPortfolio = (force = false) => request<Portfolio>(`/api/portfolio/workspace${force ? '?refresh=true' : ''}`)
export const refundBond = (b: PortfolioBond, refundedAt: string, note: string) => request<{ message: string }>(`/api/portfolio/bonds/${b.id}/refund`, { refundedAt, note, fingerprints: b.sources.map(p => p.fingerprint) })
export const openPortfolioSource = (source: SheetLocation) => request<{ ok: boolean }>('/api/portfolio/source/open', source)
export function usePortfolio() {
  const [data, setData] = useState<Portfolio | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const refresh = useCallback(async (force = true) => {
    try { const d = await getPortfolio(force); setData(d); setError(''); return d }
    catch (e) { setError(e instanceof Error ? e.message : '读取失败'); return null }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void refresh(false); const timer = window.setInterval(() => void refresh(false), 60000); return () => clearInterval(timer) }, [refresh])
  return { data, error, loading, refresh }
}
export const localToday = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date())
export const fiscalYear = (date = localToday()) => String(Number(date.slice(0, 4)) + (date.slice(5, 7) === '12' ? 1 : 0))
export const formatMoney = (n: number | null) => n === null ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '元'
