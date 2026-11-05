import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { provider } from '../services'
import type { FeedbackPayload, SourceHealth } from '../types/domain'
import { PageHeader } from '../components/PageHeader'
import { StatusPill } from '../components/StatusPill'
import { useWorkspace, workspaceRequest } from '../services/workspace'
import { WorkspaceNotice, SourceButton } from './WorkspaceUI'
import './WorkspaceUI.css'

/**
 * 系统运维页（2026-09-28）。
 *
 * 数据契约（全部为后端实测，非猜测）：
 * - GET /api/sources/health → SourceHealth[]：source_id / source_name / strategy /
 *   last_success_at / last_checked_at / consecutive_failures / items_found /
 *   requires_login / human_action_required / last_error
 * - GET /api/audit → Array<Record<string,unknown>>：id / action / actor_id /
 *   object_type / object_id / created_at / before / after / source_pointer_ids / metadata
 * - GET /api/feedback → FeedbackRecord[]：id / created_at / object_type / object_id /
 *   ai_output / corrected_output / reason / evidence_source_ids / user_id
 * - POST /api/feedback ← FeedbackInput（object_type / object_id / ai_output /
 *   corrected_output / reason / evidence_source_ids / user_id，后三者在后端有默认值）
 *
 * 纪律：只用 provider 真实数据；空态 / 错误如实展示，失败不许冒充"无数据"。
 */

// --------------------------------------------------------------- 展示辅助

