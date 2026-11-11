import { useEffect, useState } from 'react'
import { Workbench, ProjectSummary, SourceDetails, WorkRecord, DraftNotice } from './WorkspaceUI'
import { useProjectDraft } from '../services/projectDraft'
import { saveProjectWork, workSavedMessage, workspaceRequest, type WorkspaceProject, type WorkMaterial } from '../services/workspace'
function Materials({ project:p }: { project:WorkspaceProject }) {
  const fromProject = (work = p.work) => ({ baseline: work, materials: work?.materials || [] as WorkMaterial[], label: '', notice: '', dirty: false })
  const draft = useProjectDraft(`${p.id}:materials`, fromProject())
  const { baseline, materials, label, notice, dirty } = draft.value
  const setMaterials = (fn: (items: WorkMaterial[]) => WorkMaterial[]) => draft.update(previous => ({ ...previous, materials: fn(previous.materials), dirty: true }))
  const setLabel = (label: string) => draft.update({ label, dirty: true })
  const setNotice = (notice: string) => draft.update({ notice, dirty: true })
  const setDirty = (dirty: boolean) => draft.update({ dirty })
  const [message,setMessage]=useState('')
  const [busy,setBusy]=useState(false)
  useEffect(()=>{if(!dirty)draft.reset(fromProject())},[p.work,dirty,draft.reset])
  const add=()=>{if(!label.trim())return;setMaterials(xs=>[...xs,{id:`manual_${Date.now()}`,label:label.trim(),done:false}]);setLabel('');setDirty(true);setMessage('有未保存修改')}
  const identify=async()=>{if(!notice.trim())return;setBusy(true);try{const d=await workspaceRequest<{materials?:{id:string;label:string;status:string}[];unknown_requirements?:string[]}>('/api/registrations/identify-materials',{announcement_text:notice});const parsed=(d.materials || []).filter(m=>m.status==='ready'||m.status==='missing');if(parsed.length)setDirty(true);setMaterials(xs=>[...xs,...parsed.filter(m=>!xs.some(x=>x.label===m.label)).map(m=>({id:m.id,label:m.label,done:false}))]);setMessage(parsed.length?`识别出 ${parsed.length} 项，请对照原文确认后保存。${d.unknown_requirements?.length?'另有要求需要人工阅读：'+d.unknown_requirements.join('；'):''}`:'这段原文未识别出明确材料要求，可手动添加。')}catch(e){setMessage(e instanceof Error?e.message:'识别失败')}finally{setBusy(false)}}
  const save=async()=>{
    const revision=draft.getRevision()
    setBusy(true)
    try{
      const saved=await saveProjectWork(p,{materials},baseline)
      const replaced=label || notice ? draft.update({ ...fromProject(saved), label, notice, dirty: true },revision) : draft.reset(fromProject(saved),revision)
      if(!replaced)draft.update(previous=>previous.baseline?.version===baseline?.version&&previous.baseline?.digest===baseline?.digest?{...previous,baseline:saved}:previous)
      setMessage(workSavedMessage(saved))
    }catch(e){setMessage(e instanceof Error?e.message:'保存失败')}finally{setBusy(false)}
  }
  return <><DraftNotice dirty={dirty} discard={()=>draft.reset(fromProject())}/><h3>报名材料 <span className="ws-subtle">已备齐 {materials.filter(m=>m.done).length} / {materials.length} 项</span></h3>{!materials.length&&<p className="ws-subtle">按招标原文添加实际需要的材料，勾选后保存即可保留准备进度。</p>}<div className="ws-materials">{materials.map(m=><label key={m.id}><input type="checkbox" disabled={busy} checked={m.done} onChange={e=>{setMaterials(xs=>xs.map(x=>x.id===m.id?{...x,done:e.target.checked}:x));setDirty(true);setMessage('有未保存修改')}} /><span>{m.label}</span><button disabled={busy} aria-label={`移除${m.label}`} onClick={()=>{setMaterials(xs=>xs.filter(x=>x.id!==m.id));setDirty(true);setMessage('有未保存修改')}}>×</button></label>)}</div><div className="ws-toolbar"><input style={{flex:1}} aria-label="材料名称" value={label} onChange={e=>setLabel(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')add()}} placeholder="材料名称，例如：报名授权委托书" /><button className="btn ghost" onClick={add} disabled={busy||!label.trim()}>添加</button><button className="btn primary" disabled={busy} onClick={()=>void save()}>{busy?'处理中…':'保存清单'}</button></div><details className="ws-source"><summary>从公告正文识别材料要求</summary><p className="ws-subtle">粘贴公告的报名要求，系统提取可能需要的材料；请核对原文后保存。</p><textarea rows={5} aria-label="报名要求原文" value={notice} onChange={e=>setNotice(e.target.value)} placeholder="粘贴真实报名要求…" /><div className="ws-actions"><button className="btn ghost" disabled={busy||!notice.trim()} onClick={()=>void identify()}>识别材料要求</button></div></details>{message&&<p className="ws-note" role="status">{message}</p>}</>
}
export function RegistrationPage(){return <Workbench title="报名管理" subtitle="同一份项目资料，集中查看负责人、计划和报名准备。">{p=><><ProjectSummary project={p}/><Materials project={p}/><WorkRecord project={p}/><SourceDetails project={p}/></>}</Workbench>}
