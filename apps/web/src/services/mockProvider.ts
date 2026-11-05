import { alerts, bidBonds, bidDocuments, competitors, dashboard, evaluations, opportunities, registrations, results, searchResults, sourcePointers } from '../data/mock'
import type { DataProvider, FieldEdit, OwnResultRecord } from './dataProvider'
import type {
  AlertItem,
  BidBondRecord,
  DecisionSupport,
  EventActionResponse,
  FeedbackPayload,
  SourceHealth,
  TimelineEvent,
} from '../types/domain'

const delay = <T,>(value: T) => new Promise<T>(resolve => setTimeout(() => resolve(structuredClone(value)), 80))

// ---------------------------------------------------------------------------
// 可变 mock 状态
//
// 铁律（交接文档 §3.1 / §12.5）：已读 != 已解决。
// 这里用一份内存态刻意复刻后端语义：
//   handleAlert  → 只把 handled 置 true，事件**仍然**留在 openAlerts 里；
//   resolveAlert → 从 openAlerts 移除，事件才真正消失。
// ---------------------------------------------------------------------------

const inferType = (alert: AlertItem): string => {
  if (alert.type) return alert.type
  if (alert.title.includes('保证金')) return 'DEPOSIT_OVERDUE'
  if (alert.title.includes('到期') || alert.title.includes('90天')) return 'PROJECT_EXPIRING'
  return 'HUMAN_REVIEW_REQUIRED'
}

/** 未解决事件（含已读）。只有被业务解决后才从这里移除。 */
let openAlerts: AlertItem[] = alerts.map(a => {
  const type = inferType(a)
  // 模拟后端即将补上的 isDeposit 字段，让前端走与真实模式相同的判定路径。
  const isDeposit = type.startsWith('DEPOSIT') || a.title.includes('保证金')
  // 模拟后端 _event_to_alert 下发的证据详情，避免 mock 与真实模式在"证据是否可见"上分叉。
  const sources = sourcePointers.filter(s => a.sourceIds.includes(s.id))
  return {
    ...structuredClone(a),
    resolved: false,
    type,
    isDeposit,
    sources,
    sourcesResolved: sources.length === a.sourceIds.length,
  }
})
let bondLedger: BidBondRecord[] = structuredClone(bidBonds)

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

/**
 * 保证金类事件（含"应退日期待补录"的复核事件）。
 * 与后端 `event_store.is_deposit_event` 口径一致：这类事件不得以 manual_closed 结案。
 * 优先用后端 isDeposit 字段，回退到 type / 标题匹配。
 */
const isDepositAlert = (alert: AlertItem) =>
  alert.isDeposit ?? (!!alert.type?.startsWith('DEPOSIT') || alert.title.includes('保证金'))

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

const mockDecision = (projectId: string, question: string): DecisionSupport => ({
  project_id: projectId,
  question,
  summary: '已整理支持与谨慎两侧的事实依据；AI只提供参考，最终由人讨论决定。',
  support: [{ id: 's1', stance: 'support', statement: '历史相似项目存在成功案例。', source_pointer_ids: [], confidence: .82 }],
  caution: [{ id: 'c1', stance: 'caution', statement: '近期主要竞对在相似项目上的报价有所下探。', source_pointer_ids: [], confidence: .76 }],
  unknown: [{ id: 'u1', stance: 'unknown', statement: '主要竞对是否参与当前项目尚未确认。', source_pointer_ids: [], confidence: .42 }],
  human_decision_required: true,
  generated_at: new Date().toISOString(),
})

const sourceHealth: SourceHealth[] = [{
  source_id: 'mock_official', source_name: '示例政府采购网', strategy: 'http', consecutive_failures: 0, items_found: 18, requires_login: false, human_action_required: false, last_success_at: new Date().toISOString(), last_checked_at: new Date().toISOString(),
}]

const GROUP_PREFIXES: Record<string, string[]> = {
  opportunities: ['NEW_OPPORTUNITY'],
  key_projects: ['KEY_PROJECT'],
  deadlines: ['DEADLINE_APPROACHING', 'ANNOUNCEMENT_CHANGED'],
  expiry: ['PROJECT_EXPIRING'],
  bonds: ['DEPOSIT_'],
  data_review: ['HUMAN_REVIEW_REQUIRED'],
}

