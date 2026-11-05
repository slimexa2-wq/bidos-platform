import type { SourcePointer } from '../types/domain'

/**
 * 从指针原文里兜底提取公告原文链接。
 * 盲投源表的「招标公告网址」记录在 original_text（形如 "招标公告网址=https://..."），
 * 而指针自身的 url/deepLink 字段为空——不提取的话"跳转原文"永远置灰。
 */
export function originalUrlOf(source: SourcePointer): string | undefined {
  if (source.deepLink || source.url) return (source.deepLink || source.url) as string
  // 后端直接提取的原文链接（列表不带 originalText 减体积）
  const ou = (source as unknown as { originalUrl?: string }).originalUrl
  if (ou) return ou
  // 兜底：从 originalText 提取
  const raw = (source as unknown as { originalText?: string }).originalText || ''
  const m = raw.match(/(?:招标公告网址|公告网址|原文链接)[=：:]\s*(https?:\/\/[^\s|]+)/)
  return m ? m[1] : undefined
}

function labelFor(source:SourcePointer){
  if(source.label) return source.label
  if(source.type==='official') return '原公告'
  if(source.type==='sheet') return '源表位置'
  if(source.type==='file') return '原文件'
  return '来源'
}

export function SourceActions({sources,compact=false}:{sources:SourcePointer[],compact?:boolean}){
  return <div className="source-actions">{sources.slice(0,compact?2:4).map(s=>{
    const href=originalUrlOf(s)
    const text=labelFor(s)
    return href
      ? <a key={s.id} className="btn ghost" href={href} target="_blank" rel="noreferrer">{text} ↗</a>
      : <button key={s.id} className="btn ghost" disabled title="当前来源暂不支持深链接">{text}</button>
  })}</div>
}
