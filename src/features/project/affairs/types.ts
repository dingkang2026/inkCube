/** src/features/project/affairs/types.ts — 时期任务前端契约 + 逾期/到期纯函数(3.0 §6)
 *  字段与后端 phase_tasks 表(Task 2)/ affairs:listByProject 返回一一对齐;
 *  日期均为 ISO 日期串(YYYY-MM-DD),字典序即时间序。
 */

export type PhaseTaskType = 'material' | 'report' | 'milestone' | 'recurring'
export type PhaseTaskStatus = 'open' | 'done' | 'skipped' | 'pending_activation'
export type PhaseKey = 'before' | 'during' | 'after'

export interface RecurrenceRule { freq: 'monthly' | 'quarterly' | 'yearly'; due_day: number | 'end' | string }

export interface PhaseTask {
  id: string
  project_id: string
  phase: PhaseKey
  task_type: PhaseTaskType
  title: string
  description: string
  template_id: string | null
  status: PhaseTaskStatus
  occurrence_key: string | null
  due_date: string | null
  recurrence_rule: RecurrenceRule | null
  auto_complete: { on: string; match?: string } | null
  linked_entity_type: string | null
  linked_entity_id: string | null
  completed_at: string | null
  completed_by: string | null
  created_at: string
}

export type AffairsView = 'board' | 'rhythm' | 'calendar'

