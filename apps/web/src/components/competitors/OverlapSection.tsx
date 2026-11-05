import { useEffect, useState } from 'react'
import { provider } from '../../services'
import { StatusPill } from '../StatusPill'

/**
 * 「项目重合分析」区块：由竞对详情侧栏的「重合分析」按钮触发，
 * 调用 provider.getCompetitorOverlap(选中竞对名) 渲染确定性重合计算结果。
 *
 * 字段口径（2026-09-28 用 curl 对真实后端实测 GET /api/competitors/{name}/overlap）：
 *   competitor / matched_entity / entity_id / aliases /
 *   sample_sizes { competitor_won, our_participated, overlap } / overlap_count /
 *   regions {地区: 项目数} / clients {客户: 项目数} / notices[]（数据质量说明）/
 *   projects[{ project_id, project_name, region, client_name, project_type,
 *              our_result, competitor, our_price, winning_price,
 *              price_diff, price_ratio }]
 * 缺失值一律显示「—」，绝不编造；接口失败时原样展示错误信息。
 */

const MISSING = '—'

type OverlapProject = {
  projectId: string
  projectName: string
  region: string | null
  clientName: string
  projectType: string
  ourResult: string
  ourPrice: number | null
  winningPrice: number | null
  priceRatio: number | null
}

type OverlapData = {
  competitor: string
  matchedEntity: boolean
  entityId: string | null
  aliases: string[]
  overlapCount: number
  competitorWon: number | null
  ourParticipated: number | null
  regions: [string, number][]
  clients: [string, number][]
  notices: string[]
  projects: OverlapProject[]
}

type Phase =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'done'; data: OverlapData }

