import type {
  AlertItem,
  BidBondRecord,
  BidDocument,
  Competitor,
  DashboardSummary,
  DecisionSupport,
  EvaluationCase,
  EventActionResponse,
  FeedbackPayload,
  HumanDecisionRecord,
  Opportunity,
  AlertResolution,
  RegistrationCase,
  ResultReview,
  RuntimeConfig,
  ScanResult,
  SearchCapability,
  SearchResult,
  SourceHealth,
  SystemHealth,
  TimelineEvent,
} from '../types/domain'

/** 项目到期事件的解决方式（后端 /api/projects/{id}/expiry/resolve 只接受这两种）。 */
export type ExpiryResolution = 'new_tender_matched' | 'renewed'

export interface ExpiryResolveOptions {
  /** resolution=new_tender_matched 时必填：新公告的 Source Pointer，禁止无证据关闭。 */
  sourcePointerId?: string
  /** 匹配置信度，低于阈值后端不自动关闭，转人工核验。 */
  confidence?: number
  note?: string
  actorId?: string
}

/** 全字段编辑：field=共享盘列头名或 BidOS 系列（BidOS参与状态 等 4 列）；value 空串=清空。 */
export interface FieldEdit {
  field: string
  value: string
}

export interface FieldEditResponse {
  status: string
  id: string
  applied: number
  edits: Array<{ id: string; field: string; value: string; editor: string; at: string; excel: Record<string, unknown> }>
  excel: { sheet: string; row: string }
}

/** 我方结果（后端正源行判定：ledger_2026/announcement 族，排除竞对统计等）。 */
export interface OwnResultRecord {
  projectId: string
  result: string
  winner: string
  winningPrice: number | null
  /** 多单位中标：与 winner 按行一一对齐的金额行（rebuild_own_results 对齐写入） */
  amountLines?: string[]
}

export interface CandidateActionResponse {
  status: string
  id: string
  [key: string]: unknown
}

export interface DataProvider {
  getDashboard(): Promise<DashboardSummary>

  /**
   * 首页关键时间轴。只返回**未解决**的高价值事件（已读但未解决仍会返回）。
   * @param limit 默认 20
   */
  getEventTimeline(limit?: number): Promise<TimelineEvent[]>

  /**
   * scope=active（默认）只返回进行中的机会；all 含历史；blind 返回全量盲投月表行
   * （含历史），供盲投页；candidates 返回新信息队列（公告候选且无 dismissed/promotion），
   * 行形状同盲投行并额外带 metadata（landing 判「AI助手」筛选人）。
   */
  getOpportunities(scope?: 'active' | 'all' | 'blind' | 'candidates'): Promise<Opportunity[]>

  /**
   * 全字段编辑（盲投详情抽屉）：append 进 projects.metadata.system_edits（pending），
   * 渲染层即时覆盖 sourceFields 同名列；共享盘回写由回写引擎按标记完成。
   * 无盲投月表指针时后端 400 拒绝（绝不静默丢编辑）。
   */
  applyFieldEdits(projectId: string, edits: FieldEdit[]): Promise<FieldEditResponse>

  /** 新信息「报名」：公告候选 → 盲投信息，打 candidate_promotion 标记（回写引擎加行）。 */
  promoteCandidate(projectId: string): Promise<CandidateActionResponse>

  /** 新信息「不报名 / 忽略」：打 dismissed 标记（reason 可空），行退出队列。 */
  dismissCandidate(projectId: string, reason?: string): Promise<CandidateActionResponse>

  /** 我方结果（正源行判定）：报名参与=中标/未中标/待公布；报名未参与恒为未中标。 */
  getOwnResults(): Promise<OwnResultRecord[]>

  /** 系统健康：data_mode + 各子系统 available/degraded。失败可见，不允许假装正常。 */
  getHealth(): Promise<SystemHealth>

  /** 运行时配置：用于排查"为什么读的是 mock"。 */
  getConfig(): Promise<RuntimeConfig>

  /** 提醒中心。只返回未解决事件，handled=true 的仍会出现（已读 != 已解决）。
   *  group：业务分组（all/opportunities/key_projects/deadlines/expiry/bonds/data_review），
   *  用于把时间敏感事件与导入期数据待办分离——否则几百条数据待办会把业务事件挤出可视范围。 */
  getAlerts(group?: string): Promise<AlertItem[]>

  /** 各业务分组未解决计数（tab 徽标）。 */
  getAlertGroupCounts(): Promise<Record<string, number>>

  /** 只标记已读。事件仍然保留在首页与提醒中心。 */
  handleAlert(eventId: string): Promise<EventActionResponse>

  /**
   * 业务上真正解决事件，解决后事件不再出现。
   * 保证金事件仅接受例外关闭（converted_to_performance_bond / written_off），
   * 此时 sourcePointerId（财务凭证）与 note（原因）必填，缺一后端会拒绝。
   */
  resolveAlert(eventId: string, resolution: AlertResolution, note?: string, actorId?: string, sourcePointerId?: string): Promise<EventActionResponse>

  /** 按确定性规则重算持续事件（项目到期 / 保证金应退与逾期）。 */
  scanEvents(): Promise<ScanResult>

  /** 项目级解决到期事件：新公告匹配 / 确认续签。 */
  resolveProjectExpiry(projectId: string, resolution: ExpiryResolution, options?: ExpiryResolveOptions): Promise<EventActionResponse>

  getRegistrations(): Promise<RegistrationCase[]>
  /** scope=active（默认）只评进行中项目；all 含历史。 */
  getEvaluations(scope?: 'active' | 'all'): Promise<EvaluationCase[]>
  getBidDocuments(): Promise<BidDocument[]>

  /** 结果复盘，支持时间段筛选。start/end 为 YYYY-MM-DD。 */
  getResults(start?: string, end?: string): Promise<ResultReview[]>

  getBidBonds(): Promise<BidBondRecord[]>

  /**
   * 保证金到账确认。必须提供到账凭证 Source Pointer，缺凭证后端会返回 400。
   */
  confirmBondRefund(ledgerId: string, refundSourcePointerId: string, actorId?: string): Promise<EventActionResponse>

  getCompetitors(): Promise<Competitor[]>
  getCompetitorOverlap(name: string): Promise<Record<string, unknown>>
  getKeyProjects(): Promise<Record<string, unknown>>
  scanKeyProjects(): Promise<Record<string, unknown>>
  getAuditLogs(limit?: number): Promise<Array<Record<string, unknown>>>
  getFeedbacks(limit?: number): Promise<Array<Record<string, unknown>>>
  search(query: string, limit?: number): Promise<SearchResult[]>

  /** 检索能力自检：向量/reranker 不可用时如实说明，不假装是语义结果。 */
  getSearchCapability(): Promise<SearchCapability>

  getDecisionSupport(projectId: string, question: string): Promise<DecisionSupport>
  getSourceHealth(): Promise<SourceHealth[]>

  /** 抓取失败的来源。绝不允许静默漏抓。 */
  getUnhealthySources(): Promise<SourceHealth[]>

  submitFeedback(payload: FeedbackPayload): Promise<{ id: string }>
  saveHumanDecision(projectId: string, decision: '参与' | '不参与' | '待讨论', note: string, sourcePointerIds?: string[]): Promise<{ status: string; audit_id: string }>
  /** 历史人工结论（新的在前），供评估页回显与追溯。 */
  getHumanDecisions(projectId: string): Promise<HumanDecisionRecord[]>
}
