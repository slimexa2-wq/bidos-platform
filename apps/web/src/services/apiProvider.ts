import type {
  CandidateActionResponse,
  DataProvider,
  FieldEdit,
  FieldEditResponse,
  OwnResultRecord,
} from './dataProvider'
import type { AlertItem, FeedbackPayload, SystemHealth, TimelineEvent } from '../types/domain'

const base = import.meta.env.VITE_API_BASE || '/api'
// /health 挂在根路径，不在 /api 前缀下；这里从 base 反推根地址。
const root = base.replace(/\/api\/?$/, '')

const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const res = await fetch(`${base}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`)
  return res.json() as Promise<T>
}

/** 仅对 404 返回 null（用于"端点尚未挂载则回退"的场景），其余错误照常抛出。 */
const requestOptional = async <T,>(path: string, init?: RequestInit): Promise<T | null> => {
  const res = await fetch(`${base}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`)
  return res.json() as Promise<T>
}

const query = (params: Record<string, string | number | boolean | undefined | null>): string => {
  const sp = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    sp.set(key, String(value))
  }
  const text = sp.toString()
  return text ? `?${text}` : ''
}

/** 事件类型 → 该去哪个页面处理。 */
const eventPath = (type?: string): string => {
  switch (type) {
    case 'PROJECT_EXPIRING':
      return '/results'
    case 'DEPOSIT_OVERDUE':
    case 'DEPOSIT_REFUND_DUE':
      return '/bid-bonds'
    default:
      return '/alerts'
  }
}

const alertToTimeline = (alert: AlertItem): TimelineEvent => ({
  id: alert.id,
  at: alert.timeLabel,
  label: alert.title,
  projectId: alert.projectId,
  severity: alert.severity,
  action: alert.actionLabel,
  path: eventPath(alert.type),
  persistent: alert.persistent,
  handled: alert.handled,
  type: alert.type,
  resolutionHint: alert.resolutionHint,
  sourceIds: alert.sourceIds,
})

export const apiProvider: DataProvider = {
  getDashboard: () => request('/dashboard'),

  getEventTimeline: async (limit = 20) => {
    // 注意：/api/events/timeline 与 /api/alerts 返回的都是 AlertItem 形状
    // （_event_to_alert），不是 TimelineEvent。这里统一映射，避免首页读到空字段。
    // timeline 只出未解决的高价值事件（已读的仍在）；未挂载时回退 /alerts。
    const direct = await requestOptional<AlertItem[]>(`/events/timeline${query({ limit })}`)
    const alerts = direct ?? (await request<AlertItem[]>('/alerts'))
    return alerts.slice(0, limit).map(alertToTimeline)
  },

  getHealth: async () => {
    // 优先 /api/health（与业务同前缀，反向代理只放行 /api/ 时也可达），
    // 回退根路径 /health（nginx.conf 有显式 location /health）。
    const direct = await requestOptional<SystemHealth>('/health')
    if (direct) return direct
    const res = await fetch(`${root}/health`, { headers: { 'Content-Type': 'application/json' } })
    if (!res.ok) throw new Error(`API ${res.status}: /health`)
    return (await res.json()) as SystemHealth
  },

  getConfig: () => request('/config'),
  getOpportunities: (scope?: 'active' | 'all' | 'blind' | 'candidates') => request(`/opportunities${query({ scope })}`),

  // 全字段编辑 / 新信息队列（王明李契约）：前端只调 API 打标记，
  // 共享盘回写由后端回写引擎消费 system_edits / candidate_promotion 完成。
  applyFieldEdits: (projectId, edits: FieldEdit[]) =>
    request<FieldEditResponse>(`/projects/${encodeURIComponent(projectId)}/fields`, {
      method: 'POST',
      body: JSON.stringify({ edits }),
    }),

  promoteCandidate: (projectId) =>
    request<CandidateActionResponse>(`/candidates/${encodeURIComponent(projectId)}/promote`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  dismissCandidate: (projectId, reason = '') =>
    request<CandidateActionResponse>(`/candidates/${encodeURIComponent(projectId)}/dismiss`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  getOwnResults: async () => {
    const payload = await request<{ total: number; results: OwnResultRecord[] }>('/results/own')
    return payload.results
  },
  getAlerts: (group?: string) => request(`/alerts${query({ group })}`),

  getAlertGroupCounts: () => request<Record<string, number>>('/alerts/group-counts'),

  handleAlert: (eventId: string) => request(`/alerts/${encodeURIComponent(eventId)}/handle`, { method: 'POST' }),

  resolveAlert: (eventId, resolution, note, actorId = 'demo-user', sourcePointerId) =>
    request(
      `/alerts/${encodeURIComponent(eventId)}/resolve${query({
        resolution,
        actor_id: actorId,
        note,
        source_pointer_id: sourcePointerId,
      })}`,
      { method: 'POST' },
    ),

  scanEvents: () => request('/events/scan', { method: 'POST' }),

  resolveProjectExpiry: (projectId, resolution, options = {}) =>
    request(
      `/projects/${encodeURIComponent(projectId)}/expiry/resolve${query({
        resolution,
        actor_id: options.actorId ?? 'demo-user',
        note: options.note,
        source_pointer_id: options.sourcePointerId,
        confidence: options.confidence,
      })}`,
      { method: 'POST' },
    ),

  getRegistrations: () => request('/registrations'),
  getEvaluations: (scope?: 'active' | 'all') => request(`/evaluations${query({ scope })}`),
  getBidDocuments: () => request('/documents'),
  getResults: (start?: string, end?: string) => request(`/results${query({ start, end })}`),
  getBidBonds: () => request('/bid-bonds'),

  confirmBondRefund: (ledgerId, refundSourcePointerId, actorId = 'demo-user') =>
    request(
      `/bid-bonds/${encodeURIComponent(ledgerId)}/refund/confirm${query({
        actor_id: actorId,
        refund_source_pointer_id: refundSourcePointerId,
      })}`,
      { method: 'POST' },
    ),

  getHumanDecisions: (projectId) =>
    request(`/evaluations/${encodeURIComponent(projectId)}/human-decision`),
  getCompetitors: () => request('/competitors'),
  getCompetitorOverlap: (name: string) =>
    request(`/competitors/${encodeURIComponent(name)}/overlap`),
  getKeyProjects: () => request('/key-projects'),
  scanKeyProjects: () => request('/key-projects/scan', { method: 'POST' }),
  getAuditLogs: (limit = 50) => request(`/audit${query({ limit })}`),
  getFeedbacks: (limit = 50) => request(`/feedback${query({ limit })}`),
  search: (q: string, limit = 20) => request(`/search${query({ q, limit })}`),
  getSearchCapability: () => request('/search/capability'),
  getDecisionSupport: (projectId, question) =>
    request(`/projects/${encodeURIComponent(projectId)}/decision-support${query({ question })}`),
  getSourceHealth: () => request('/sources/health'),
  getUnhealthySources: () => request('/sources/unhealthy'),
  submitFeedback: (payload: FeedbackPayload) => request('/feedback', { method: 'POST', body: JSON.stringify(payload) }),
  saveHumanDecision: (projectId, decision, note, sourcePointerIds: string[] = []) =>
    request(`/evaluations/${encodeURIComponent(projectId)}/human-decision`, {
      method: 'POST',
      // 证据指针此前硬编码为 []，导致人工结论无法回链到依据（交接文档 §五要求可追溯）。
      body: JSON.stringify({
        decision,
        note,
        user_id: 'demo-user',
        source_pointer_ids: sourcePointerIds,
      }),
    }),
}