/** 后端响应必须是对象，否则视为格式异常（交给错误分支，不静默吞掉）。 */
function asRecord(v: unknown): Record<string, unknown> {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v as Record<string, unknown>
  throw new Error('重合分析接口返回了非对象结构，无法解析')
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** {地区: 数量} → 按数量降序的键值对；非数字计数丢弃（不猜 0）。 */
function countEntries(v: unknown): [string, number][] {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return []
  return Object.entries(v as Record<string, unknown>)
    .filter((entry): entry is [string, number] => typeof entry[1] === 'number')
    .sort((a, b) => b[1] - a[1])
}

function parseOverlap(raw: unknown): OverlapData {
  const d = asRecord(raw)
  const sizes = asRecord(d.sample_sizes ?? {})
  const rawProjects = Array.isArray(d.projects) ? d.projects : []
  return {
    competitor: str(d.competitor),
    matchedEntity: d.matched_entity === true,
    entityId: str(d.entity_id) || null,
    aliases: Array.isArray(d.aliases) ? d.aliases.filter((x): x is string => typeof x === 'string') : [],
    overlapCount: typeof d.overlap_count === 'number' ? d.overlap_count : 0,
    competitorWon: numOrNull(sizes.competitor_won),
    ourParticipated: numOrNull(sizes.our_participated),
    regions: countEntries(d.regions),
    clients: countEntries(d.clients),
    notices: Array.isArray(d.notices) ? d.notices.filter((x): x is string => typeof x === 'string') : [],
    projects: rawProjects.flatMap((p) => {
      if (!p || typeof p !== 'object' || Array.isArray(p)) return []
      const o = p as Record<string, unknown>
      return [{
        projectId: str(o.project_id),
        projectName: str(o.project_name) || str(o.project_id) || MISSING,
        region: str(o.region) || null,
        clientName: str(o.client_name) || MISSING,
        projectType: str(o.project_type) || MISSING,
        ourResult: str(o.our_result) || MISSING,
        ourPrice: numOrNull(o.our_price),
        winningPrice: numOrNull(o.winning_price),
        priceRatio: numOrNull(o.price_ratio),
      }]
    }),
  }
}

function fmtNum(v: number | null): string {
  return v === null ? MISSING : String(v)
}

/** 区域 / 客户分布小表。为空时如实显示「暂无数据」。 */
function CountTable({ title, entries }: { title: string; entries: [string, number][] }) {
  return (
    <div>
      <h3 style={{ margin: '12px 0 8px' }}>{title}</h3>
      {entries.length === 0 ? (
        <p className="muted">暂无数据。</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>{title.replace(/分布$/, '')}</th><th>重合项目数</th></tr>
            </thead>
            <tbody>
              {entries.map(([k, n]) => (
                <tr key={k}><td>{k}</td><td>{n}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function OverlapBody({ data }: { data: OverlapData }) {
  const hasProjects = data.projects.length > 0
  return (
    <>
      <div className="kv-grid">
        <div><span>重合项目数</span><b>{data.overlapCount}</b></div>
        <div><span>竞对中标样本</span><b>{fmtNum(data.competitorWon)}</b></div>
        <div><span>我司参与项目样本</span><b>{fmtNum(data.ourParticipated)}</b></div>
        <div>
          <span>实体匹配</span>
          <b>{data.matchedEntity ? '已匹配' : '未匹配'}</b>
          {data.entityId && <small className="muted" style={{ display: 'block', marginTop: 4 }}>{data.entityId}</small>}
        </div>
      </div>
      {data.aliases.length > 0 && (
        <p className="muted" style={{ margin: '0 0 10px', fontSize: 13 }}>
          名称别名：{data.aliases.join('、')}
        </p>
      )}
      {data.notices.length > 0 && (
        <div className="callout warning">
          {data.notices.map((n, i) => <p key={i}>{n}</p>)}
        </div>
      )}
      {!hasProjects && <p className="muted">暂无重合项目。</p>}
      {hasProjects && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
            <CountTable title="区域分布" entries={data.regions} />
            <CountTable title="客户分布" entries={data.clients} />
          </div>
          <h3 style={{ margin: '14px 0 8px' }}>重合项目明细（{data.projects.length} 个）</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>项目名称</th><th>客户</th><th>类型</th><th>区域</th>
                  <th>我司结果</th><th>我司报价</th><th>中标价</th><th>报价比</th>
                </tr>
              </thead>
              <tbody>
                {data.projects.map((p) => (
                  <tr key={p.projectId || p.projectName}>
                    <td><b>{p.projectName}</b></td>
                    <td>{p.clientName}</td>
                    <td>{p.projectType}</td>
                    <td>{p.region ?? MISSING}</td>
                    <td>
                      <StatusPill severity={p.ourResult === '中标' ? 'success' : 'neutral'}>
                        {p.ourResult}
                      </StatusPill>
                    </td>
                    <td>{fmtNum(p.ourPrice)}</td>
                    <td>{fmtNum(p.winningPrice)}</td>
                    <td>{fmtNum(p.priceRatio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}

export function OverlapSection({ name, nonce, onClose }: { name: string; nonce: number; onClose: () => void }) {
  const [state, setState] = useState<Phase>({ phase: 'loading' })
  useEffect(() => {
    let alive = true
    setState({ phase: 'loading' })
    provider
      .getCompetitorOverlap(name)
      .then((raw) => {
        if (alive) setState({ phase: 'done', data: parseOverlap(raw) })
      })
      .catch((e: unknown) => {
        if (alive) setState({ phase: 'error', message: e instanceof Error ? e.message : String(e) })
      })
    return () => {
      alive = false
    }
  }, [name, nonce])

  return (
    <div className="panel" style={{ marginTop: 16, padding: 14 }}>
      <div className="panel-title">
        <div>
          <h2>项目重合分析 · {name}</h2>
          <p>该竞对与我司投标记录的确定性重合计算（实时接口返回）。</p>
        </div>
        <button className="btn ghost" onClick={onClose}>关闭</button>
      </div>
      {state.phase === 'loading' && <p className="muted">正在加载重合分析…</p>}
      {state.phase === 'error' && (
        <div className="callout warning">
          <p>重合分析加载失败：{state.message}</p>
        </div>
      )}
      {state.phase === 'done' && <OverlapBody data={state.data} />}
    </div>
  )
}
