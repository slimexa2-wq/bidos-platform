import { useEffect, useState } from 'react'
import { Workbench, ProjectSummary, SourceDetails } from './WorkspaceUI'
import { saveProjectWork, workSavedMessage, workspaceRequest, type WorkspaceProject } from '../services/workspace'
import { StatusPill } from '../components/StatusPill'
type CheckResult={issues:{severity:string;rule?:string;message?:string;text?:string;location?:string}[];error_count:number;warning_count:number;notice_count:number;text_parse?:{unparsed_fields?:string[]}}
function DocumentTools({project:p}:{project:WorkspaceProject}){
  const [baseline,setBaseline]=useState(p.work)
  const [text,setText]=useState(p.work?.documentText||'')
  const [required,setRequired]=useState('')
  const [keywords,setKeywords]=useState('')
  const [amount,setAmount]=useState('')
  const [headcount,setHeadcount]=useState('')
  const [start,setStart]=useState('')
  const [end,setEnd]=useState('')
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  const [result,setResult]=useState<CheckResult|null>(null)
  const [dirty,setDirty]=useState(false)
  useEffect(()=>{if(!dirty){const savedText=p.work?.documentText||'';if(savedText!==text)setResult(null);setText(savedText);setBaseline(p.work)}},[p.id,p.work,dirty])
  const save=async()=>{setBusy(true);try{const saved=await saveProjectWork(p,{documentText:text},baseline);setBaseline(saved);setDirty(false);setMessage(workSavedMessage(saved))}catch(e){setMessage(e instanceof Error?e.message:'保存失败')}finally{setBusy(false)}}
  const check=async()=>{if(!text.trim())return;setBusy(true);setResult(null);setMessage('');try{const meta:Record<string,unknown>={};if(start&&end)meta.dates=[{label:'服务期限',start,end}];if(amount.trim())meta.amount=Number(amount);if(headcount.trim())meta.headcount=Number(headcount);const data=await workspaceRequest<CheckResult>('/api/documents/check',{content:text,meta,required_sections:required.split(/[,，、\n]/).map(s=>s.trim()).filter(Boolean),required_keywords:keywords.split(/[,，、\n]/).map(s=>s.trim()).filter(Boolean)});setResult(data)}catch(e){setMessage(e instanceof Error?e.message:'检查失败')}finally{setBusy(false)}}
  const issues=result?.issues.filter(issue=>issue.rule!=='plain_text_parse')||[]
  const plain=(text:string)=>text.replace(/meta\.headcount/g,'基准人数').replace(/meta\.amount/g,'基准金额').replace(/meta\.dates/g,'服务起止日期').replace(/（未用默认值硬比）。?/g,'。')
  return <><h3>标书正文检查</h3><p className="ws-subtle">粘贴正在编写的真实正文或章节。检查金额、人数、日期及指定章节的一致性；版式、签章和附件请在原文档中核对。</p><textarea aria-label="标书正文" disabled={busy} rows={14} value={text} onChange={e=>{setText(e.target.value);setDirty(true);setResult(null)}} placeholder="粘贴 Word 中的正文或需要检查的章节…" /><details className="ws-source"><summary>设置检查依据（可选，按招标文件填写）</summary><label className="ws-formlabel" htmlFor="document-sections">必需章节</label><textarea id="document-sections" rows={2} value={required} onChange={e=>setRequired(e.target.value)} placeholder="每行一个章节名称" /><label className="ws-formlabel" htmlFor="document-keywords">必须包含的内容</label><textarea id="document-keywords" rows={2} value={keywords} onChange={e=>setKeywords(e.target.value)} placeholder="按原文要求填写，每行一项" /><div className="ws-fields"><label>基准金额（元）<input aria-label="基准金额" type="number" min="0" value={amount} onChange={e=>setAmount(e.target.value)} /></label><label>基准人数<input aria-label="基准人数" type="number" min="0" value={headcount} onChange={e=>setHeadcount(e.target.value)} /></label><label>服务开始<input aria-label="服务开始日期" type="date" value={start} onChange={e=>setStart(e.target.value)}/></label><label>服务结束<input aria-label="服务结束日期" type="date" value={end} onChange={e=>setEnd(e.target.value)}/></label></div></details><div className="ws-actions"><button className="btn primary" disabled={busy||!text.trim()} onClick={()=>void check()}>{busy?'处理中…':'检查当前正文'}</button><button className="btn ghost" disabled={busy} onClick={()=>void save()}>保存项目正文</button>{dirty&&<small className="ws-subtle">有未保存修改</small>}</div>{message&&<p role="status" className="ws-note">{message}</p>}{result&&<><h3>检查结果</h3><p className="ws-subtle">{issues.filter(i=>i.severity==='error').length} 项问题 · {issues.filter(i=>i.severity==='warning').length} 项提醒 · {issues.filter(i=>i.severity==='notice').length} 项待核实</p>{!issues.length?<p className="ws-note">当前规则范围内未发现问题，请继续核对签章、附件与最终提交版本。</p>:issues.map((issue,i)=><div className="ws-task" key={i}><StatusPill severity={issue.severity==='error'?'critical':issue.severity==='warning'?'warning':'info'}>{issue.severity==='error'?'问题':issue.severity==='warning'?'提醒':'待核实'}</StatusPill><div><b>{plain(issue.message||issue.text||issue.rule||'需核实')}</b>{issue.location&&!/^meta|入参/.test(issue.location)&&<small>{issue.location}</small>}</div></div>)}</>}</>
}
export function DocumentPage(){return <Workbench title="标书制作" subtitle="项目资料随手查，真实正文随时检查，正式文档继续在 Word 中完成。">{p=><><ProjectSummary project={p}/><DocumentTools project={p}/><SourceDetails project={p}/></>}</Workbench>}