/** ISO 时间 → 本地可读；解析失败时原样透出（不编造"-"掩盖脏数据）。 */
const fmtTime = (iso: unknown): string => {
  if (typeof iso !== 'string' || !iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

/** Record 字段安全取字符串（后端返回的是宽松 Record，不能直接下标断言）。 */
const str = (row: Record<string, unknown>, key: string): string => {
  const v = row[key]
  return typeof v === 'string' ? v : ''
}

/** 对象 / 数组压缩成一行 JSON 摘要，超长截断——如实透出，不做语义改写。 */
const brief = (v: unknown, max = 140): string => {
  if (v === null || v === undefined) return ''
  let text = ''
  try { text = JSON.stringify(v) } catch { return String(v) }
  if (text === undefined) return String(v)
  return text.length > max ? `${text.slice(0, max)}…` : text
}

const cellText = (text: string, max = 120): string =>
  text.length > max ? `${text.slice(0, max)}…` : (text || '—')

// --------------------------------------------------------------- 区块一：数据源健康

function SourceHealthSection() {
  const [items, setItems] = useState<SourceHealth[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true); setError(null)
    return provider.getSourceHealth()
      .then(setItems)
      .catch(e => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const total = items.length
  const healthy = items.filter(x => x.consecutive_failures === 0 && !!x.last_success_at).length
  const failing = total - healthy
  const needHuman = items.filter(x => x.human_action_required).length
  const needLogin = items.filter(x => x.requires_login).length
  const found = items.reduce((a, b) => a + (Number.isFinite(b.items_found) ? b.items_found : 0), 0)

  return (
    <section style={{ marginTop: 18 }}>
      <PageHeader title="数据源健康" subtitle="抓取失败绝不允许静默漏报。连续失败 > 0 的来源整行红色标注，错误原样透出。" />
      <div className="kpis">
        <div><span>数据源健康 / 总数</span><b style={{ color: total > 0 && healthy === total ? 'var(--green)' : 'var(--red)' }}>{healthy} / {total}</b></div>
        <div><span>连续失败来源</span><b style={{ color: failing > 0 ? 'var(--red)' : undefined }}>{failing}</b></div>
        <div><span>需人工介入</span><b>{needHuman}</b></div>
        <div><span>需登录 / 最近检查发现条目</span><b>{needLogin} / {found}</b></div>
      </div>
      {error && <div className="callout warning" role="alert"><b>数据源健康加载失败</b><p>{error}。请确认后端服务可用后点击「刷新」。</p></div>}
      {loading && !error && <p style={{ color: 'var(--muted)' }}>加载中…</p>}
      {!loading && !error && total === 0 && <div className="callout info"><b>后端未返回任何数据源健康记录</b><p>列表为空（GET /api/sources/health 返回 []），非前端过滤。</p></div>}
      {total > 0 && (
        <div className="panel table-wrap">
          <table>
            <thead><tr>
              <th>数据源</th><th>策略</th><th>最近成功</th><th>最近检查</th>
              <th>连续失败</th><th>发现条目</th><th>最近错误</th>
            </tr></thead>
            <tbody>
              {items.map(s => {
                const bad = s.consecutive_failures > 0
                return (
                  <tr key={s.source_id} style={bad ? { background: 'var(--red-soft)' } : undefined}>
                    <td><b>{s.source_name}</b><small>{s.source_id}{s.human_action_required ? ' · 需人工介入' : ''}</small></td>
                    <td>
                      {s.strategy}
                      {s.requires_login && <><br /><StatusPill severity="warning">需登录</StatusPill></>}
                    </td>
                    <td>{fmtTime(s.last_success_at)}</td>
                    <td>{fmtTime(s.last_checked_at)}</td>
                    <td>{bad ? <StatusPill severity="critical">连续失败 {s.consecutive_failures}</StatusPill> : <StatusPill severity={s.last_success_at ? "success" : "neutral"}>{s.last_success_at ? "最近成功" : "尚未成功"}</StatusPill>}</td>
                    <td>{s.items_found}</td>
                    <td style={{ whiteSpace: 'normal', minWidth: 260, maxWidth: 420, color: bad ? 'var(--red)' : undefined }}>
                      {cellText(s.last_error || '')}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
        <button className="btn ghost" onClick={() => load()} disabled={loading}>{loading ? '刷新中…' : '刷新'}</button>
      </div>
    </section>
  )
}

// --------------------------------------------------------------- 区块二：审计日志

function AuditSection() {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true); setError(null)
    return provider.getAuditLogs(50)
      .then(setItems)
      .catch(e => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <section style={{ marginTop: 18 }}>
      <PageHeader
        title="审计日志"
        subtitle="最近 50 条真实操作记录（GET /api/audit?limit=50）。时间 / 操作人 / 动作 / 对象 / 内容摘要，after·before 原样压缩透出。"
        action={<button className="btn ghost" onClick={() => load()} disabled={loading}>{loading ? '刷新中…' : '刷新'}</button>}
      />
      {error && <div className="callout warning" role="alert"><b>审计日志加载失败</b><p>{error}。日志可能不完整，请勿据此认为"系统无操作"。</p></div>}
      {loading && !error && <p style={{ color: 'var(--muted)' }}>加载中…</p>}
      {!loading && !error && items.length === 0 && <div className="callout info"><b>暂无审计日志</b><p>接口返回空列表。</p></div>}
      {items.length > 0 && (
        <div className="panel table-wrap">
          <table>
            <thead><tr>
              <th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>内容摘要</th>
            </tr></thead>
            <tbody>
              {items.map(row => {
                const pointers = Array.isArray(row.source_pointer_ids) ? row.source_pointer_ids : []
                const summary = brief(row.after) || brief(row.before) || ''
                return (
                  <tr key={str(row, 'id') || JSON.stringify(row).slice(0, 40)} style={{ cursor: 'default' }}>
                    <td>{fmtTime(row.created_at)}</td>
                    <td>{str(row, 'actor_id') || '—'}</td>
                    <td><b>{str(row, 'action') || '—'}</b></td>
                    <td>{str(row, 'object_type')}{str(row, 'object_id') ? ` · ${str(row, 'object_id')}` : ''}</td>
                    <td style={{ whiteSpace: 'normal', minWidth: 280, maxWidth: 480, color: 'var(--muted)' }}>
                      {cellText(summary)}
                      {pointers.length > 0 && <small>证据 Source Pointer ×{pointers.length}</small>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// --------------------------------------------------------------- 区块三：人工反馈

const EMPTY_FORM = { object_type: '', object_id: '', ai_output: '', corrected_output: '', reason: '', evidence: '', user_id: '' }

function FeedbackSection() {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const load = useCallback(() => {
    setLoading(true); setListError(null)
    return provider.getFeedbacks(50)
      .then(setItems)
      .catch(e => setListError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const set = (key: keyof typeof EMPTY_FORM) => (e: { target: { value: string } }) =>
    setForm(f => ({ ...f, [key]: e.target.value }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!form.object_type.trim() || !form.object_id.trim()) {
      setMsg({ kind: 'err', text: '「对象类型」与「对象 ID」必填：反馈必须能落到具体对象上。' }); return
    }
    // FeedbackPayload 契约：evidence_source_ids 逗号/分号/空白分隔；user_id 留空走后端默认。
    const payload: FeedbackPayload = {
      object_type: form.object_type.trim(),
      object_id: form.object_id.trim(),
      ai_output: form.ai_output,
      corrected_output: form.corrected_output,
      reason: form.reason,
      evidence_source_ids: form.evidence.split(/[,，;；\s]+/).map(t => t.trim()).filter(Boolean),
      user_id: form.user_id.trim() || undefined,
    }
    setBusy(true); setMsg(null)
    try {
      const res = await provider.submitFeedback(payload)
      setMsg({ kind: 'ok', text: `反馈已提交，记录 ID：${res.id}。已写入审计日志（human_feedback_submitted）。` })
      setForm({ ...EMPTY_FORM })
      await load()
    } catch (err) {
      setMsg({ kind: 'err', text: err instanceof Error ? err.message : '提交失败' })
    } finally {
      setBusy(false)
    }
  }

  const inputStyle = { width: '100%', border: '1px solid var(--line)', borderRadius: 10, padding: '10px 12px', background: '#fff' } as const

  return (
    <section style={{ marginTop: 18 }}>
      <PageHeader title="人工反馈" subtitle="AI 原输出 → 人工修正 → 修正原因 → 证据来源，四要素闭环（POST /api/feedback）。提交后写入审计日志。" />

      {msg && (
        <div className={`callout ${msg.kind === 'ok' ? 'success' : 'warning'}`} role={msg.kind === 'err' ? 'alert' : undefined}>
          <b>{msg.kind === 'ok' ? '提交成功' : '提交失败'}</b>
          <p>{msg.text}</p>
        </div>
      )}

      <form className="panel" style={{ padding: 18 }} onSubmit={submit}>
        <div className="panel-title"><h2>提交反馈</h2></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: 12 }}>
          <label>对象类型 *<input style={{ ...inputStyle, marginTop: 6 }} value={form.object_type} onChange={set('object_type')} placeholder="如 opportunity / evaluation / bid_document" /></label>
          <label>对象 ID *<input style={{ ...inputStyle, marginTop: 6 }} value={form.object_id} onChange={set('object_id')} placeholder="该反馈针对的对象 ID" /></label>
        </div>
        <label style={{ display: 'block', marginTop: 12 }}>AI 原输出<textarea style={{ ...inputStyle, marginTop: 6, minHeight: 64 }} value={form.ai_output} onChange={set('ai_output')} placeholder="AI 当时给出的原始结论 / 内容" /></label>
        <label style={{ display: 'block', marginTop: 12 }}>人工修正<textarea style={{ ...inputStyle, marginTop: 6, minHeight: 64 }} value={form.corrected_output} onChange={set('corrected_output')} placeholder="人工核实后的正确结论 / 内容" /></label>
        <label style={{ display: 'block', marginTop: 12 }}>修正原因<textarea style={{ ...inputStyle, marginTop: 6, minHeight: 64 }} value={form.reason} onChange={set('reason')} placeholder="为什么 AI 错了、依据是什么（建议填写，会写入审计日志）" /></label>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12, marginTop: 12 }}>
          <label>证据来源 ID（多个用逗号分隔）<input style={{ ...inputStyle, marginTop: 6 }} value={form.evidence} onChange={set('evidence')} placeholder="Source Pointer ID，逗号分隔；无证据可留空" /></label>
          <label>操作人（留空 = demo-user）<input style={{ ...inputStyle, marginTop: 6 }} value={form.user_id} onChange={set('user_id')} placeholder="user_id" /></label>
        </div>
        <div className="action-bar">
          <button type="button" className="btn ghost" onClick={() => { setForm({ ...EMPTY_FORM }); setMsg(null) }} disabled={busy}>清空</button>
          <button type="submit" className="btn primary" disabled={busy}>{busy ? '提交中…' : '提交反馈'}</button>
        </div>
      </form>

      <h3 style={{ margin: '22px 2px 10px' }}>历史反馈 <small style={{ color: 'var(--muted)', fontWeight: 400 }}>（最近 50 条，GET /api/feedback）</small></h3>
      {listError && <div className="callout warning" role="alert"><b>反馈历史加载失败</b><p>{listError}。请勿据此认为"没有任何反馈"。</p></div>}
      {loading && !listError && <p style={{ color: 'var(--muted)' }}>加载中…</p>}
      {!loading && !listError && items.length === 0 && <div className="callout info"><b>暂无历史反馈记录</b><p>接口当前返回空列表。提交上方表单后，记录会出现在这里。</p></div>}
      {items.length > 0 && (
        <div className="panel table-wrap">
          <table>
            <thead><tr>
              <th>时间</th><th>对象</th><th>操作人</th><th>AI 原输出</th><th>人工修正</th><th>修正原因</th><th>证据</th>
            </tr></thead>
            <tbody>
              {items.map((row, i) => {
                const evidence = Array.isArray(row.evidence_source_ids) ? row.evidence_source_ids : []
                return (
                  <tr key={str(row, 'id') || `${str(row, 'created_at')}-${i}`} style={{ cursor: 'default' }}>
                    <td>{fmtTime(row.created_at)}</td>
                    <td><b>{str(row, 'object_type')}</b><small>{str(row, 'object_id')}</small></td>
                    <td>{str(row, 'user_id') || '—'}</td>
                    <td style={{ whiteSpace: 'normal', minWidth: 200, maxWidth: 320 }}>{cellText(str(row, 'ai_output'))}</td>
                    <td style={{ whiteSpace: 'normal', minWidth: 200, maxWidth: 320 }}>{cellText(str(row, 'corrected_output'))}</td>
                    <td style={{ whiteSpace: 'normal', minWidth: 180, maxWidth: 300 }}>{cellText(str(row, 'reason'))}</td>
                    <td>{evidence.length > 0 ? `${evidence.length} 条` : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// --------------------------------------------------------------- 页面

type AutomationState = { displaySnapshots?: {intervalMinutes:number;refreshing:boolean;lastError:string;snapshots:Record<string,{generatedAt:string}>}; running?:string[]; scheduler?: { next_runs?:{job_id:string;next_run_time:string}[]; recent_runs?:{job_id:string;status:string;reason?:string;finished_at?:string}[] }; collection?: { lastRun?:string; finishedAt?:string; status?:string; usable?:number; failed?:number; newCount?:number; total?:number; channels?: {name:string;status:string;message?:string}[] }; resultMonitor?:{status?:string;finishedAt?:string;searched?:number;updated?:number;errors?:string[];sources?:{name:string;status:string;errorKind?:string;nextRetryAt?:string}[]} }
function collectionIssue(message: string) {
  if (/验证码|anti_bot|反爬/.test(message)) return '网站要求人工验证，当前无法自动读取。'
  if (/timeout|超时|handshake|TLS/i.test(message)) return '网站连接超时，稍后重试。'
  if (/登录|login/i.test(message)) return '需要登录网站后读取。'
  if (/未提取|解析|未适配/.test(message)) return '公告列表尚未接入，需要补充采集规则。'
  if (/连接失败|connect|dns/i.test(message)) return '网站暂时无法连接，稍后重试。'
  return message
}
export function OpsPage() {
  const ws=useWorkspace()
  const [automation,setAutomation]=useState<AutomationState|null>(null)
  const [automationError,setAutomationError]=useState('')
  const [busy,setBusy]=useState('')
  const [message,setMessage]=useState('')
  const [allChannels,setAllChannels]=useState(false)
  const load=useCallback(async()=>{try{setAutomation(await workspaceRequest<AutomationState>('/api/automation/status'));setAutomationError('')}catch(e){setAutomationError(e instanceof Error?e.message:'自动化状态读取失败')}},[])
  useEffect(()=>{void load();const timer=window.setInterval(()=>void load(),15000);return()=>window.clearInterval(timer)},[load])
  const run=async(job:string)=>{setBusy(job);setMessage('');try{const d=await workspaceRequest<{message?:string;status?:string}>('/api/automation/run',{job});setMessage(d.message||'任务已启动，结果会自动更新');await load()}catch(e){setMessage(e instanceof Error?e.message:'运行失败')}finally{setBusy('')}}
  const collection=automation?.collection
  const result=automation?.resultMonitor
  const running=!!busy||!!automation?.running?.length||collection?.status==='采集中'
  const channels=collection?.channels||[]
  const failedChannels=channels.filter(c=>/失败|需|未接|错误/.test(c.status))
  const shownChannels=allChannels?channels:(failedChannels.length?failedChannels:channels).slice(0,8)
  const nextCollection=automation?.scheduler?.next_runs?.find(j=>j.job_id==='blind_daily_collection')?.next_run_time
  const nextSnapshot=automation?.scheduler?.next_runs?.find(j=>j.job_id==='display_hourly_snapshot')?.next_run_time
  const nextResults=automation?.scheduler?.next_runs?.find(j=>j.job_id==='result_backfill')?.next_run_time
  return <section className="page ws-page"><PageHeader title="数据与自动化" subtitle="查看本轮实际收集结果、失败来源和源表整理情况。" action={<button className="btn ghost" onClick={()=>{void load();void ws.refresh(false).catch(()=>{})}}>刷新状态</button>}/><WorkspaceNotice {...ws}/>{automationError&&<div className="ws-note ws-error">{automationError}</div>}<div className="ws-metrics"><div><span>最近信息收集</span><b style={{fontSize:20}}>{collection?.status||'尚无执行记录'}</b><small>{collection?.finishedAt||collection?.lastRun?fmtTime(collection?.finishedAt||collection?.lastRun):'等待首次执行'}</small></div><div><span>本轮已读取 / 需处理渠道</span><b>{collection?.usable??channels.filter(c=>c.status==='已读取').length} / {collection?.failed??failedChannels.length}</b><small>未接入渠道不计为成功</small></div><div><span>本轮新增信息</span><b>{collection?.newCount??'—'}</b><small>近24小时公告</small></div><div><span>结果跟进更新</span><b>{result?.updated??'—'}</b><small>{result?.finishedAt?fmtTime(result.finishedAt):'尚无执行记录'}</small></div></div><div className="panel ws-work"><h2>自动化任务</h2><p className="ws-subtle">每小时生成数据快照 · 下次 {nextSnapshot?fmtTime(nextSnapshot):'待调度'} · 页面“刷新”读取最新源表。</p>{automation?.displaySnapshots?.lastError&&<p className="ws-note ws-error">{automation.displaySnapshots.lastError}</p>}<div className="ws-actions"><button className="btn primary" disabled={running} onClick={()=>void run('collection')}>{busy==='collection'||automation?.running?.includes('collection')?'正在收集…':'立即收集新信息'}</button><button className="btn ghost" disabled={running} onClick={()=>void run('results')}>{busy==='results'||automation?.running?.includes('results')?'正在跟进…':'跟进中标结果'}</button></div>{message&&<p className="ws-note" role="status">{message}</p>}<p className="ws-subtle">下次收集：{nextCollection?fmtTime(nextCollection):'待调度'} · 下次结果跟进：{nextResults?fmtTime(nextResults):'待调度'}</p>{!!result?.errors?.length&&<details className="ws-source"><summary>本轮 {result.errors.length} 项结果查询未完成 · 展开原因</summary>{result.sources?.length ? result.sources.map((s,i)=><p className="ws-note ws-error" key={i}>{s.name}：{s.errorKind === 'timeout' ? '连接超时' : s.status}{s.nextRetryAt && `，下次尝试 ${fmtTime(s.nextRetryAt)}`}</p>) : result.errors.map((e,i)=><p className="ws-note ws-error" key={i}>{collectionIssue(e)}</p>)}</details>}{collection?.channels?.length?<div>{shownChannels.map((c,i)=><div className="ws-task" key={`${c.name}-${i}`}><div><b>{c.name}</b>{c.message&&<small>{collectionIssue(c.message)}</small>}</div><StatusPill severity={/失败|需|未接|错误/.test(c.status)?'warning':/成功|完成|可用|已读取/.test(c.status)?'success':'neutral'}>{c.status}</StatusPill></div>)}{channels.length>shownChannels.length||allChannels?<button className="btn ghost" onClick={()=>setAllChannels(v=>!v)}>{allChannels?'优先显示需处理渠道':`查看全部 ${channels.length} 个已检查渠道`}</button>:null}</div>:<p className="ws-subtle">暂无渠道执行明细。运行后查看成功、失败或需要人工登录的来源。</p>}</div><div className="panel ws-work" style={{marginTop:20}}><h2>整理后的统一台账</h2><p className="ws-subtle">按项目、进度、保证金、竞对统一整理，保留原始来源。当前系统写回仍使用现有正本，下载文件为整理副本。</p><a className="btn primary" href="/api/workspace/organized-workbook" download>下载整理台账</a>{ws.data?.notes?.map((note,i)=><p key={i} className="ws-subtle">{note}</p>)}</div><details className="ws-source"><summary>原表分析资料 · {ws.data?.sourceDocuments?.filter(d=>!d.empty).length||0} 份</summary>{ws.data?.sourceDocuments?.filter(d=>!d.empty).map(d=><details className="ws-source" key={d.id}><summary>{d.title}</summary><p className="ws-subtle">{d.source.file} · {d.source.sheet}</p><SourceButton source={d.source}/>{d.cells.map((c,i)=><p key={i} className="ws-subtle" style={{whiteSpace:'pre-wrap'}}><b>{c.cell}</b> {c.value}</p>)}</details>)}</details><details className="ws-source"><summary>数据源运行明细</summary><SourceHealthSection/></details><details className="ws-source"><summary>操作记录</summary><AuditSection/></details><details className="ws-source"><summary>纠错反馈</summary><FeedbackSection/></details></section>
}
