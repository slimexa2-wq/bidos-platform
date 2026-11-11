import { useEffect, useState } from 'react'
import { localToday, type SheetLocation, type SourceDataRow } from './portfolio'
export type WorkMaterial = { id: string; label: string; done: boolean }
export type ProjectWork = { version: number; digest: string; sync: string; cacheWarning?: string; owner?: string; notes: string; decision: string; materials: WorkMaterial[]; documentText: string; attachments: unknown[]; updatedAt: string; nextAction?: string; dueAt?: string }
export type WorkspaceProject = {
  id: string; year?:string; blindId?: string; blindPath?: string; name: string; client: string; channel: string; partStatus: string; resultStatus: string;
  deadline: string; openingAt: string; owner: string; progress: string; plan: string; priority: string; limit: string; url: string;
  source?: SheetLocation; sources: SheetLocation[]; sourceRows: SourceDataRow[]; fields: Record<string, string>;
  work: ProjectWork; hasPlan: boolean; isHistorical: boolean; planHistory?: SourceDataRow[];
}
export type WorkspaceTask = { id: string; kind: string; title: string; detail: string; severity: string; at: string; path: string; projectId?: string }
export type SourceDocument = { id:string; title:string; kind:string; empty:boolean; source:SheetLocation; cells:{cell:string;value:string}[] }
export type WorkspaceData = { updatedAt: string; snapshot?: {generatedAt:string; intervalMinutes:number}; projects: WorkspaceProject[]; tasks: WorkspaceTask[]; planHistory: SourceDataRow[]; sourceDocuments?:SourceDocument[]; notes: string[] }
let snapshot: WorkspaceData | null = null
let lastError = ''
let running: Promise<WorkspaceData> | null = null
let checkedAt = 0
const subscribers = new Set<() => void>()
const publish = () => subscribers.forEach(fn => fn())
export async function workspaceRequest<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, body === undefined ? { signal: AbortSignal.timeout(45000) } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : res.status >= 500 ? '数据服务暂时不可用，请稍后重试' : `操作失败 (${res.status})`)
  return data as T
}
export function refreshWorkspace(force = true): Promise<WorkspaceData> {
  if (running) return running
  running = workspaceRequest<WorkspaceData>(`/api/workspace${force ? '?refresh=true' : ''}`).then(data => {
    snapshot = data; lastError = ''; checkedAt = Date.now(); return data
  }).catch(e => { lastError = e instanceof DOMException && (e.name === 'TimeoutError' || e.name === 'AbortError') ? '读取超时，请点击重试' : e instanceof Error ? e.message : '读取失败'; throw e }).finally(() => { running = null; publish() })
  publish()
  return running
}
export function useWorkspace() {
  const [, tick] = useState(0)
  useEffect(() => {
    const update = () => tick(n => n + 1)
    subscribers.add(update)
    if (!snapshot || Date.now() - checkedAt > 60000) void refreshWorkspace(false).catch(() => {})
    const timer = window.setInterval(() => { if (Date.now() - checkedAt > 60000) void refreshWorkspace(false).catch(() => {}) }, 60000)
    return () => { subscribers.delete(update); window.clearInterval(timer) }
  }, [])
  return { data: snapshot, error: lastError, loading: !snapshot && !lastError, refreshing: !!running, refresh: refreshWorkspace }
}
export async function saveProjectWork(project: WorkspaceProject, changes: Partial<ProjectWork>, baseline?: ProjectWork) {
  const conflict = baseline && Object.keys(changes).some(key => JSON.stringify(project.work?.[key as keyof ProjectWork]) !== JSON.stringify(baseline[key as keyof ProjectWork]))
  const base = conflict ? baseline : project.work
  const baseVersion = base?.version || 0
  const baseDigest = base?.digest || ''
  const work = await workspaceRequest<ProjectWork>(`/api/workspace/projects/${encodeURIComponent(project.id)}/work`, { baseVersion, baseDigest, ...changes })
  if (snapshot) snapshot = { ...snapshot, projects: snapshot.projects.map(p => p.id === project.id && work.version >= (p.work?.version || 0) ? { ...p, work } : p) }
  publish()
  void refreshWorkspace().catch(() => {})
  return work
}
export const workSavedMessage = (work: ProjectWork) => [work.sync || '工作记录已保存', work.cacheWarning].filter(Boolean).join(' · ')
export function nextWorkDate(p: WorkspaceProject) {
  if (p.work?.dueAt && p.work.nextAction) return { label: '跟进', value:p.work.dueAt }
  const dates=[{label:'报名截止',value:p.deadline},{label:'开标',value:p.openingAt}].filter(d=>d.value && d.value.slice(0,10)>=localToday()).sort((a,b)=>a.value.localeCompare(b.value))
  return dates[0] || {label:'',value:''}
}
export const workProjects = (rows: WorkspaceProject[], history = false, includeAssessment = false) => rows.filter(p => (p.hasPlan || p.partStatus === '报名参与' || !!p.work?.updatedAt || (includeAssessment && p.partStatus === '待评估')) && (history || !p.isHistorical)).sort((a,b)=> {
  const assessmentOrder = includeAssessment ? Number(b.partStatus === '待评估') - Number(a.partStatus === '待评估') : 0
  return assessmentOrder || (nextWorkDate(a).value||'9999').localeCompare(nextWorkDate(b).value||'9999')
})
export const searchProjects = (rows: WorkspaceProject[], query: string) => {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  return rows.filter(p => { const hay = [p.name, p.client, p.owner, p.channel, p.partStatus, p.resultStatus, ...Object.values(p.fields || {}), ...p.sourceRows.flatMap(s => Object.values(s.fields || {}))].join(' ').toLowerCase(); return words.every(w => hay.includes(w)) })
}
