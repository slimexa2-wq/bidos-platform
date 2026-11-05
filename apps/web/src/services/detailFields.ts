import type { CompetitorProject, PortfolioBond, PortfolioResult } from './portfolio'

type SourceFields = Record<string, unknown>
export type DetailSection = { title: string; fields: Record<string, string>; notes?: Record<string, string>; collapsed?: boolean; blankWhenMissing?: string[] }
export type DetailTemplate = readonly { title: string; labels: readonly string[] }[]
type SourceTemplateRecord = {
  sourceFields?: SourceFields; relatedFields?: SourceFields; priceFields?: SourceFields; auxiliaryFields?: SourceFields
  fields?: SourceFields; notes?: Record<string, string>; cellNotes?: Record<string, string>; sourceRows?: { fields?: SourceFields; comments?: Record<string, string> }[]
}

// Keep the mapped template stable while automatically appending any populated source headers not mapped yet.
export function withSourceFields(template: DetailTemplate, records: SourceTemplateRecord[], mappedFields: string[], title = '其他原表字段'): DetailTemplate {
  const existing = new Set([...template.flatMap(section => section.labels), ...mappedFields, '序号', '项目名称', '投递项目名称'])
  const discovered = new Set<string>()
  for (const record of records) {
    const resultNoteKeys = new Set(Object.keys(record.notes || {}))
    const fields = [record.sourceFields, record.fields, record.relatedFields, record.priceFields, record.auxiliaryFields,
      ...(record.sourceRows || []).map(row => row.fields)].filter(Boolean) as SourceFields[]
    for (const source of fields) for (const [key, value] of Object.entries(source)) {
      if (!existing.has(key) && !resultNoteKeys.has(key) && !/^BidOS/i.test(key) && displayValue(value) !== '/') discovered.add(key)
    }
    for (const notes of [record.cellNotes, ...(record.sourceRows || []).map(row => row.comments)]) {
      for (const [key, note] of Object.entries(notes || {})) {
        if (!existing.has(key) && !/^BidOS/i.test(key) && displayValue(note) !== '/') discovered.add(key)
      }
    }
  }
  const labels = [...discovered].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  return labels.length ? [...template, { title, labels }] : template
}

// 仅用于展示，保留源数据和编辑框里的原始空值。
export function displayValue(value: unknown): string {
  if (value === undefined || value === null) return '/'
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return !text.trim() || /^[\/／—–-]+$/.test(text.trim()) ? '/' : text
}

export function sourceValue(fields: SourceFields, ...keys: string[]): string {
  for (const key of keys) {
    const value = fields[key]
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim()
  }
  return ''
}

