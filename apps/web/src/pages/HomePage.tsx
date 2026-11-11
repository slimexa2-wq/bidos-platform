import { Link } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { useWorkspace, workProjects, nextWorkDate } from '../services/workspace'
import { fiscalYear } from '../services/portfolio'
import { TaskRows, WorkspaceNotice } from './WorkspaceUI'
export function HomePage() {
  const ws=useWorkspace()
  const projects=ws.data?.projects || []
  const tasks=ws.data?.tasks || []
  const year=fiscalYear()
  const participating=projects.filter(p=>p.partStatus==='报名参与' && !/盲投项目信息/.test(p.source?.file||'') && p.year===year)
  const awaiting=projects.filter(p=>p.partStatus==='待评估' && !p.isHistorical)
  const active=workProjects(projects)
  const won=participating.filter(p=>p.resultStatus==='中标')
  return <section className="page ws-page"><PageHeader title="工作台" subtitle="先处理今天要做的事，再查看项目进度。" action={<button className="btn ghost" disabled={ws.refreshing} onClick={()=>void ws.refresh().catch(()=>{})}>{ws.refreshing?'更新中…':'刷新'}</button>} /><WorkspaceNotice {...ws} />{ws.data && <><div className="ws-metrics ws-home-metrics"><Link to="/blind?tab=待评估"><span>待评估</span><b>{awaiting.length}</b><small>等待人工评估</small></Link><Link to="/registration"><span>在办项目</span><b>{active.length}</b><small>报名、计划与工作记录</small></Link><Link to="/alerts"><span>待处理事项</span><b>{tasks.length}</b><small>按实际业务状态更新</small></Link></div><div className="panel ws-taskpanel"><div className="panel-title"><h2>需要处理</h2><Link to="/alerts">查看全部 {tasks.length} 项</Link></div><TaskRows tasks={tasks.slice(0,10)} /></div><div className="ws-home-secondary"><Link to="/results">{year}财年参与 <b>{participating.length}</b> · 中标 <b>{won.length}</b> <small>（{Number(year)-1}年12月—{year}年11月）</small></Link><Link className="btn ghost" to="/decision-brief">生成决策卡 ↗</Link></div><div className="panel ws-taskpanel" style={{marginTop:20}}><div className="panel-title"><h2>在办项目</h2><Link to="/registration">查看完整计划</Link></div>{active.slice(0,8).map(p=><Link className="ws-task" to={`/registration?select=${encodeURIComponent(p.id)}`} key={p.id}><div><b>{p.name}</b><small>{[p.owner && `负责人 ${p.owner}`,p.work?.nextAction || p.progress || p.plan].filter(Boolean).join(' · ')}</small></div><time>{nextWorkDate(p).label} {nextWorkDate(p).value}</time><span>›</span></Link>)}{!active.length&&<p className="ws-empty">当前没有在办项目，历史计划仍可在报名管理查看。</p>}</div><p className="ws-subtle">每小时快照 {new Date(ws.data.snapshot?.generatedAt || ws.data.updatedAt).toLocaleString('zh-CN')} · 项目资料按跨表来源合并，工作备注与材料清单随项目保存。</p></>}</section>
}
