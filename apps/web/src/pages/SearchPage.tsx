import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { useWorkspace, searchProjects, type WorkspaceProject, type SourceDocument } from '../services/workspace'
import { usePortfolio, type SheetLocation } from '../services/portfolio'
import { WorkspaceNotice, ProjectStatus, SourceDetails, SourceButton, safeUrl } from './WorkspaceUI'
type Hit={id:string;kind:string;title:string;detail:string;path?:string;url?:string;source?:SheetLocation;project?:WorkspaceProject;document?:SourceDocument}
export function SearchPage(){
  const ws=useWorkspace()
  const portfolio=usePortfolio()
  const[params,setParams]=useSearchParams()
  const[q,setQ]=useState(params.get('q')||'')
  const[limit,setLimit]=useState(30)
  const[kind,setKind]=useState('')
  const query=params.get('q')||''
  useEffect(()=>{setQ(query);setLimit(30);setKind('')},[query])
  const words=query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const matches=(text:string)=>words.length>0&&words.every(w=>text.toLowerCase().includes(w))
  const hits:Hit[]=query.trim()?searchProjects(ws.data?.projects||[],query).map(p=>({id:p.id,kind:'我方项目',title:p.name,detail:[p.client,p.owner&&`负责人 ${p.owner}`,p.channel,p.limit,p.openingAt].filter(Boolean).join(' · '),path:`/registration?select=${encodeURIComponent(p.id)}`,url:p.url,project:p})):[]
  for(const company of portfolio.data?.competitors||[])for(const p of company.projects){if(matches([company.name,p.name,p.client,p.winner,p.region,p.business,...Object.values(p.fields||{}),...(p.sourceRows||[]).flatMap(s=>Object.values(s.fields||{}))].join(' ')))hits.push({id:`competitor_${company.id}_${p.id}`,kind:'竞对项目',title:p.name,detail:[company.name,p.client,p.result,p.date,p.amount].filter(Boolean).join(' · '),path:`/competitors?select=${encodeURIComponent(company.id)}&project=${encodeURIComponent(p.id)}`,source:p.source,url:p.url})}
  for(const b of portfolio.data?.bonds||[]){if(matches([b.name,b.client,b.company,b.owner,b.kind,b.note,...Object.values(b.sourceFields||{})].join(' ')))hits.push({id:b.id,kind:'保证金',title:b.name,detail:[b.kind,b.amountText,b.status,b.owner,b.note].filter(Boolean).join(' · '),path:`/bid-bonds?select=${encodeURIComponent(b.id)}`,source:b.sources[0]})}
  for(const d of ws.data?.sourceDocuments||[]){if(!d.empty&&matches([d.title,...d.cells.map(c=>c.value)].join(' ')))hits.push({id:d.id,kind:'原表分析资料',title:d.title,detail:`${d.source.file} · ${d.source.sheet}`,source:d.source,document:d})}
  const rows=kind?hits.filter(h=>h.kind===kind):hits
  const kinds=[...new Set(hits.map(h=>h.kind))]
  const run=()=>{setParams(q.trim()?{q:q.trim()}:{});setLimit(30);setKind('')}
  return <section className="page ws-page"><PageHeader title="全局搜索" subtitle="检索项目、甲方、竞对、保证金和原表中的资料，查看完整信息与来源。"/><div className="ws-toolbar"><input type="search" aria-label="全局搜索关键词" value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')run()}} placeholder="输入项目名、甲方或关键词，多个词用空格分开"/><button className="btn primary" onClick={run}>搜索</button></div><WorkspaceNotice {...ws}/>{portfolio.error&&<p className="ws-note ws-error">竞对与保证金搜索未加载：{portfolio.error}</p>}{query&&portfolio.loading&&<p className="ws-subtle">正在读取竞对与保证金资料…</p>}{!query?<div className="panel ws-empty">输入关键词开始检索；项目各来源中的备注和补充内容也会一起搜索。</div>:ws.data&&<><div className="tabs"><button className={!kind?'active':''} onClick={()=>{setKind('');setLimit(30)}}>全部 {hits.length}</button>{kinds.map(k=><button key={k} className={kind===k?'active':''} onClick={()=>{setKind(k);setLimit(30)}}>{k} {hits.filter(h=>h.kind===k).length}</button>)}</div><div className="panel ws-work">{rows.slice(0,limit).map(hit=><article className="ws-search-row" key={hit.id}>{hit.project?<ProjectStatus project={hit.project}/>:<span className="ws-subtle">{hit.kind}</span>}<h3>{hit.title}</h3><p>{hit.detail}</p><div className="ws-actions">{hit.path&&<Link className="btn primary" to={hit.path}>查看完整资料</Link>}{hit.source&&<SourceButton source={hit.source}/>} {safeUrl(hit.url||'')&&<a className="btn ghost" href={hit.url} target="_blank" rel="noreferrer">公告原文 ↗</a>}</div>{hit.project&&<SourceDetails project={hit.project}/>} {hit.document&&<details className="ws-source"><summary>查看原表内容</summary>{hit.document.cells.map((c,i)=><p className="ws-subtle" key={`${c.cell}-${i}`} style={{whiteSpace:'pre-wrap'}}><b>{c.cell}</b> {c.value}</p>)}</details>}</article>)}{!rows.length&&<p className="ws-empty">没有匹配记录，可缩短名称或减少关键词。</p>}{rows.length>limit&&<div className="ws-actions"><button className="btn ghost" onClick={()=>setLimit(n=>n+30)}>继续显示 {Math.min(30,rows.length-limit)} 条</button></div>}</div></>}</section>
}
