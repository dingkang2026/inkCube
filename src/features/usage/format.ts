/** src/features/usage/format.ts — Token 数字格式化与来源标签映射（2.10 §6.5 视觉规格）。
 *  紧凑化仅用于行内呈现；tooltip/汇总卡保留千分位完整值（精确值原则贯穿呈现，§6.4 规则 3）。 */

/** 紧凑 token：1234 → 1.2k、3400000 → 3.4M；千位内原样 */
export function compactTokens(n: number): string {
  if (n >= 1_000_000) {
    const v = (n / 1_000_000).toFixed(1)
    return `${v.endsWith('.0') ? v.slice(0, -2) : v}M`
  }
  if (n >= 1000) {
    const v = (n / 1000).toFixed(1)
    return `${v.endsWith('.0') ? v.slice(0, -2) : v}k`
  }
  return String(n)
}

/** 千分位完整值（tooltip / 汇总卡） */
export function thousands(n: number): string {
  return n.toLocaleString('en-US')
}

/** models 字段归一：GROUP_CONCAT 逗号串 → 数组（容错已数组形态） */
export function modelsList(models?: string | string[]): string[] {
  if (!models) return []
  return Array.isArray(models) ? models : models.split(',')
}

/** source 枚举 → i18n 标签键（2.10 §3.3 枚举；统计页来源表 / 徽章分拆共用）。
 *  vlm 保留（历史数据渲染；2026-10-21 图片描述改走 knowledge/LLM 链路） */
export const USAGE_SOURCE_LABEL_KEYS: Record<string, string> = {
  task: 'usage.srcTask',
  chat: 'usage.srcChat',
  vlm: 'usage.srcVlm',
  knowledge: 'usage.srcKnowledge',
  wiki: 'usage.srcWiki',
  report: 'usage.srcReport',
  form: 'usage.srcForm',
  other: 'usage.srcOther',
}
