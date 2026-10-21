/** src/features/usage/useUsageSummary.ts — /usage/summary 数据源 hook（2.10 §5.4 L2 刷新）。
 *  挂载取 + `app:usage-changed`（1.5s 去抖合并批量终态）+ 窗口 focus/可见自愈
 *  （覆盖午夜翻转/长挂起/SSE 瞬断三种过期）；失败保留上次值（§6.3：底栏不闪烁、
 *  Python 不可用静默降级）。零新增轮询（§6.4 规则 1）。 */
import { useCallback, useEffect, useState } from 'react'
import type { UsageSummary } from '../../types'

/** by_* 分组行（grp = 分组键：date/source/model/project_id；by_project 补名称） */
export interface UsageAggRow extends UsageSummary {
  grp: string
  project_name?: string | null
}

/** GET /usage/summary 响应（python/db/database.py get_usage_summary 形状） */
export interface UsageSummaryResponse {
  today: UsageSummary
  total: UsageSummary & { models_count: number }
  range: UsageSummary
  range_key: string
  by_day: UsageAggRow[]
  by_source: UsageAggRow[]
  by_model: UsageAggRow[]
  by_project: UsageAggRow[]
}

const DEBOUNCE_MS = 1500

export function useUsageSummary(rangeKey = 'all', projectId?: string) {
  const [data, setData] = useState<UsageSummaryResponse | null>(null)

  const reload = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('usage:summary', { rangeKey, projectId })
      // 形状校验：不完整响应（旧版后端缺字段/异常形状）保留上次值，不把坏形状
      // 下发到消费端（§6.3 降级：明细查询失败静默保留，绝不崩渲染树）
      if (res && typeof res === 'object' && 'total' in res) {
        setData(res as UsageSummaryResponse)
      }
    } catch {
      // 明细查询降级：保留上次值，不报错弹层（§6.3 状态映射）
    }
  }, [rangeKey, projectId])

  useEffect(() => { void reload() }, [reload])

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    const onChanged = () => {
      if (timer) return // 去抖窗口内：合并后续信号为一次重取
      timer = setTimeout(() => { timer = null; void reload() }, DEBOUNCE_MS)
    }
    const onFocus = () => {
      if (document.visibilityState === 'visible') void reload()
    }
    window.addEventListener('app:usage-changed', onChanged)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('app:usage-changed', onChanged)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
      if (timer) { clearTimeout(timer); timer = null }
    }
  }, [reload])

  return { data, reload }
}
