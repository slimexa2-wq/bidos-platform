export type Severity = 'critical' | 'warning' | 'info' | 'success' | 'neutral'
export type WorkflowStage = '盲投信息' | '报名管理' | '摘要评估' | '标书制作' | '结果复盘'

export interface SourcePointer {
  id: string
  type: 'official' | 'sheet' | 'file' | 'third_party' | 'internal'
  label: string
  url?: string
  deepLink?: string
  location?: string
  capturedAt?: string
  trust: 'official' | 'internal' | 'reference'
  projectId?: string
  entityId?: string
  fileId?: string
  fileName?: string
  sheetName?: string
  rowId?: string
  cellRange?: string
  pageNumber?: number
  section?: string
  blockId?: string
  recordId?: string
  contentHash?: string
  originalText?: string
  originalUrl?: string
}

export interface Opportunity {
  month?: string
  /** 跨表补全的结论性数据（2026年未参与子表/汇总表匹配） */
  enriched?: {
    won?: string
    winner?: string
    amount?: string
    competitorWinner?: string
    competitorPrice?: string
    reason?: string
  }
  /** 源表逐字字段（表头与共享盘源表一致） */
  sourceFields?: Record<string, string>
  id: string
  name: string
  client: string
  region: string
  /**
   * 招标金额，单位 **元**（不是万元）。
   * 来源：`projects.budget`（numeric，元）。展示请用 `utils/format.ts`
   * 的 `formatBudgetWan()`，不要在页面里手写「万」。
   */
  budget: number
  deadline: string
  stage: WorkflowStage
  status: string
  severity?: Severity
  projectType: string
  sourceName: string
  summary: string
  followed: boolean
  verified: boolean
  sources: SourcePointer[]
}

export interface AlertItem {
  id: string
  title: string
  projectId?: string
  detail: string
  timeLabel: string
  severity: Severity
  actionLabel: string
  /** 已读。注意：已读 != 已解决，handled 为 true 的事件仍然会出现。 */
  handled: boolean
  /** 业务上是否已解决。只有 true 才不再出现在首页/提醒中心。 */
  resolved?: boolean
  /** 事件类型，用于决定可选的解决方式（PROJECT_EXPIRING / DEPOSIT_OVERDUE 等）。 */
  type?: string
  /**
   * 是否属于投标保证金退回类事件（含"应退日期待补录"的人工复核事件）。
   * 后端 `_event_to_alert` 尚未返回该字段时，前端回退到 resolutionKey / type / 标题判断。
   */
  isDeposit?: boolean
  /** 事件幂等键。后端以 `deposit_refund` 前缀判定保证金类事件，前端可据此对齐口径。 */
  resolutionKey?: string
  /** 来源详情（SourcePointer）。后端解析成功才有；解析不到会明确 sourcesResolved=false，绝不伪造。 */
  sources?: SourcePointer[]
  /** sources 是否已完整解析出全部 sourceIds。false 表示部分证据详情不可用，必须显式告知。 */
  sourcesResolved?: boolean
  persistent?: boolean
  resolutionHint?: string
  sourceIds: string[]
}

export interface RegistrationMaterial {
  id: string
  label: string
  status: 'ready' | 'missing' | 'confirm' | 'unpaid'
  source?: string
}

export interface RegistrationCase {
  projectId: string
  progress: number
  deadline: string
  status: string
  materials: RegistrationMaterial[]
}

export interface EvaluationEvidence {
  id: string
  type: 'support' | 'caution' | 'unknown'
  text: string
  sourceLabel: string
}

export interface EvaluationCase {
  projectId: string
  score: number
  aiView: string
  evidence: EvaluationEvidence[]
  humanDecision: '建议参与' | '建议不参与' | '待讨论'
  humanNote: string
  similarProjects: Array<{ name: string; year: string; result: string; similarity: number }>
}

export interface DocumentSection {
  id: string
  title: string
  progress: number
  status: 'done' | 'working' | 'todo' | 'issue'
  content: string
}

export interface BidDocument {
  projectId: string
  progress: number
  dueAt: string
  sections: DocumentSection[]
  issues: Array<{ id: string; severity: Severity; text: string; location: string }>
}

export interface ResultReview {
  projectId: string
  result: '中标' | '未中标' | '放弃'
  resultDate: string
  projectEndDate?: string
  projectEndDateKind?: 'exact' | 'estimated' | 'manual'
  expiryResolved?: boolean
  winner: string
  winningPrice: number | null
  ourPrice: number | null
  reasons: string[]
  lessons: string[]
  reviewed: boolean
}

export interface BidBondRecord {
  projectSources?: SourcePointer[]
  id: string
  projectId: string
  projectName: string
  /** 甲方名称：源表《投标项目汇总》保证金 sheet 表头，来自 projects.client_name */
  clientName?: string
  /** 开标时间：源表表头，来自 projects.deadline（YYYY-MM-DD），缺失为 null */
  openBidAt?: string | null
  bidSection?: string
  amount: number
  paidAt?: string
  resultDate?: string
  expectedRefundAt?: string
  actualRefundAt?: string
  status: '未缴纳' | '已缴纳' | '待退回' | '逾期未退' | '已退回' | '无需退回'
  owner: string
  overdueDays?: number
  sourceLabel?: string
  note?: string
  /**
   * 到账确认记录。交接文档 §2.8 要求"实际到账确认必须记录操作人、时间、凭证来源"且可查。
   * 后端 /api/bid-bonds 目前尚未下发这四个字段；前端有则展示，无则明确说明"未记录"。
   */
  refundSourcePointerId?: string
  refundSourceLabel?: string
  refundConfirmedBy?: string
  refundConfirmedAt?: string
}

