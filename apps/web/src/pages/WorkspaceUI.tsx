import { useEffect, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useWorkspace, workProjects, nextWorkDate, searchProjects, saveProjectWork, workSavedMessage, type WorkspaceProject, type WorkspaceTask, type ProjectWork } from '../services/workspace'
import { openPortfolioSource, type SheetLocation } from '../services/portfolio'
import { sourceDate, formatDiscountRate } from '../services/detailFields'
import { StatusPill } from '../components/StatusPill'
import { PageHeader } from '../components/PageHeader'
import { useProjectDraft } from '../services/projectDraft'
import './WorkspaceUI.css'
export const blank = (value: unknown) => value == null || !String(value).trim() ? '/' : String(value)
export const safeUrl = (value: string) => /^https?:\/\//i.test(value || '') ? value : undefined
export function WorkspaceNotice({ error, loading, refresh }: { error: string; loading: boolean; refresh: () => Promise<unknown> }) {
  return <>{error && <div className="ws-note ws-error" role="alert">数据读取失败：{error} <button className="btn ghost" onClick={() => void refresh().catch(() => {})}>重试</button></div>}{loading && <p className="ws-empty">正在读取最新项目与源表…</p>}</>
}
export function ProjectStatus({ project: p }: { project: WorkspaceProject }) {
  return <div className="ws-status-row">{p.partStatus && <StatusPill severity={p.partStatus === '报名参与' ? 'info' : p.partStatus === '待评估' ? 'warning' : 'neutral'}>{p.partStatus}</StatusPill>}{p.resultStatus && <StatusPill severity={p.resultStatus === '中标' ? 'success' : p.resultStatus === '待公布' ? 'warning' : 'neutral'}>{p.resultStatus}</StatusPill>}{p.isHistorical && <span className="ws-subtle">历史项目</span>}</div>
}
export function SourceButton({ source }: { source: SheetLocation }) {
  const [error, setError] = useState('')
  return <><button className="btn ghost" onClick={() => { setError(''); void openPortfolioSource(source).catch(e => setError(e.message)) }}>打开原表</button>{error && <span className="ws-error" role="alert">{error}</span>}</>
}
export function SourceDetails({ project: p }: { project: WorkspaceProject }) {
  return <details className="ws-source"><summary>原表资料与来源 · {p.sourceRows.length} 条记录</summary>{p.sourceRows.map((row, i) => <article key={`${row.source.file}-${row.source.sheet}-${row.source.row}-${i}`}><div className="ws-subtle">{row.source.file} · {row.source.sheet} · 第{row.source.row}行</div><div className="ws-actions"><SourceButton source={row.source} /></div><dl className="ws-fields">{Object.entries(row.fields || {}).filter(([key]) => !/^BidOS/i.test(key)).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{safeUrl(value) ? <a href={value} target="_blank" rel="noreferrer">打开链接 ↗</a> : /时间|日期/.test(key) ? blank(sourceDate(row.fields, key)) : /折扣率/.test(key) ? blank(formatDiscountRate(value)) : blank(value)}{row.comments?.[key] && <small className="ws-subtle"> · {row.comments[key]}</small>}</dd></div>)}</dl></article>)}</details>
}
export function ProjectSummary({ project: p }: { project: WorkspaceProject }) {
  return <><h2>{p.name}</h2><ProjectStatus project={p} /><dl className="ws-fields">{[['甲方',p.client],['参与来源',p.channel],['限价 / 招标金额',p.limit],['负责人',p.owner],['报名截止',p.deadline],['开标时间',p.openingAt],['目前进度',p.progress],['计划安排',p.plan]].map(([k,v]) => <div key={k}><dt>{k}</dt><dd>{blank(v)}</dd></div>)}</dl><div className="ws-actions">{safeUrl(p.url) && <a className="btn ghost" href={p.url} target="_blank" rel="noreferrer">招标原文 ↗</a>}{p.source && <SourceButton source={p.source} />}{p.blindPath && <Link className="btn ghost" to={p.blindPath}>查看参与状态</Link>}</div></>
}
export function Workbench({ title, subtitle, children, includeAssessment = false }: { title: string; subtitle: string; children: (p: WorkspaceProject) => ReactNode; includeAssessment?: boolean }) {
  const ws = useWorkspace()
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState('')
  const [history, setHistory] = useState(false)
  const [limit, setLimit] = useState(40)
  const requested = ws.data?.projects.find(p => p.id === params.get('select'))
  useEffect(() => { if (requested?.isHistorical) setHistory(true) }, [requested?.id,requested?.isHistorical])
  const rows = searchProjects(workProjects(ws.data?.projects || [], history, includeAssessment), query)
  const hiddenHistoricalTarget = Boolean(requested?.isHistorical && !history)
  const selected = hiddenHistoricalTarget ? undefined : rows.find(p => p.id === requested?.id) || rows[0]
  return <section className="page ws-page"><PageHeader title={title} subtitle={subtitle} /><WorkspaceNotice {...ws} /><div className="ws-toolbar"><input type="search" aria-label="搜索工作项目" placeholder="搜索项目、甲方或负责人" value={query} onChange={e => { setQuery(e.target.value); setLimit(40) }} /><label><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} />包含历史项目</label><span className="ws-subtle">{rows.length}个项目</span>{requested?.isHistorical && history && <small className="ws-subtle">已按项目链接展开历史项目，可取消勾选收起。</small>}<button className="btn ghost" disabled={ws.refreshing} onClick={() => void ws.refresh().catch(() => {})}>刷新</button></div>{!ws.loading && <div className="ws-split"><aside className="panel ws-projects">{rows.slice(0,limit).map(p => <button key={p.id} className={selected?.id === p.id ? 'active' : ''} onClick={() => setParams({ select: p.id })}><b>{p.name}</b><small>{[p.client,p.owner].filter(Boolean).join(' · ')}</small><small>{[p.partStatus,p.progress,nextWorkDate(p).value && `${nextWorkDate(p).label} ${nextWorkDate(p).value}`].filter(Boolean).join(' · ')}</small></button>)}{!rows.length && <div className="ws-empty">当前没有符合筛选的在办项目，可切换“包含历史项目”查看。</div>}{rows.length > limit && <button onClick={() => setLimit(n => n+40)}>继续显示 {Math.min(40,rows.length-limit)} 条</button>}</aside>{selected ? <div className="panel ws-work" key={selected.id}>{children(selected)}</div> : <div className="panel ws-empty">{hiddenHistoricalTarget ? '当前链接指向历史项目；勾选“包含历史项目”或选择其他项目后处理。' : '选择项目后开始处理。'}</div>}</div>}</section>
}
export function DraftNotice({ dirty, discard }: { dirty: boolean; discard: () => void }) {
  return dirty ? <div className="ws-note" role="status">有未保存修改 · 草稿按项目保留在当前浏览器会话，切换项目或页面后可继续编辑。<button className="btn ghost" onClick={() => { if (window.confirm('放弃此处未保存的草稿，恢复已保存内容？')) discard() }}>放弃草稿</button></div> : null
}
export function WorkRecord({ project: p, decision = false }: { project: WorkspaceProject; decision?: boolean }) {
  const fromProject = (work = p.work) => ({ baseline: work, notes: work?.notes || '', next: work?.nextAction || '', due: work?.dueAt || '', choice: work?.decision || '', owner: work?.owner || p.owner || '', dirty: false })
  const draft = useProjectDraft(`${p.id}:work:${decision ? 'evaluation' : 'followup'}`, fromProject())
  const { baseline, notes, next, due, choice, owner, dirty } = draft.value
  const [message,setMessage] = useState('')
  const [failed,setFailed] = useState(false)
  const [busy,setBusy] = useState(false)
  useEffect(() => { if (!dirty) draft.reset(fromProject()) }, [p.work,p.owner,dirty,draft.reset])
  const change = (patch: Partial<typeof draft.value>) => draft.update({ ...patch, dirty: true })
  const save = async (complete = false) => {
    const revision = draft.getRevision()
    setBusy(true); setMessage(''); setFailed(false)
    try {
      // Completion belongs only to this work follow-up. Keeping the completed
      // text and time in notes uses the existing durable Excel work-log field.
      const completionEntry = complete ? `【跟进完成 ${new Date().toLocaleString('zh-CN')}】\n事项：${next.trim()}${due ? `\n原跟进日期：${due}` : ''}` : ''
      const completedNotes = complete ? [notes.trim(), completionEntry].filter(Boolean).join('\n\n') : notes
      const changes: Partial<ProjectWork> = { notes: completedNotes, nextAction: complete ? '' : next, dueAt: complete ? '' : due, owner }
      if(decision) changes.decision=choice
      const saved=await saveProjectWork(p,changes,baseline)
      if (!draft.reset(fromProject(saved), revision)) {
        // Keep newer edits, but let their next save build on this successful
        // write. Completion still updates fields the user has not changed.
        draft.update(previous => {
          if (previous.baseline?.version !== baseline?.version || previous.baseline?.digest !== baseline?.digest) return previous
          const submitted = { notes, next, due, choice, owner }
          const savedDraft = fromProject(saved)
          const merged = { ...previous, baseline: saved }
          for (const field of ['notes', 'next', 'due', 'choice', 'owner'] as const) {
            if (previous[field] === submitted[field]) merged[field] = savedDraft[field]
            else if (field === 'notes' && complete) merged.notes = [previous.notes.trim(), completionEntry].filter(Boolean).join('\n\n')
          }
          return merged
        })
      }
      setMessage(complete ? `本次跟进已完成，已移出工作跟进提醒；完成内容和时间已保存在工作备注。${workSavedMessage(saved)}` : `${next.trim() ? `已安排下次跟进${due ? ` · ${due}` : '（未设置日期）'}。` : ''}${workSavedMessage(saved)}`)
    } catch(e) { setFailed(true); setMessage(e instanceof Error ? e.message : '保存失败') }
    finally {setBusy(false)}
  }
  return <><h3>{decision ? '人工评估记录' : '本次跟进'}</h3><DraftNotice dirty={dirty} discard={() => draft.reset(fromProject())} />{decision && <><label className="ws-formlabel" htmlFor="work-decision">评估意见</label><select id="work-decision" disabled={busy} value={choice} onChange={e => change({ choice: e.target.value })}><option value="">未记录</option><option>建议参与</option><option>建议不参与</option><option>待讨论</option></select><p className="ws-subtle">评估意见保存在工作记录中。{p.blindPath ? '实际参与状态请通过上方“查看参与状态”调整。' : '实际参与状态以源表为准。'}</p></>}<label className="ws-formlabel" htmlFor="work-owner">负责人</label><input id="work-owner" value={owner} disabled={busy} onChange={e=>change({owner:e.target.value})} placeholder="负责人姓名"/><label className="ws-formlabel" htmlFor="next-action">下一步</label><input id="next-action" style={{width:'100%'}} disabled={busy} value={next} onChange={e=>change({next:e.target.value})} placeholder="例如：向代理确认报名要求" /><label className="ws-formlabel" htmlFor="work-due">跟进日期</label><input id="work-due" type="date" disabled={busy} value={due} onChange={e=>change({due:e.target.value})} /><label className="ws-formlabel" htmlFor="work-notes">工作备注</label><textarea id="work-notes" rows={5} disabled={busy} value={notes} onChange={e=>change({notes:e.target.value})} placeholder="记录已核实的信息、处理进展或需要协助的事项" /><p className="ws-subtle">保存记录会保留下一步提醒。完成本次跟进后，事项和时间保存在工作备注；如需继续跟进，填写下一步和日期后保存。保证金等业务提醒仍通过对应业务流程处理。</p><div className="ws-actions"><button className="btn primary" disabled={busy} onClick={() => void save()}>{busy?'保存中…':'保存工作记录'}</button>{!decision && <button className="btn ghost" disabled={busy || !next.trim()} onClick={() => void save(true)}>完成本次跟进</button>}{message && <span role="status" className={failed?'ws-error':'ws-subtle'}>{message}</span>}</div>{p.work?.updatedAt && <small className="ws-subtle">最近保存 {new Date(p.work.updatedAt).toLocaleString('zh-CN')} · {p.work.sync}</small>}</>
}
export function TaskRows({ tasks }: { tasks: WorkspaceTask[] }) { return <>{tasks.map(task=><Link className="ws-task" to={task.path || (task.projectId?`/registration?select=${encodeURIComponent(task.projectId)}`:'/alerts')} key={task.id}><span className={`dot ${task.severity}`} /><div><b>{task.title}</b>{task.detail && <small>{task.detail}</small>}</div>{task.at && <time>{task.at.slice(0,10)}</time>}<span className="ws-task-open">处理 ›</span></Link>)}{!tasks.length && <div className="ws-empty">当前没有需要处理的事项。</div>}</> }

