/** src/features/schedule/useSchedules.ts — 定时订阅摘要数据 hook（2.11 E3 T6）。
 *  消费 schedules:summary（服务端聚合精确计数，T1 schedule_unread_count 上链）；
 *  挂载拉一次 + schedule 活动终态边沿补拉（期次完成 → 未读计数上屏，0.4 §6.3）
 *  + refresh 供写操作后手动调用。零轮询。 */
import { useState, useEffect, useCallback } from 'react'
import { useActivity } from '../activity/ActivityContext'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'

/** GET /schedules/summary totals（db schedule_unread_count 直出，snake_case 语义
 *  键 total=未读数——与订阅行字段命名解耦） */
export interface ScheduleTotals {
  n: number
  total: number
  abnormal: number
}

/** task_schedules 行（db 列直出，E2 CustomRow 同口径不做 camel 映射） */
export interface ScheduleRow {
  id: string
  name: string
  prompt: string
  category: string
  interval_type: 'daily' | 'weekly' | 'monthly'
  interval_value: string
  target_type: 'project' | 'baselib' | null
  target_id: string | null
  /** 订阅类型标记（2.11 §12 收敛 2026-10-25）：'rollup' 聚合简报 / null 情报
   *  订阅（focus/sweep 已退役置 NULL；db 出参归一恒为这两值） */
  scope: 'rollup' | null
  /** rollup 聚合圈选（订阅 id 数组；null = 全部启用订阅） */
  digest_schedule_ids: string[] | null
  auto_import: number | boolean
  enabled: number | boolean
  next_run_at: string | null
  last_run_at: string | null
  /** 行呼吸点（§8.4）：list_schedules 关联 running 期次存在时为 true */
  is_running?: boolean
  consecutive_failures: number
  archive_last_run_id?: string | null
  archive_file_id?: string | null
  latest_done: { run_id: string; finished_at: string; summary: string[] } | null
  created_at: string
}

/** task_schedule_runs 行（list/inbox/runs 直出；summary 已解析 list） */
export interface ScheduleRunRow {
  id: string
  schedule_id: string
  task_id: string | null
  status: 'running' | 'done' | 'failed' | 'skipped' | 'missed'
  report_path: string | null
  summary: string[] | null
  /** 失败原因（§8.4 ReadView 失败态；done 恒 NULL） */
  error?: string | null
  read_at: string | null
  started_at: string
  finished_at: string | null
  schedule_name?: string
  /** digest 行透传订阅 enabled（速览行暂停灰显，§8.4；仅 schedules:digest 返回） */
  schedule_enabled?: number
  /** inbox/digest 行透传订阅分类（0.6 §3.2 情报流类别 chip；订阅删除后 fallback general） */
  schedule_category?: string
  /** inbox/digest 行透传订阅类型（2.11 §12.4：rollup 期次徽标随行） */
  schedule_scope?: string | null
  archive_last_run_id?: string | null
}

const ZERO_TOTALS: ScheduleTotals = { n: 0, total: 0, abnormal: 0 }

/** 摘要拉取三态（页面遍历 review M1-1 2026-10-24）：失败必须区别于真实零订阅，
 *  引导类消费端（管理台收件箱 / IntelCardZone 空态）不得在失败时渲染「零订阅」
 *  建订阅引导（误导创建重复订阅）；纯数字展示类消费端（NavTabs 徽章/
 *  MorningHero/ScheduleSideSummary）失败回退 0 不触发动作、可接受 */
export type SchedulesStatus = 'loading' | 'loaded' | 'failed'

export function useSchedules() {
  const [totals, setTotals] = useState<ScheduleTotals>(ZERO_TOTALS)
  const [status, setStatus] = useState<SchedulesStatus>('loading')

  const refresh = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('schedules:summary') as {
        totals?: ScheduleTotals
      }
      setTotals(res?.totals ?? ZERO_TOTALS)
      setStatus('loaded')
    } catch (err) {
      console.error('Load schedule summary failed:', err)
      setStatus('failed')
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 订阅期次执行（schedule 源，2.11 D2 复核）：运行中 → 终态边沿补拉
  // （信号源归正：影子任务已不入 task 源，改由 schedule 源呈现；
  // 用法不变，仍是 useActivity(kind).isActive 边沿，零新增轮询）
  const { isActive } = useActivity('schedule')
  useEdgeRefetch(isActive, () => {
    void refresh()
  })

  return { totals, status, refresh }
}
