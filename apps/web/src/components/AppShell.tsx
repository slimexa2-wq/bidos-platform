import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useState, type FormEvent } from 'react'
import { useWorkspace } from '../services/workspace'

const nav=[['首页','/'],['盲投信息','/blind'],['报名管理','/registration'],['摘要评估','/evaluation'],['标书制作','/document'],['结果复盘','/results'],['保证金台账','/bid-bonds'],['竞对分析','/competitors']]

export function AppShell(){
  const navg=useNavigate(); const [q,setQ]=useState('')
  // 体检修复：角标此前硬编码为 4，与真实提醒数无关。改为读真实未解决数。
  const workspace = useWorkspace()
  const alertCount = workspace.data?.tasks.length || 0
  const submit=(e:FormEvent)=>{e.preventDefault(); if(q.trim()) navg(`/search?q=${encodeURIComponent(q.trim())}`)}
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="logo">B</span><div><b>BidOS</b><small>招投标智能工作平台</small></div></div>
      <nav>{nav.map(([label,to])=><NavLink key={to} to={to} end={to==='/'} className={({isActive})=>isActive?'active':''}>{label}</NavLink>)}</nav>
      <div className="side-bottom">
        <NavLink to="/search">全局搜索</NavLink>
        <NavLink to="/alerts">提醒{alertCount>0 && <span className="badge">{alertCount>999?'999+':alertCount}</span>}</NavLink>
        <NavLink to="/ops">数据与自动化</NavLink>
      </div>
    </aside>
    <main className="main">
      <header className="topbar"><form onSubmit={submit}><span>⌕</span><input value={q} onChange={e=>setQ(e.target.value)} placeholder="搜索项目、甲方、负责人或源表内容…"/></form><NavLink className="icon-btn" to="/alerts">🔔</NavLink><div className="avatar">张</div></header>
      <Outlet/>
    </main>
    {/* §9.1：PC 与手机同一套能力。禁止 slice(0,5)——那会让结果复盘/保证金台账/竞对分析
        在手机上无任何入口（sidebar 在 ≤900px 被 display:none）。改为横向可滚动的完整导航。 */}
    <nav className="mobile-nav">{nav.map(([label,to])=><NavLink key={to} to={to} end={to==='/'}>{label.replace('管理','').replace('信息','')}</NavLink>)}</nav>
  </div>
}