export function RelatedWorkHistory({ sourceKeys = [], projectId }: { sourceKeys?: SheetLocation[]; projectId?: string }) {
  const ws=useWorkspace()
  const p=ws.data?.projects.find(item=>item.id===projectId||item.blindId===projectId||item.sources.some(s=>sourceKeys.some(k=>k.file===s.file&&k.sheet===s.sheet&&String(k.row)===String(s.row))))
  if(!p) return null
  const plans=p.planHistory||[]
  const work=p.work
  if(!plans.length&&!p.owner&&!p.progress&&!work?.updatedAt)return null
  return <section className="ws-page ws-source"><h3>工作进度</h3>{(p.owner||p.progress)&&<p className="ws-subtle">{[p.owner&&`负责人：${p.owner}`,p.progress&&`最新进度：${p.progress}`].filter(Boolean).join(' · ')}</p>}{(work?.nextAction||work?.notes||work?.decision)&&<div className="ws-note">{work.decision&&<div>评估记录：{work.decision}</div>}{work.nextAction&&<div>下一步：{work.nextAction}{work.dueAt&&` · ${work.dueAt}`}</div>}{work.notes&&<div>{work.notes}</div>}</div>}{plans.length>0&&<details><summary>周计划记录 · {plans.length} 条</summary>{plans.map((row,i)=><article key={i}><div className="ws-subtle">{row.source.sheet} · 第{row.source.row}行</div><p className="ws-subtle">{Object.entries(row.fields||{}).filter(([k,v])=>v&&/进度|负责人|计划|时间|备注/.test(k)).map(([k,v])=>`${k}：${v}`).join('；')}</p><SourceButton source={row.source}/></article>)}</details>}<div className="ws-actions"><Link className="btn ghost" to={`/registration?select=${encodeURIComponent(p.id)}`}>打开工作记录</Link></div></section>
}