export const mockProvider: DataProvider = {
  getDashboard: () => delay(dashboard),

  getEventTimeline: (limit = 20) => delay(openAlerts.slice(0, limit).map(alertToTimeline)),

  getHealth: () => delay({
    status: 'ok',
    version: '0.3.1-mock',
    data_mode: 'mock',
    evidence_mode: true,
    subsystems: {
      repository: { mode: 'mock', available: true, reason: null },
      event_engine: { available: true, store: 'InMemoryEventStore', persistent: false, rejected_events: 0 },
      hybrid_search: { available: true, mode: 'in_memory' },
      official_sources: { available: false },
      degraded: [],
    },
  }),

  getConfig: () => delay({
    env: 'development',
    configured_data_mode: 'mock',
    effective_data_mode: 'mock',
    database_url_set: false,
    degraded: [],
  }),

  getOpportunities: () => delay(opportunities), // mock 数据即演示集，不按 scope 裁剪

  // 全字段编辑 / 新信息队列：mock 是内存演示集，编辑即时回写内存 opportunity，
  // 语义与真库「pending 编辑覆盖 sourceFields 渲染」对齐；候选队列演示集为空。
  applyFieldEdits: (projectId: string, edits: FieldEdit[]) => {
    const target = opportunities.find(o => o.id === projectId)
    if (!target) return Promise.reject(new Error(`mock 数据无该项目：${projectId}`))
    for (const e of edits) {
      target.sourceFields = { ...(target.sourceFields || {}), [e.field]: e.value }
    }
    return delay({
      status: 'saved',
      id: projectId,
      applied: edits.length,
      edits: edits.map((e, i) => ({
        id: `edt_mock${i}`,
        field: e.field,
        value: e.value,
        editor: '王明李',
        at: new Date().toISOString(),
        excel: { sheet: '9月', row: '', column: e.field, status: 'pending', written_at: null },
      })),
      excel: { sheet: '9月', row: '' },
    })
  },
  promoteCandidate: (projectId: string) => delay({ status: 'promoted', id: projectId }),
  dismissCandidate: (projectId: string, reason = '') => delay({ status: 'dismissed', id: projectId, reason }),
  getOwnResults: () => delay<OwnResultRecord[]>([]),

  getAlerts: (group?: string) => delay(
    group && group !== 'all'
      ? openAlerts.filter(a => (GROUP_PREFIXES[group] || []).some(p => (a.type || '').startsWith(p)))
      : openAlerts
  ),
  getAlertGroupCounts: async () => {
    const counts: Record<string, number> = { all: openAlerts.length }
    for (const g of Object.keys(GROUP_PREFIXES)) {
      if (g === 'all') continue
      counts[g] = openAlerts.filter(a => (GROUP_PREFIXES[g] || []).some(p => (a.type || '').startsWith(p))).length
    }
    return counts
  },

  handleAlert: (eventId: string) => {
    openAlerts = openAlerts.map(a => (a.id === eventId ? { ...a, handled: true } : a))
    return delay<EventActionResponse>({ status: 'handled', id: eventId, note: '已读不等于已解决，事件仍会在业务解决前持续出现' })
  },

  resolveAlert: (eventId: string, resolution: string, note = '', _actorId = 'demo-user', sourcePointerId?: string) => {
    const target = openAlerts.find(a => a.id === eventId)
    // 复刻后端红线（event_store.assert_manual_close_allowed）：保证金事件不得人工关闭，
    // 必须走到账确认（confirmBondRefund）。mock 若不拦，会掩盖真实模式下的失败。
    if (target && isDepositAlert(target) && resolution === 'manual_closed') {
      return Promise.reject(new Error('保证金事件不允许以「人工关闭」结案：必须到台账凭到账凭证确认'))
    }
    // 例外关闭（转履约保证金 / 没收核销）：与后端对齐——无财务凭证或无原因一律拒绝。
    if (target && isDepositAlert(target) && (resolution === 'converted_to_performance_bond' || resolution === 'written_off')) {
      if (!sourcePointerId?.trim()) {
        return Promise.reject(new Error('例外关闭属例外操作：必须提供财务凭证 Source Pointer（银行流水 / 财务到账记录 / 核销批复），缺凭证禁止关闭'))
      }
      if (!note.trim()) {
        return Promise.reject(new Error('例外关闭必须填写原因说明（会写入审计日志）'))
      }
    }
    openAlerts = openAlerts.filter(a => a.id !== eventId)
    void _actorId
    return delay<EventActionResponse>({ status: 'resolved', id: eventId, resolution })
  },

  scanEvents: () => delay({ created: 0, updated: 0, events: openAlerts.length }),

  resolveProjectExpiry: (projectId, resolution) => {
    // 项目级解决：把该项目下的到期持续事件一并关闭。
    openAlerts = openAlerts.filter(a => !(a.projectId === projectId && a.type === 'PROJECT_EXPIRING'))
    return delay<EventActionResponse>({ status: 'resolved', id: projectId, resolution })
  },

  getRegistrations: () => delay(registrations),
  getEvaluations: () => delay(evaluations),
  getBidDocuments: () => delay(bidDocuments),

  getResults: (start?: string, end?: string) => {
    if (!start && !end) return delay(results)
    // 与后端一致：没有结果日期的项目不做时间过滤，避免结果页静默少数据。
    const filtered = results.filter(item => {
      if (!item.resultDate) return true
      if (start && item.resultDate < start) return false
      if (end && item.resultDate > end) return false
      return true
    })
    return delay(filtered)
  },

  getBidBonds: () => delay(bondLedger),

  confirmBondRefund: (ledgerId: string, refundSourcePointerId: string, actorId = 'demo-user') => {
    if (!refundSourcePointerId) {
      return Promise.reject(new Error('到账凭证 Source Pointer 缺失，禁止仅凭口头确认关闭'))
    }
    const today = new Date().toISOString().slice(0, 10)
    const bond = bondLedger.find(b => b.id === ledgerId)
    bondLedger = bondLedger.map(b =>
      b.id === ledgerId
        ? {
            ...b,
            status: '已退回' as const,
            actualRefundAt: today,
            overdueDays: 0,
            // 模拟后端补齐的到账确认记录（§2.8：操作人 / 时间 / 凭证来源必须可查）
            refundSourcePointerId,
            refundSourceLabel: refundSourcePointerId,
            refundConfirmedBy: actorId,
            refundConfirmedAt: today,
          }
        : b,
    )
    // 到账确认 → 关闭该项目的保证金持续事件（§3.2：实际到账确认才结束提醒）。
    if (bond) openAlerts = openAlerts.filter(a => !(a.projectId === bond.projectId && isDepositAlert(a)))
    return delay<EventActionResponse>({ status: 'confirmed', id: ledgerId })
  },

  getCompetitors: () => delay(competitors),
  getCompetitorOverlap: (_name: string) => delay({ competitor: _name, notices: ['mock 模式无重合数据'], projects: [], summary: { total: 0 } }),
  getKeyProjects: () => delay({ total: 0, projects: [] }),
  scanKeyProjects: () => delay({ created: 0, updated: 0, skipped: 0 }),
  getAuditLogs: () => delay([]),
  getFeedbacks: () => delay([]),

  search: (query: string, limit = 20) => {
    const filtered = searchResults.filter(x => !query || `${x.title}${x.snippet}`.toLowerCase().includes(query.toLowerCase()))
    return delay((filtered.length ? filtered : searchResults).slice(0, limit))
  },

  getSearchCapability: () => delay({ mode: 'in_memory', available: true, degraded: false }),

  getDecisionSupport: (projectId, question) => delay(mockDecision(projectId, question)),
  getSourceHealth: () => delay(sourceHealth),
  getUnhealthySources: () => delay<SourceHealth[]>([]),
  submitFeedback: (_payload: FeedbackPayload) => delay({ id: `fb_${Date.now()}` }),
  saveHumanDecision: (_projectId, _decision, _note, _sourcePointerIds) => delay({ status: 'saved', audit_id: `audit_${Date.now()}` }),
  getHumanDecisions: () => delay([]),
}