/** 本地时区当日 ISO(YYYY-MM-DD):toISOString 走 UTC 会造成边界日期偏一天,禁用 */
export function localISODate(now?: Date): string {
  const d = now ?? new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

/** 逾期:open 且 due 早于当日(当日到期不算逾期) */
export function isOverdue(task: PhaseTask, now?: Date): boolean {
  return task.status === 'open' && !!task.due_date && task.due_date.slice(0, 10) < localISODate(now)
}

/** 逾期天数(≥1);未逾期返回 0 */
export function daysOverdue(task: PhaseTask, now?: Date): number {
  if (!isOverdue(task, now)) return 0
  const dueMs = new Date(`${task.due_date!.slice(0, 10)}T00:00:00`).getTime()
  const todayMs = new Date(`${localISODate(now)}T00:00:00`).getTime()
  return Math.max(1, Math.round((todayMs - dueMs) / 86400000))
}

/** 含今日起 days 日内到期(当日到期含在内);已逾期不在此列,归 isOverdue 展示 */
export function dueWithin(task: PhaseTask, days: number, now?: Date): boolean {
  if (task.status !== 'open' || !task.due_date) return false
  const due = task.due_date.slice(0, 10)
  const today = localISODate(now)
  if (due < today) return false
  const d = new Date(`${today}T00:00:00`)
  d.setDate(d.getDate() + days)
  const limit = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return due <= limit
}

/** 脉搏带应办概要(3.0 §2 二期):由任务列表派生的纯数据 */
export interface PulseDueSummary {
  overdue: number
  weekDue: number
  /** 警示:0 开头坏 due_date 经字典序会成为 nearest 且 daysOverdue 产 NaN(Task 12 review C-1);
   *  当前 UI 零消费不可达,后续 UI 消费前必须先过非 ISO 哨兵(/^\d{4}-\d{2}-\d{2}/,同 PhaseBoard/TaskDrawer 口径) */
  nearestOverdueTitle: string | null
  nearestOverdueDays: number
}

export function summarizeDueTasks(tasks: PhaseTask[], now: Date = new Date()): PulseDueSummary {
  const open = tasks.filter((x) => x.status === 'open')
  const overdueList = open.filter((x) => isOverdue(x, now))
  const nearest = overdueList.reduce<PhaseTask | null>((acc, x) => {
    if (!acc || (x.due_date ?? '') < (acc.due_date ?? '')) return x
    return acc
  }, null)
  return {
    overdue: overdueList.length,
    weekDue: open.filter((x) => dueWithin(x, 7, now) && !isOverdue(x, now)).length,
    nearestOverdueTitle: nearest?.title ?? null,
    nearestOverdueDays: nearest ? daysOverdue(nearest, now) : 0,
  }
}

// ── 3.0 三期:首页今日工作台 + 健康色点(§7.2;口径复用上方 isOverdue/dueWithin)──

/** affairs:listDue 返回的任务行(PhaseTask + JOIN 出的项目名/阶段) */
export interface DueTask extends PhaseTask {
  project_name: string
  project_stage?: string | null
}

/** 待办聚合窗口天数（§7.2：逾期 + 未来 N 天；后端 horizon 默认值、色点 weekDue、近期日历三处共源） */
export const DUE_HORIZON_DAYS = 7

/** per 项目健康概要(色点与卡片墙派生的纯数据);矛盾/缺口为 V2-E4 首页卡片行数据源
 *  (后端 reviews_counts 分型桶,gap = data-gap + missing-page,与知识册封面信任信号行同口径) */
export interface ProjectHealthSummary {
  overdue: number
  weekDue: number
  reviewsPending: number
  contradiction: number
  gap: number
}

export type HealthLevel = 'green' | 'orange' | 'red'

/** 色点档位(§7.2 口径):红=有逾期;橙=本周待办或待处理发现项;绿=两无 */
export function healthLevel(s: ProjectHealthSummary): HealthLevel {
  if (s.overdue > 0) return 'red'
  if (s.weekDue > 0 || s.reviewsPending > 0) return 'orange'
  return 'green'
}

/** reviews_counts 分型桶（affairs:listDue 响应，V2-E4）：total=全部待处理；
 *  gap = data-gap + missing-page（与知识册封面信任信号行同口径） */
export type ReviewTypedCounts = Record<string, { total: number; contradiction: number; gap: number }>

/** 跨项目按项目分组计数(weekDue 口径与 summarizeDueTasks 一致:dueWithin 且非逾期);
 *  reviewsCounts 中的项目即使无任务也出现(纯发现项项目=橙,侧栏色点可见)。
 *  V2-E4 起值为分型桶 {total,contradiction,gap}。 */
export function summarizeDueByProject(
  tasks: DueTask[],
  reviewsCounts?: ReviewTypedCounts,
  now: Date = new Date(),
): Record<string, ProjectHealthSummary> {
  const acc: Record<string, ProjectHealthSummary> = {}
  for (const pid of Object.keys(reviewsCounts ?? {})) {
    const b = reviewsCounts![pid]
    acc[pid] = {
      overdue: 0, weekDue: 0, reviewsPending: b?.total ?? 0,
      contradiction: b?.contradiction ?? 0, gap: b?.gap ?? 0,
    }
  }
  for (const task of tasks) {
    const b = reviewsCounts?.[task.project_id]
    const entry = acc[task.project_id]
      ?? (acc[task.project_id] = {
        overdue: 0, weekDue: 0, reviewsPending: b?.total ?? 0,
        contradiction: b?.contradiction ?? 0, gap: b?.gap ?? 0,
      })
    if (isOverdue(task, now)) entry.overdue += 1
    else if (dueWithin(task, DUE_HORIZON_DAYS, now)) entry.weekDue += 1
  }
  return acc
}

// ── 3.0 四期:AI 任务建议(3.1 §4.3,task_suggestions 表行;kind 与后端 affairs.py 同白名单)──

export type SuggestionKind = 'create_task' | 'complete_task'
export type SuggestionStatus = 'pending' | 'accepted' | 'dismissed'

export interface TaskSuggestion {
  id: string
  project_id: string
  kind: SuggestionKind
  title: string
  description: string
  suggested_phase: PhaseKey | null
  suggested_task_type: PhaseTaskType | null
  target_task_id: string | null
  accepted_task_id: string | null
  status: SuggestionStatus
  created_at: string
  decided_at: string | null
}
