import { Link } from 'react-router-dom'
import { useWorkspace, type WorkspaceProject } from '../services/workspace'
import { Workbench, ProjectSummary, SourceDetails, WorkRecord, blank } from './WorkspaceUI'
function Reference({project:p}:{project:WorkspaceProject}){
  const ws=useWorkspace()
  const shown=new Set([p.name,p.client,p.channel,p.deadline,p.openingAt,p.owner,p.limit,p.progress,p.plan].filter(Boolean).map(v=>v.trim()))
  const relevant=Object.entries(p.fields||{}).filter(([key,value])=>/项目情况|要求|资质|付款|服务期|服务内容|备注|说明|限价|报名|保证金|评分/.test(key)&&value&&!/^BidOS/.test(key)&&!shown.has(value.trim()))
  const history=(ws.data?.projects||[]).filter(x=>x.id!==p.id&&x.client&&x.client===p.client&&x.isHistorical).slice(0,5)
  return <><h3>已有项目信息</h3>{relevant.length?<dl className="ws-fields">{relevant.map(([k,v])=><div key={k}><dt>{k}</dt><dd>{blank(v)}</dd></div>)}</dl>:<p className="ws-subtle">现有台账没有更多要求说明，请查看招标原文后记录评估意见。</p>}{history.length>0&&<><h3>同甲方历史项目</h3>{history.map(x=><Link key={x.id} className="ws-task" to={`/evaluation?select=${encodeURIComponent(x.id)}`}><div><b>{x.name}</b><small>{[x.openingAt,x.resultStatus,x.limit].filter(Boolean).join(' · ')}</small></div><span>›</span></Link>)}</>}</>
}
export function EvaluationPage(){return <Workbench includeAssessment title="摘要评估" subtitle="集中阅读已有资料，记录人工判断和下一步核实事项。">{p=><><ProjectSummary project={p}/><Reference project={p}/><WorkRecord project={p} decision/><SourceDetails project={p}/></>}</Workbench>}