export interface Competitor {
  id: string
  name: string
  regions: string[]
  projectCount: number
  wonCount: number
  /**
   * 平均报价比。**源数据缺失时为 null**（真库 our_price 全为 NULL），
   * 前端必须显示「—」，不要用 0 冒充（曾渲染出假的「平均报价 0.0%」）。
   */
  avgQuoteRatio: number | null
  /** 活跃度。真库 result_date 全为 NULL 时后端给空字符串，前端显示「暂无」。 */
  activity: string
  specialties: string[]
  expiringProjects: number
  /** 近月中标次数（纯数字数组，供 <Spark> 使用）。 */
  recentTrend: number[]
}

export interface SearchResult {
  id: string
  type: 'project' | 'client' | 'competitor' | 'file' | 'announcement' | 'sheet' | 'chunk'
  title: string
  snippet: string
  source: SourcePointer
  score: number
}

export interface DashboardSummary {
  stages: Array<{ name: WorkflowStage; primary: number; primaryLabel: string; secondary: number; secondaryLabel: string; path: string }>
  keyTimeline: Array<{ id: string; at: string; label: string; projectId?: string; severity: Severity; action: string; path?: string; persistent?: boolean }>
}

export interface SourceHealth {
  source_id: string
  source_name: string
  strategy: string
  last_success_at?: string
  last_checked_at?: string
  consecutive_failures: number
  items_found: number
  requires_login: boolean
  human_action_required: boolean
  last_error?: string
}

export interface DecisionEvidence {
  id: string
  stance: 'support' | 'caution' | 'unknown'
  statement: string
  source_pointer_ids: string[]
  confidence: number
}

export interface DecisionSupport {
  project_id: string
  question: string
  summary: string
  support: DecisionEvidence[]
  caution: DecisionEvidence[]
  unknown: DecisionEvidence[]
  human_decision_required: boolean
  generated_at: string
}

export interface FeedbackPayload {
  object_type: string
  object_id: string
  ai_output: string
  corrected_output: string
  reason: string
  evidence_source_ids: string[]
  user_id?: string
}

// ---------------------------------------------------------------------------
// 事件 / 健康 / 运行时配置
//
// 后端铁律（交接文档 §3.1 / §12.5）：已读 != 已解决。
// 因此事件有两个独立动作：handleAlert（只标记已读，事件仍在）与
// resolveAlert（业务解决，事件消失）。两者绝不可在前端混为一个"处理"。
// ---------------------------------------------------------------------------

/** 首页关键时间轴条目。由未解决事件派生，已解决事件不出现。 */
export interface TimelineEvent {
  id: string
  at: string
  label: string
  projectId?: string
  severity: Severity
  action: string
  path?: string
  persistent?: boolean
  handled?: boolean
  type?: string
  resolutionHint?: string
  sourceIds?: string[]
}

export interface DegradedSubsystem {
  subsystem: string
  reason: string
}

export interface RepositoryStatus {
  mode: string
  available: boolean
  reason?: string | null
}

export interface SystemHealth {
  status: string
  version: string
  /** 真实数据来源，如实反映：postgres 挂了就是 mock，绝不谎报。 */
  data_mode: string
  evidence_mode: boolean
  subsystems: {
    repository: RepositoryStatus
    event_engine: {
      available: boolean
      store?: string | null
      persistent?: boolean
      rejected_events?: number
    }
    hybrid_search: { available: boolean; mode: string }
    official_sources: { available: boolean }
    /** 非空时必须在前端显式提示，不能假装一切正常。 */
    degraded: DegradedSubsystem[]
  }
}

export interface RuntimeConfig {
  env: string
  configured_data_mode: string
  effective_data_mode: string
  database_url_set: boolean
  degraded: DegradedSubsystem[]
}

export interface SearchCapability {
  mode: string
  available: boolean
  /** true 表示向量/reranker 不可用，当前结果不是纯语义结果，必须如实说明。 */
  degraded: boolean
}

/**
 * 事件解决方式。'handled' / 'manual_closed' 为既有方式；
 * 'converted_to_performance_bond'（转履约保证金）与 'written_off'（没收 / 核销）为
 * 保证金事件的**例外关闭**方式（§2.9）：均属例外操作，必须附财务凭证 Source Pointer，
 * 后端 event-agent 实现完成后 `POST /api/alerts/{id}/resolve` 即可直达。
 */
export type AlertResolution =
  | 'handled'
  | 'manual_closed'
  | 'converted_to_performance_bond'
  | 'written_off'

/** 保证金事件可用的例外关闭方式。 */
export type DepositExceptionResolution = Extract<AlertResolution, 'converted_to_performance_bond' | 'written_off'>

export interface EventActionResponse {
  status: string
  id?: string
  resolution?: string
  note?: string
  [key: string]: unknown
}

export interface ScanResult {
  created: number | null
  updated: number | null
  events: number
}

export interface HumanDecisionRecord {
  id: string
  decision: '参与' | '不参与' | '待讨论'
  note: string
  userId: string
  createdAt: string
}
