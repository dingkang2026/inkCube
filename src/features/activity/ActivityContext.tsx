/** src/features/activity/ActivityContext.tsx — Activity Hub 全局状态（0.4lifecycle-design.md §5.1）
 *  单一数据源：ActivityMonitor 轮询聚合端点后调用 applyStatus 写入，
 *  所有全局反馈面（NavTabs 徽章 / ProjectList 活跃点 / Tab 角标 / 首页活动区）
 *  从本 Context 取数，替代原有各组件独立轮询。
 *
 *  useActivity(kind)（0.4lifecycle-design.md §6.2）：按 kind 订阅活动视图；页面级轮询的 enabled 谓词
 *  一律从这取（全局心跳派生），禁止自建手动布尔（coding-habits §2.7）。
 */
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { ActivityStatus, ActivitySummary, ActiveEntityType, ActiveItem, NotificationItem, ProjectActivity } from './types'
import { EMPTY_SUMMARY } from './types'

interface ActivityContextValue {
  /** 最近一次聚合结果（null = 尚未拉取成功） */
  status: ActivityStatus | null
  /** 五字段 summary（未拉取时全 0） */
  summary: ActivitySummary
  /** 按项目聚合的活动计数（活跃点 / Tab 角标 / 项目徽章取数） */
  byProject: Map<string, ProjectActivity>
  /** 有进行中活动的项目 id 集 */
  activeProjectIds: Set<string>
  /** 最近动态流（unread + recent 按 id 去重降序，首页展示用；未读态看条目自身 read_at） */
  feed: NotificationItem[]
  /** Monitor 拉取成功后调用 */
  applyStatus: (status: ActivityStatus) => void
  /** 批量标记已读（弹出即已读 / 点击动态条目时调用，乐观更新） */
  markRead: (ids: number[]) => Promise<void>
}

const ActivityContext = createContext<ActivityContextValue | null>(null)

export function ActivityProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ActivityStatus | null>(null)
  const [feed, setFeed] = useState<NotificationItem[]>([])

  const applyStatus = useCallback((next: ActivityStatus) => {
    setStatus(next)
    // unread + recent 合并去重，unread 状态优先（可能含刚标已读的最新快照差异）
    const map = new Map<number, NotificationItem>()
    for (const n of next.notifications.recent) map.set(n.id, n)
    for (const n of next.notifications.unread) map.set(n.id, n)
    setFeed([...map.values()].sort((a, b) => b.id - a.id))
  }, [])

  const markRead = useCallback(async (ids: number[]) => {
    if (ids.length === 0) return
    try {
      await window.electronAPI.invoke('notifications:markRead', { ids })
    } catch {
      // best-effort：失败不影响 UI，下次轮询会重新同步
    }
    const idSet = new Set(ids)
    const now = new Date().toISOString()
    setFeed((prev) => prev.map((n) => (idSet.has(n.id) && !n.read_at ? { ...n, read_at: now } : n)))
  }, [])

  const summary = status?.summary ?? EMPTY_SUMMARY

  const byProject = useMemo(() => {
    const map = new Map<string, ProjectActivity>()
    for (const a of status?.active ?? []) {
      if (!a.project_id) continue
      let p = map.get(a.project_id)
      if (!p) {
        p = {
          projectId: a.project_id,
          projectName: a.project_name || '',
          container: a.container === 'baselib' ? 'baselib' : 'project',
          files: 0,
          imageDescribing: 0,
          compiles: 0,
          reports: 0,
        }
        map.set(a.project_id, p)
      }
      if (a.entity_type === 'file') {
        p.files += a.count || 0
        p.imageDescribing += a.image_describing || 0
      } else if (a.entity_type === 'wiki_compile') {
        p.compiles += 1
      } else if (a.entity_type === 'report') {
        p.reports += 1
      }
    }
    return map
  }, [status])

  const activeProjectIds = useMemo(() => {
    const set = new Set<string>()
    for (const p of byProject.values()) {
      if (p.files > 0 || p.compiles > 0 || p.reports > 0) set.add(p.projectId)
    }
    return set
  }, [byProject])

  const value = useMemo(
    () => ({ status, summary, byProject, activeProjectIds, feed, applyStatus, markRead }),
    [status, summary, byProject, activeProjectIds, feed, applyStatus, markRead],
  )

  return <ActivityContext.Provider value={value}>{children}</ActivityContext.Provider>
}

/** 单一 kind 的活动视图（useActivity(kind) 返回） */
export interface ActivityKindView {
  kind: ActiveEntityType
  /** 该 kind 的活跃项（全局心跳快照，Monitor 5/15s 粒度） */
  items: ActiveItem[]
  /** 是否存在活跃项 */
  isActive: boolean
}

export function useActivity(): ActivityContextValue
export function useActivity(kind: ActiveEntityType): ActivityKindView
export function useActivity(kind?: ActiveEntityType): ActivityContextValue | ActivityKindView {
  const ctx = useContext(ActivityContext)
  if (!ctx) throw new Error('useActivity must be used within ActivityProvider')
  const items = useMemo(
    () => (kind ? (ctx.status?.active ?? []).filter((a) => a.entity_type === kind) : []),
    [ctx.status, kind],
  )
  if (!kind) return ctx
  return { kind, items, isActive: items.length > 0 }
}
