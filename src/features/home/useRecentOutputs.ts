/** src/features/home/useRecentOutputs.ts — 卡片墙「新产出」角标数据源(0.5 §7.2)。
 *  从 ActivityContext 全局心跳快照(status.notifications.unread)纯派生,
 *  零新增轮询/网络调用(coding-habits §2.7:数据源统一,禁自建拉取)。
 *  口径:kind==='completed' && entity_type ∈ 产出类 && project_id 非空 && 距今 <24h。
 *  消退路径:通知已读由通知中心/动态行 markRead 管理,角标随 unread 收敛自然消失,此处不主动 markRead。 */
import { useMemo } from 'react'
import { useActivity } from '../activity/ActivityContext'
import type { NotificationItem } from '../activity/types'

/** 计入「新产出」的实体类型(通知侧写入值,activity/types 枚举;wiki_compile=Wiki 编译产出) */
export const OUTPUT_ENTITY_TYPES: ReadonlySet<string> = new Set([
  'task', 'report', 'file', 'ppt', 'form_fill', 'wiki_compile',
])

const DAY_MS = 24 * 60 * 60 * 1000

/** 纯派生:未读通知 → 按项目 id 计数(nowMs 注入便于测试 24h 边界) */
export function deriveRecentOutputs(
  unread: NotificationItem[],
  nowMs: number,
): Record<string, number> {
  const acc: Record<string, number> = {}
  for (const n of unread) {
    if (n.kind !== 'completed' || !n.project_id || !OUTPUT_ENTITY_TYPES.has(n.entity_type)) continue
    const ts = new Date(n.created_at).getTime()
    if (Number.isNaN(ts) || nowMs - ts >= DAY_MS) continue
    acc[n.project_id] = (acc[n.project_id] ?? 0) + 1
  }
  return acc
}

export function useRecentOutputs(nowMs?: number): Record<string, number> {
  const { status } = useActivity()
  // nowMs 可注入(测试 24h 窗口边界);缺省真实时钟
  return useMemo(
    () => deriveRecentOutputs(status?.notifications.unread ?? [], nowMs ?? Date.now()),
    [status, nowMs],
  )
}
