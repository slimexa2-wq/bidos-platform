import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { provider } from '../services'
import type { Opportunity } from '../types/domain'

type AppState = {
  followed: Set<string>
  verified: Set<string>
  handledAlerts: Set<string>
  /** 未处理提醒数（真实来自 /api/alerts，不再写死）。 */
  alertCount: number
  /** 项目目录加载失败原因。null 表示正常；非 null 时界面必须可见地提示。 */
  catalogError: string | null
  toggleFollow:(id:string)=>void
  verify:(id:string)=>void
  handleAlert:(id:string)=>void
  projectName:(id:string)=>string
}
const Ctx = createContext<AppState | null>(null)
export function AppProvider({children}:{children:ReactNode}){
  // 质检修复：项目名/关注/核验不再来自 mock 种子——真库加载，加载前如实回落。
  const [catalog,setCatalog] = useState<Opportunity[]>([])
  const [followed,setFollowed] = useState(new Set<string>())
  const [verified,setVerified] = useState(new Set<string>())
  const [handledAlerts,setHandledAlerts] = useState(new Set<string>())
  const [alertCount,setAlertCount] = useState(0)
  const [catalogError,setCatalogError] = useState<string|null>(null)

  useEffect(()=>{
    // 失败可见：此前是 `.catch(()=>{})` **静默吞错**，接口挂了界面上完全看不出来，
    // 表现为"项目名全变成『相关项目』"却毫无提示。现在把原因存起来供界面展示。
    provider.getOpportunities()
      .then((d)=>{ setCatalog(d); setCatalogError(null) })
      .catch((e)=>setCatalogError(String(e?.message || e)))
  },[])

  useEffect(()=>{
    // 侧边栏提醒角标此前写死为 4。改为读真实**未解决**提醒数。
    // 注意口径：本系统铁律是「已读 ≠ 已解决」，所以角标只统计 resolved !== true，
    // 不能统计 handled（否则用户点一下"已读"角标就清零，而问题还在）。
    provider.getAlerts()
      .then((rows)=>setAlertCount(rows.filter((a)=>a.resolved !== true).length))
      .catch(()=>setAlertCount(0))
  },[])

  const value = useMemo<AppState>(()=>({
    followed, verified, handledAlerts, alertCount, catalogError,
    toggleFollow:(id)=>setFollowed(s=>{const n=new Set(s); n.has(id)?n.delete(id):n.add(id); return n}),
    verify:(id)=>setVerified(s=>new Set(s).add(id)),
    handleAlert:(id)=>setHandledAlerts(s=>new Set(s).add(id)),
    projectName:(id)=>catalog.find(x=>x.id===id)?.name || '相关项目'
  }),[followed,verified,handledAlerts,alertCount,catalogError,catalog])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
export const useApp = () => { const v=useContext(Ctx); if(!v) throw new Error('AppProvider missing'); return v }