type PriceToken = { value: number; currency: string; basis: string }
const PRICE_TOKEN = /([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(亿元|万元|万|元)(\s*(?:(?:\/|／|每)\s*(?:人次|人月|人|月|年|小时|天|日|次|岗位|平方米|平米|㎡|m²|吨|份|单))+)?/g

function priceTokens(value: unknown): PriceToken[] {
  if (typeof value === 'number' && Number.isFinite(value)) return [{ value, currency: 'source', basis: '' }]
  const text = String(value ?? '').trim().replace(/^[^\d￥¥]+[:：]?\s*/, '')
  if (!text || /^[\/／—–-]+$/.test(text) || text.startsWith('=')) return []
  const plain = text.match(/^[￥¥]?\s*([0-9][0-9,，]*(?:\.[0-9]+)?)\s*$/)
  if (plain) return [{ value: Number(plain[1].replace(/[,，]/g, '')), currency: 'source', basis: '' }]
  const tokens: PriceToken[] = []
  for (const match of text.matchAll(PRICE_TOKEN)) {
    const raw = Number(match[1].replace(/[,，]/g, ''))
    const currency = match[2]
    const factor = currency === '亿元' ? 100_000_000 : currency === '万元' || currency === '万' ? 10_000 : 1
    const basis = (match[3] || '').replace(/[\s／]/g, '/').replace(/每/g, '/').replace(/\/+/g, '/')
      .replace('/人月', '/人/月').replace('/人年', '/人/年')
    tokens.push({ value: raw * factor, currency, basis })
  }
  return tokens
}

// 仅在两边金额单位和计价基础可一一对应时计算；多金额无法唯一对应时留空。
export function calculateDiscountRate(limit: unknown, winningPrice: unknown): string {
  const limits = priceTokens(limit)
  const prices = priceTokens(winningPrice)
  const ratios: number[] = []
  for (const price of prices) for (const cap of limits) {
    if (price.basis !== cap.basis) continue
    if ((price.currency === 'source') !== (cap.currency === 'source')) continue
    if (price.value <= 0 || cap.value <= 0) continue
    ratios.push(price.value / cap.value)
  }
  if (ratios.length !== 1 || !Number.isFinite(ratios[0])) return ''
  return `${(ratios[0] * 100).toFixed(2)}%`
}

export function formatDiscountRate(value: unknown, legacyFold = false): string {
  const raw = String(value ?? '').trim()
  if (!raw || raw.startsWith('=') || /^[\/／—–-]+$/.test(raw)) return ''
  const match = raw.match(/^([+-]?\d+(?:\.\d+)?)\s*(%)?$/)
  if (!match) return raw
  const amount = Number(match[1])
  const percent = match[2] ? amount : legacyFold && Math.abs(amount) > 1 ? amount * 10 : Math.abs(amount) <= 1 ? amount * 100 : amount
  return `${percent.toFixed(2)}%`
}

function firstPresent(fields: SourceFields, keys: string[]): string {
  for (const key of keys) {
    const value = sourceValue(fields, key)
    if (value && !/^[\/／—–-]+$/.test(value)) return value
  }
  return ''
}

// 原表表头明确标注“万元”时，只读取该金额列首行的单个数值；同格附带的管理费等口径不混入总限价。
function firstLineInWan(fields: SourceFields, keys: string[]): string {
  for (const key of keys) {
    const raw = sourceValue(fields, key)
    if (!raw || /^[\/／—–-]+$/.test(raw)) continue
    const firstLine = raw.split(/\r?\n/, 1)[0].trim()
    const match = firstLine.match(/^([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(?:万元)?$/)
    if (match) return `${match[1].replace(/[,，]/g, '')}万元`
  }
  return ''
}
export function sourceDate(fields: SourceFields, ...keys: string[]) {
  const raw = sourceValue(fields, ...keys)
  if (/^\d{5}(?:\.\d+)?$/.test(raw)) return new Date(Date.UTC(1899, 11, 30) + Number(raw) * 86400000).toISOString().slice(0, 10)
  const match = raw.match(/(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})/)
  if (!match) return raw
  return `${match[1]}-${match[2].padStart(2, '0')}-${match[3].padStart(2, '0')}${raw.match(/[T\s](\d{2}:\d{2})/)?.[1] ? ` ${raw.match(/[T\s](\d{2}:\d{2})/)![1]}` : ''}`
}

// 字段顺序由模板确定，与某条记录是否有值无关；导出可复用相同模板。
export function detailSections(template: DetailTemplate, values: Record<string, string>, notes: Record<string, string> = {}): DetailSection[] {
  return template.map(section => ({ title: section.title, fields: Object.fromEntries(section.labels.map(label => [label, values[label] || ''])), notes }))
}
// 用整个区域的数据确定有依据的字段，单条记录和筛选条件不改变模板。
export function populatedDetailTemplate(template: DetailTemplate, records: DetailSection[][]): DetailTemplate {
  const populated = new Set<string>()
  for (const sections of records) for (const section of sections) for (const [label, value] of Object.entries(section.fields)) {
    const text = value.trim()
    if ((text && !/^[\/／—–-]+$/.test(text)) || section.notes?.[label]?.trim()) populated.add(label)
  }
  return template.map(section => ({ ...section, labels: section.labels.filter(label => populated.has(label)) })).filter(section => section.labels.length)
}
// 只映射已核对的原表列。同名“项目类型”的第二列确实记录采购方式。
export const RESULT_TEMPLATE: DetailTemplate = [
  { title: '项目信息', labels: ['甲方名称', '代理机构', '项目类型', '项目情况'] },
  { title: '招投标信息', labels: ['开标时间', '结果公布时间', '服务期限', '招标金额 / 限价', '中标单位', '中标金额', '折扣率'] },
  { title: '入库台账', labels: ['合同签订时间', '到期日期'] },
  { title: '原表价格资料', labels: ['报价类型'] },
  { title: '原表投标资料', labels: ['所属公司', '甲方类型', '采购方式', '参与单位', '报名参与情况', '报名参与情况统计', '是否踏勘', '标书负责情况', '资料份数', '盲投排名', '报价评分', '系统录入', '跟进人'] },
  { title: '原表费用与保证金', labels: ['人数', '管理费（元/人/月）', '预估总价（万元）', '报名费（元）', '打印费（元）', '投标保证金（元）', '中标服务费（元）', '履约保证金（万元）'] },
  { title: '关联盲投资料', labels: ['项目区域', '招标时间', '报名截止时间', '项目情况简述', '信息筛选人'] },
  { title: '公告链接', labels: ['招标公告网址', '结果公告网址'] },
]
export const RESULT_SOURCE_FIELDS = [
  '甲方名称', '甲方单位', '区域', '代理机构', '项目类型', '项目类型（采购方式）', '项目情况', '是否中标', '开标时间', '结果公布时间', '结果公布日期', '中标公示时间', '公示日期',
  '服务期限', '限价(万元)', '限价（万元）', '限价', '最高限价', '最高限价（万元）', '最高限价（元）', '采购预算', '采购预算（万元）', '采购预算（元）',
  '预算金额', '预算金额（万元）', '预算金额（元）', '招标金额', '招标金额（万元）', '招标金额（元）', '中标单位', '中标价格', '中标金额（万元）', '中标金额',
  '折扣率', '报价折扣率', '合同签订时间', '到期日期', '报价类型', '所属公司', '参与单位', '报名参与情况', '报名参与情况统计', '是否踏勘',
  '标书负责情况', '资料份数', '盲投排名', '报价评分', '系统录入', '人数', '管理费（元/人/月）', '预估总价（万元）', '报名费（元）', '报名费用', '打印费（元）',
  '投标保证金（元）', '中标服务费（元）', '履约保证金（万元）', '项目区域', '招标时间', '报名截止时间', '项目情况简述', '信息筛选人', '跟进人', '招标公告网址',
  '公告网址', '公告连接', '公告链接', '原文链接', '结果公告网址', '序号', '项目名称',
]
const resultTextInWinner = new Set(['未公示', '未公布', '暂未公示', '待公布', '流标', '废标', '重新招采', '放弃中标重新招采'])
export function resultWinner(r: PortfolioResult) { return resultTextInWinner.has(r.winner.trim()) ? '' : r.winner }
export function resultNotes(r: PortfolioResult) {
  if (resultWinner(r) || !r.winner.trim() || Object.values(r.notes).includes(r.winner)) return r.notes
  return { ...r.notes, '原表记录（中标单位栏）': r.winner }
}
export function resultDetails(r: PortfolioResult, template: DetailTemplate = RESULT_TEMPLATE) {
  const primary = r.sourceFields || r.fields
  const prices = r.priceFields || {}
  const allSources = [primary, prices, r.relatedFields || {}, r.auxiliaryFields || {}, ...(r.sourceRows || []).map(row => row.fields || {})]
  const sourceKeys = [...new Set(allSources.flatMap(source => Object.keys(source)))]
  const f = Object.fromEntries(sourceKeys.map(key => [key, allSources.map(source => sourceValue(source, key)).find(Boolean) || '']))
  const b = r.relatedFields || {}
  const isBlind = /盲投项目信息/.test(r.source.file)
  const blind = isBlind ? f : b
  const rawType = sourceValue(f, '项目类型')
  const clientTypes = ['企业', '机关', '酒店', '事业单位', '事业', '政府']
  const purchaseTypes = ['询价', '比选', '磋商', '谈判', '公开', '公开招标', '竞标', '入库', '公开比质比价']
  const values: Record<string, string> = { ...f,
    甲方名称: r.client, 代理机构: r.agency, 所属公司: sourceValue(f, '所属公司'),
    甲方类型: clientTypes.includes(rawType) ? rawType : '',
    采购方式: sourceValue(f, '项目类型（采购方式）') || (purchaseTypes.includes(rawType) ? rawType : ''),
    项目类型: sourceValue(blind, '项目类型') || (!clientTypes.includes(rawType) && !purchaseTypes.includes(rawType) ? rawType : '') || r.business,
    项目情况: sourceValue(f, '项目情况') || r.channel, 是否中标: r.ownMark, 参与单位: r.participants,
    跟进人: sourceValue(f, '跟进人', 'BidOS跟进人'),
    开标时间: r.date, 服务期限: sourceValue(f, '服务期限') || sourceValue(b, '服务期限'),
    '招标金额 / 限价': r.limit || firstPresent(f, ['限价(万元)', '限价（万元）', '限价', '最高限价', '最高限价（万元）', '最高限价（元）', '采购预算', '采购预算（万元）', '采购预算（元）', '预算金额', '预算金额（万元）', '预算金额（元）', '招标金额', '招标金额（万元）', '招标金额（元）']),
    中标单位: resultWinner(r) || sourceValue(f, '中标单位'), 中标金额: r.amount || firstPresent(f, ['中标金额（万元）', '中标金额', '中标价格']),
    结果公布时间: sourceDate(f, '结果公布时间', '结果公布日期', '中标公示时间', '公示日期') || r.publishedAt,
    合同签订时间: sourceDate(r.auxiliaryFields || {}, '合同签订时间'), 到期日期: sourceDate(r.auxiliaryFields || {}, '到期日期'),
    报价类型: sourceValue(prices, '报价类型'), 折扣率: calculateDiscountRate(
      firstPresent(prices, ['限价']), firstPresent(prices, ['中标价格']),
    ) || calculateDiscountRate(
      firstPresent(primary, ['限价(万元)', '限价（万元）', '限价', '招标金额']),
      firstPresent(primary, ['中标金额（万元）', '中标金额', '中标价格']),
    ) || calculateDiscountRate(
      firstLineInWan(primary, ['限价(万元)', '限价（万元）']),
      firstLineInWan(primary, ['中标金额（万元）']),
    ) || calculateDiscountRate(r.limit, r.amount)
      || formatDiscountRate(
        firstPresent(prices, ['折扣率', '报价折扣率']) || firstPresent(primary, ['折扣率', '报价折扣率']),
        /投标项目汇总.*\.xlsx/.test(r.source.file) && /2025年未参与/.test(r.source.sheet),
      ),
    人数: sourceValue(f, '人数'), '管理费（元/人/月）': sourceValue(f, '管理费（元/人/月）'), '预估总价（万元）': sourceValue(f, '预估总价（万元）'),
    是否踏勘: sourceValue(f, '是否踏勘'), 标书负责情况: sourceValue(f, '标书负责情况'), 资料份数: sourceValue(f, '资料份数'),
    盲投排名: sourceValue(f, '盲投排名'), 报价评分: sourceValue(f, '报价评分'), 系统录入: sourceValue(f, '系统录入'),
    '报名费（元）': sourceValue(f, '报名费（元）') || sourceValue(blind, '报名费用'),
    '打印费（元）': sourceValue(f, '打印费（元）'), '投标保证金（元）': sourceValue(f, '投标保证金（元）'),
    '中标服务费（元）': sourceValue(f, '中标服务费（元）'), '履约保证金（万元）': sourceValue(f, '履约保证金（万元）'),
    项目区域: sourceValue(blind, '项目区域') || sourceValue(f, '项目区域', '区域'), 招标时间: sourceDate(blind, '招标时间'), 报名截止时间: sourceDate(blind, '报名截止时间'),
    项目情况简述: sourceValue(blind, '项目情况简述'), 信息筛选人: sourceValue(blind, '信息筛选人'), 招标公告网址: r.url,
    结果公告网址: r.resultUrl || sourceValue(f, '结果公告网址', '中标结果公告网址', '成交结果公告网址'),
  }
  return detailSections(template, values, r.cellNotes).filter(section => section.title !== '入库台账' || r.ownMark === '入库' || values.合同签订时间 || values.到期日期).map(section => ({
    ...section, collapsed: ['原表价格资料', '原表投标资料', '原表费用与保证金', '关联盲投资料', '其他原表字段'].includes(section.title),
    blankWhenMissing: r.result === '待公布' || ['流标', '废标', '重新招采'].includes(r.ownMark) || ['流标', '废标', '重新招采', '放弃中标重新招采'].includes(r.winner.trim()) ? ['中标单位', '中标金额', '折扣率'] : resultTextInWinner.has(r.winner.trim()) ? ['中标单位'] : [],
  }))
}

// 金额原文可能同时记录预算、限价或报价，不把单元格里的词扩成新表头。
export const COMPETITOR_TEMPLATE: DetailTemplate = [
  { title: '项目信息', labels: ['公司名称', '甲方名称', '项目类型', '项目地点', '招标平台'] },
  { title: '招采资料', labels: ['招采时间', '服务期限', '岗位类型'] },
  { title: '结果与金额', labels: ['中标单位', '项目金额'] },
  { title: '公告链接', labels: ['公告连接'] },
]
export const COMPETITOR_SOURCE_FIELDS = [
  '公司名称', '甲方名称', '项目类型', '项目地点', '招标平台', '招采时间', '服务期限', '岗位类型', '中标单位', '项目金额', '项目金额\n（限价）', '项目金额（限价）',
  '中标金额', '是否中标', '公告连接', '公告链接', '投递项目名称', '项目名称', '序号',
]
export function competitorDetails(p: CompetitorProject, companyName: string, template: DetailTemplate = COMPETITOR_TEMPLATE) {
  const sourceList = [p.sourceFields || p.fields, ...(p.sourceRows || []).map(row => row.fields || {})]
  const sourceKeys = [...new Set(sourceList.flatMap(source => Object.keys(source)))]
  const f = Object.fromEntries(sourceKeys.map(key => [key, sourceList.map(source => sourceValue(source, key)).find(Boolean) || '']))
  return detailSections(template, { ...f,
    公司名称: companyName, 甲方名称: p.client, 项目类型: sourceValue(f, '项目类型'), 项目地点: p.region, 招标平台: sourceValue(f, '招标平台'),
    是否中标: sourceValue(f, '是否中标'),
    招采时间: p.date || p.dateRaw, 服务期限: p.duration, 岗位类型: sourceValue(f, '岗位类型'),
    中标单位: p.winner, 项目金额: sourceValue(f, '项目金额', '项目金额\n（限价）', '项目金额（限价）') || (sourceValue(f, '中标金额') ? `中标金额：${sourceValue(f, '中标金额')}` : p.amount),
    公告连接: p.url,
  }, p.cellNotes).map(section => ({ ...section, blankWhenMissing: p.result === '待公布' ? ['中标单位'] : [] }))
}
export const BOND_TEMPLATE: DetailTemplate = [
  { title: '原表记录', labels: ['甲方名称', '保证金金额', '所属公司', '开标时间', '缴纳时间', '退回时间（合同到期）', '退回情况', '预计时间', '跟进人'] },
]
export const BOND_SOURCE_FIELDS = [
  '项目名称', '序号', '甲方名称', '投标保证金（元）', '履约保证金（万元）', '所属公司', '开标时间', '缴纳时间', '缴纳日期', '退回时间（合同到期）',
  '应退日期', '实际退回日期', '实际退回时间', '退回情况', '预计时间', '跟进人', '负责人', '备注',
]
export function bondDetails(b: PortfolioBond, template: DetailTemplate = BOND_TEMPLATE) {
  const sourceList = [b.sourceFields || {}, ...(b.sourceRows || []).map(row => row.fields || {})]
  const sourceKeys = [...new Set(sourceList.flatMap(source => Object.keys(source)))]
  const f = Object.fromEntries(sourceKeys.map(key => [key, sourceList.map(source => sourceValue(source, key)).find(Boolean) || '']))
  const notes = { ...b.cellNotes, 保证金金额: sourceValue(b.cellNotes || {}, b.kind === '履约保证金' ? '履约保证金（万元）' : '投标保证金（元）') }
  return detailSections(template, { ...f,
    甲方名称: b.client, 保证金金额: b.amountText, 所属公司: b.company, 开标时间: b.date,
    缴纳时间: b.paidAt, '退回时间（合同到期）': sourceDate(f, '退回时间（合同到期）'),
    退回情况: b.statusRaw, 预计时间: sourceValue(f, '预计时间'), 跟进人: b.owner,
  }, notes).map(section => ({ ...section, collapsed: section.title === '其他原表字段', blankWhenMissing: ['退回情况', '预计时间', '跟进人'] }))
}
