import { useState } from 'react'
import { PageHeader } from '../../components/PageHeader'
import { StatusPill } from '../../components/StatusPill'
import { workspaceRequest } from '../../services/workspace'
import '../WorkspaceUI.css'

type BriefResult = {
  budget: string | null;
  bid_bond: string | null;
  risks: string[];
  qualifications: string[];
}

export function DecisionCard() {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<BriefResult | null>(null)
  const [error, setError] = useState('')

  const generate = async () => {
    if (!text.trim()) return
    setBusy(true)
    setError('')
    try {
      const data = await workspaceRequest<BriefResult>('/api/documents/decision-brief', { content: text })
      setResult(data)
    } catch(e) {
      setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="page ws-page">
      <PageHeader title="一页式极简投标决策卡" subtitle="拖入或粘贴招标书即刻输出风险、证照与报价参考，辅助快速决策。" />

      <div className="ws-split" style={{ gridTemplateColumns: '1fr 1fr' }}>
        {/* Left side: Input */}
        <div className="panel ws-work">
          <h3>招标文件输入</h3>
          <p className="ws-subtle">直接粘贴招标文件长文本即可进行分析（无需填写配置）。</p>
          <textarea
            aria-label="招标文件内容"
            disabled={busy}
            rows={20}
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder="在此粘贴招标文件（招标公告、评分标准、投标人须知等）..."
            style={{ width: '100%', resize: 'vertical', padding: '12px', boxSizing: 'border-box', marginBottom: '14px' }}
          />
          <div className="ws-actions">
            <button className="btn primary" disabled={busy || !text.trim()} onClick={() => void generate()}>
              {busy ? '智能分析中…' : '一键生成决策摘要'}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => { setText(''); setResult(null); setError('') }}>清空</button>
          </div>
          {error && <p className="ws-error">{error}</p>}
        </div>

        {/* Right side: Output (Decision Brief) */}
        <div className="panel ws-work">
          <h3>决策卡片摘要</h3>

          {!result && !busy && (
            <div className="ws-empty">
              <p>请在左侧输入招标文件以生成决策摘要。</p>
            </div>
          )}

          {busy && (
            <div className="ws-empty">
              <p>正在粉碎解析文件并提取关键信息...</p>
            </div>
          )}

          {result && !busy && (
            <div className="decision-brief-result">
              <div className="ws-fields" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
                <div style={{ background: '#f3f6fa', padding: '16px', borderRadius: '10px' }}>
                  <dt style={{ fontWeight: 'bold', marginBottom: '8px' }}>💰 预算 / 限价</dt>
                  <dd style={{ fontSize: '18px', color: 'var(--blue)' }}>{result.budget || <span className="ws-subtle">未解析到明确金额</span>}</dd>
                </div>
                <div style={{ background: '#f3f6fa', padding: '16px', borderRadius: '10px' }}>
                  <dt style={{ fontWeight: 'bold', marginBottom: '8px' }}>💳 投标保证金</dt>
                  <dd style={{ fontSize: '18px', color: 'var(--blue)' }}>{result.bid_bond || <span className="ws-subtle">无需缴纳或未提及</span>}</dd>
                </div>
              </div>

              <div style={{ marginTop: '24px' }}>
                <h4 style={{ borderBottom: '1px solid var(--line)', paddingBottom: '8px', marginBottom: '12px' }}>🚨 废标与风险条款扫描</h4>
                {result.risks.length > 0 ? (
                  <ul style={{ paddingLeft: '20px', lineHeight: '1.6' }}>
                    {result.risks.map((r, i) => (
                      <li key={i} style={{ marginBottom: '8px' }}>
                        <StatusPill severity="critical">风险</StatusPill> {r}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="ws-note ws-success">✅ 未扫描到常见废标风险项。</p>
                )}
              </div>

              <div style={{ marginTop: '24px' }}>
                <h4 style={{ borderBottom: '1px solid var(--line)', paddingBottom: '8px', marginBottom: '12px' }}>📋 核心资质与证书匹配</h4>
                {result.qualifications.length > 0 ? (
                  <ul style={{ paddingLeft: '20px', lineHeight: '1.6' }}>
                    {result.qualifications.map((q, i) => (
                      <li key={i} style={{ marginBottom: '8px' }}>
                        <StatusPill severity="info">要求</StatusPill> {q}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="ws-note">ℹ️ 未提取到特定的资格证书要求。</p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
