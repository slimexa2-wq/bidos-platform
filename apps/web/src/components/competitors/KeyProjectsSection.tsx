import { originalUrlOf } from '../SourceActions'
import type { SourcePointer } from '../../types/domain'
type SourcePointerLite = Partial<SourcePointer> & { fileName?: string; sheetName?: string; rowId?: string | number }
import { useCallback, useEffect, useState } from 'react'
import { provider } from '../../services'

/**
 * 「重点项目监控」区块：页面加载时调 provider.getKeyProjects() 渲染真实列表，
 * 提供「重新扫描」按钮（调 provider.scanKeyProjects() 后刷新列表）。
 *
 * 字段口径（2026-09-28 用 curl 对真实后端实测）：
 *   GET  /api/key-projects
 *        → { total, projects[{ projectId, name, clientName, kind,
 *                              projectEndAt, winner, result }] }
 *        kind 实测取值：'competitor_expiring'（竞对即将到期）、'our_history'（我司历史项目）
 *   POST /api/key-projects/scan
 *        → { keyProjects, announcementsScanned, matches, matchCount,
 *            eventsEmitted, expiryResolved, skippedDuplicates, errors }
 * 缺失值显示「—」，接口失败时原样展示错误信息，绝不编造。
 * 表头对齐源表《（汇总）主要竞争对手 投标项目统计》逐字用词（甲方名称/项目类型/中标单位），
 * 接口字段名不变，仅改表头文字与列→字段映射关系。
 */

const MISSING = '—'

type KeyProject = {
  projectId: string
  name: string
  clientName: string
  kind: string
  projectEndAt: string | null
  winner: string | null
  result: string | null
  sources?: Array<Record<string, unknown>>
}

type ScanSummary = {
  announcementsScanned: number | null
  matchCount: number | null
  eventsEmitted: number | null
  expiryResolved: number | null
  skippedDuplicates: number | null
  errors: string[]
}

/** kind → 展示标签（未知值原样展示，不猜）。 */
const KIND_LABELS: Record<string, string> = {
  competitor_expiring: '竞对即将到期',
  our_history: '我司历史项目',
}

function asRecord(v: unknown): Record<string, unknown> {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v as Record<string, unknown>
  throw new Error('重点项目接口返回了非对象结构，无法解析')
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function parseProjects(raw: unknown): { total: number; projects: KeyProject[] } {
  const d = asRecord(raw)
  const rows = Array.isArray(d.projects) ? d.projects : []
  return {
    total: typeof d.total === 'number' ? d.total : rows.length,
    projects: rows.flatMap((p) => {
      if (!p || typeof p !== 'object' || Array.isArray(p)) return []
      const o = p as Record<string, unknown>
      return [{
        projectId: str(o.projectId),
        name: str(o.name) || str(o.projectId) || MISSING,
        clientName: str(o.clientName) || MISSING,
        kind: str(o.kind),
        projectEndAt: str(o.projectEndAt) || null,
        winner: str(o.winner) || null,
        result: str(o.result) || null,
        sources: Array.isArray(o.sources) ? (o.sources as Array<Record<string, unknown>>) : [],
      }]
    }),
  }
}

function parseScan(raw: unknown): ScanSummary {
  const d = asRecord(raw)
  return {
    announcementsScanned: numOrNull(d.announcementsScanned),
    matchCount: numOrNull(d.matchCount),
    eventsEmitted: numOrNull(d.eventsEmitted),
    expiryResolved: numOrNull(d.expiryResolved),
    skippedDuplicates: numOrNull(d.skippedDuplicates),
    errors: Array.isArray(d.errors) ? d.errors.filter((x): x is string => typeof x === 'string') : [],
  }
}

function fmtDate(iso: string | null): string {
  return iso ? iso.slice(0, 10) : MISSING
}

function fmtNum(v: number | null): string {
  return v === null ? MISSING : String(v)
}

export function KeyProjectsSection() {
  const [data, setData] = useState<{ total: number; projects: KeyProject[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)
  const [scanSummary, setScanSummary] = useState<ScanSummary | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)

  const load = useCallback(() => {
    provider
      .getKeyProjects()
      .then((raw) => {
        setData(parseProjects(raw))
        setError(null)
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e))
      })
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const rescan = () => {
    setScanning(true)
    setScanError(null)
    setScanSummary(null)
    provider
      .scanKeyProjects()
      .then((raw) => {
        setScanSummary(parseScan(raw))
        load()
      })
      .catch((e: unknown) => {
        setScanError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => setScanning(false))
  }

  return (
    <div className="panel" style={{ marginTop: 16, padding: 14 }}>
      <div className="panel-title">
        <div>
          <h2>重点项目监控</h2>
          <p>
            {error
              ? '监控列表来自后端扫描任务。'
              : data
                ? `共 ${data.total} 个项目（竞对即将到期 / 我司历史项目）。`
                : '加载中…'}
          </p>
        </div>
        <button className="btn ghost" onClick={rescan} disabled={scanning}>
          {scanning ? '扫描中…' : '重新扫描'}
        </button>
      </div>

      {error && (
        <div className="callout warning">
          <p>重点项目加载失败：{error}</p>
        </div>
      )}
      {scanError && (
        <div className="callout warning">
          <p>重新扫描失败：{scanError}</p>
        </div>
      )}
      {scanSummary && (
        <div className="callout info">
          <p>
            本次扫描：公告 {fmtNum(scanSummary.announcementsScanned)} 条，
            命中 {fmtNum(scanSummary.matchCount)} 条，
            生成事件 {fmtNum(scanSummary.eventsEmitted)} 条，
            解析到期 {fmtNum(scanSummary.expiryResolved)} 条，
            跳过重复 {fmtNum(scanSummary.skippedDuplicates)} 条。
            {scanSummary.errors.length > 0 && ` 错误 ${scanSummary.errors.length} 条：${scanSummary.errors.join('；')}`}
          </p>
        </div>
      )}

      {!error && !data && <p className="muted">正在加载重点项目…</p>}
      {data && data.projects.length === 0 && <p className="muted">暂无数据。</p>}
      {data && data.projects.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {/* 表头对齐源表《（汇总）主要竞争对手 投标项目统计》逐字用词；
                    数据字段名不变：甲方名称←clientName、项目类型←kind、中标单位←winner、到期日期←projectEndAt */}
                <th>项目名称</th><th>甲方名称</th><th>项目类型</th>
                <th>到期日期</th><th>中标单位</th><th>结果</th><th>溯源</th>
              </tr>
            </thead>
            <tbody>
              {data.projects.map((p) => (
                <tr key={p.projectId || p.name}>
                  <td><b>{p.name}</b></td>
                  <td>{p.clientName}</td>
                  <td>{KIND_LABELS[p.kind] ?? p.kind}</td>
                  <td>{fmtDate(p.projectEndAt)}</td>
                  <td>{p.winner ?? MISSING}</td>
                  <td>{p.result ?? MISSING}</td><td>{(()=>{const srcs=(p.sources||[]) as SourcePointerLite[];if(!srcs.length)return <span>—</span>;const u=originalUrlOf(srcs[0] as never);return u? <a href={u} target="_blank" rel="noreferrer" title="跳转原文/源表">原文 ↗</a> : <span title={`${srcs[0].fileName||""} ${srcs[0].sheetName||""} 第${srcs[0].rowId||"?"}行`}>源表</span>;})()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
