/**
 * 展示层格式化工具。
 *
 * 存在的理由（2026-09-24 数据质量整改）
 * ------------------------------------
 * 前端曾把 `Opportunity.budget` 直接按「万元」渲染（`{budget} 万`），
 * 但 `/api/opportunities` 返回的是**元**（实测 `budget=710375.5`，即 71.0 万元）。
 * 结果盲投页显示成「847585 万」，**放大了约 10⁴ 倍**。
 * 金额类展示一律走本文件的格式化函数，不要在页面里手写单位。
 */

/** 缺失值统一占位符：源数据没有就显示「—」，绝不用 0 冒充。 */
export const MISSING = '—'

/**
 * 预算格式化：入参单位是**元**，输出「xx 万」。
 *
 * 契约来源：`apps/api/app/repositories/postgres_repository.py` 的
 * `_opportunity()` 直接透出 `projects.budget`（numeric，元）。
 *
 * @example formatBudgetWan(710375.5) // "71 万"
 * @example formatBudgetWan(12000000) // "1200 万"
 * @example formatBudgetWan(null)     // "—"
 */
export function formatBudgetWan(budget: number | null | undefined): string {
  if (budget === null || budget === undefined || !Number.isFinite(budget)) return MISSING
  if (budget <= 0) return MISSING
  const wan = budget / 10000
  const digits = wan >= 100 ? 0 : wan >= 10 ? 1 : 2
  // 去掉无意义的尾随 0（"71.0" → "71"，"0.50" → "0.5"）
  const text = wan.toFixed(digits).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
  return `${text} 万`
}

/**
 * 百分比格式化：分母为 0 / 值为 null 时返回占位符。
 * 避免渲染出 `NaN%` 或用 0 冒充「无数据」。
 */
export function formatRatioPct(
  numerator: number | null | undefined,
  denominator: number | null | undefined,
): string {
  if (numerator === null || numerator === undefined || !denominator || denominator <= 0) {
    return MISSING
  }
  return `${((numerator / denominator) * 100).toFixed(1)}%`
}

/** 0~1 之间的小数比率格式化（如竞对报价比）。null 表示源数据缺失。 */
export function formatRatePct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING
  return `${(value * 100).toFixed(1)}%`
}
