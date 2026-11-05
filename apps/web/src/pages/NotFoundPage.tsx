import { Link, useLocation } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'

/**
 * 404 兜底页。
 *
 * 存在的理由（2026-09-24 体检）：`App.tsx` 此前**没有 `path="*"`**，
 * 路径无匹配时 react-router 连无路径的布局路由 `<AppShell/>` 都不渲染，
 * 结果是**整页纯白、连侧边栏都没有**——用户因此投诉"全是空白/花架子"。
 * 现在所有未知路径都会落到这里，并给出可达的真实入口。
 */

/** 常见误写路径 → 真实路由。用户很自然会写复数形式。 */
const SUGGESTIONS: Array<{ wrong: string; right: string; label: string }> = [
  { wrong: '/registrations', right: '/registration', label: '报名管理' },
  { wrong: '/evaluations', right: '/evaluation', label: '摘要评估' },
  { wrong: '/documents', right: '/document', label: '标书制作' },
  { wrong: '/opportunities', right: '/blind', label: '盲投信息' },
]

const ALL_PAGES: Array<{ path: string; label: string }> = [
  { path: '/', label: '首页概览' },
  { path: '/blind', label: '盲投信息' },
  { path: '/registration', label: '报名管理' },
  { path: '/evaluation', label: '摘要评估' },
  { path: '/document', label: '标书制作' },
  { path: '/results', label: '结果复盘' },
  { path: '/bid-bonds', label: '保证金台账' },
  { path: '/competitors', label: '竞对分析' },
  { path: '/search', label: '智能检索' },
  { path: '/alerts', label: '提醒' },
]

export function NotFoundPage() {
  const { pathname } = useLocation()
  const hit = SUGGESTIONS.find((s) => s.wrong === pathname)

  return (
    <section className="page">
      <PageHeader
        title="页面不存在"
        subtitle={`没有找到与 ${pathname} 匹配的页面。`}
      />
      <div className="panel">
        {hit ? (
          <div className="callout warning">
            <p>
              你访问的 <code>{hit.wrong}</code> 不是本系统的路由（本系统用单数形式）。
              你要找的应该是{' '}
              <Link to={hit.right}>
                <b>{hit.label}</b>（{hit.right}）
              </Link>
              。
            </p>
          </div>
        ) : (
          <p className="muted">
            请从下方入口进入。若你是通过旧链接或收藏访问的，说明该地址已变更。
          </p>
        )}
        <h3>全部可用页面</h3>
        <ul>
          {ALL_PAGES.map((p) => (
            <li key={p.path}>
              <Link to={p.path}>
                {p.label}
              </Link>{' '}
              <code>{p.path}</code>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
