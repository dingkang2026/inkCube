/** src/features/activity/ActivityMonitor.tsx — 单一常驻活动轮询（0.4lifecycle-design.md §8.1 #0，v2.2）
 *  自适应频率：有进行中活动 5s / 空闲 15s / 窗口不可见降频 45s（仍真实拉取），
 *  恢复可见立即拉取（visibilitychange）。
 *  连续失败指数退避 5→10→20→60s 封顶，成功即重置。
 *  新通知 → Toast（>5 条合并为汇总），弹出即标已读；
 *  首次拉取只建立基线不弹 Toast（避免启动时历史未读轰炸）。
 *  防挂起：invoke 超过 INVOKE_TIMEOUT 未 settle 视为失败（退避重试），
 *  循环不可能被底层 IPC 挂起永久杀死（曾因 Node fetch 复用半关闭
 *  keep-alive 连接且无超时导致 UI 快照冻结）。
 *  禁止 hidden 早退跳过拉取：Windows Chromium 窗口遮挡检测会把被完全
 *  遮挡的窗口判为 hidden，早退曾致心跳死区——下载进度冻结在 0%、
 *  呼吸点不出现、Toast 延迟到窗口置前才弹出（2026-10 实测根因）。
 */
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useToast } from '../knowledge/Toast'
import { useActivity } from './ActivityContext'
import type { NotificationItem } from './types'

const ACTIVE_INTERVAL = 5000
const IDLE_INTERVAL = 15000
const BACKOFF_STEPS = [5000, 10000, 20000, 60000]
/** 单次轮询新通知超过该数 → 合并为一条汇总 Toast */
const BATCH_LIMIT = 5
/** 单次 invoke 超时：Electron 侧 fetchWithTimeout 已有 15s 硬超时，
 *  此处 8s 更早放弃并退避，双重保证循环活性 */
const INVOKE_TIMEOUT = 8000

/** 通知点击导航（Toast action 与首页动态条目共用）：
 *  项目类 → 打开对应项目 Tab；任务 → 任务页；模型下载 → 设置页。
 *  container='baselib' 的项目类活动 → 打开基础库详情页（0.4 §3.6 导航分流）。
 *  entity_type 与后端 notifications 表写入值一致（0.4 §6.5：file/wiki_compile/report/task/model_download）。 */
export function navigateToNotification(n: NotificationItem) {
  if (n.entity_type === 'task') {
    window.dispatchEvent(new CustomEvent('app:navigate-tasks'))
    return
  }
  if (n.entity_type === 'model_download') {
    window.dispatchEvent(new CustomEvent('app:navigate-settings', { detail: { section: 'internalModel' } }))
    return
  }
  if (!n.project_id) return
  const tab = n.entity_type === 'file' ? 'materials' : n.entity_type === 'wiki_compile' ? 'wiki' : 'reports'
  window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
    detail: { projectId: n.project_id, tab, container: n.container ?? 'project' },
  }))
}

/** 无渲染组件：必须挂在 ActivityProvider + ToastProvider 内部 */
export function ActivityMonitor() {
  const { t } = useTranslation()
  const showToast = useToast()
  const { applyStatus, markRead, summary } = useActivity()

  // E3 activity:stream → 立即心跳入口（0.4 §6.4）：事件体不直接解析（Toast / 基线 /
  // markRead 与轮询共享同一实现，避免分叉），仅作触发信号；轮询 effect 内赋值
  const pollKickRef = useRef<() => void>(() => {})

  // 轮询间隔依赖最新 summary，用 ref 避免重建轮询循环
  const summaryRef = useRef(summary)
  summaryRef.current = summary

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let invokeTimer: ReturnType<typeof setTimeout> | null = null
    let failCount = 0
    let inFlight = false
    let rerunAfter = false
    const seenIds = new Set<number>()
    let baselineReady = false

    const currentInterval = () => {
      const s = summaryRef.current
      const hasActive =
        s.active_files > 0 ||
        s.active_compiles > 0 ||
        s.generating_reports > 0 ||
        s.running_tasks > 0 ||
        s.downloading_models > 0
      return hasActive ? ACTIVE_INTERVAL : IDLE_INTERVAL
    }

    const dispatchToasts = (items: NotificationItem[]) => {
      if (items.length > BATCH_LIMIT) {
        showToast(t('activity.batch', { count: items.length }), 'info')
        void markRead(items.map((n) => n.id))
        return
      }
      for (const n of items) {
        const type = n.kind === 'failed' ? 'error' : 'success'
        showToast(
          t(`activity.${n.entity_type}.${n.kind}`, { title: n.entity_title || '' }),
          type,
          {
            detail: n.detail || undefined,
            action: {
              label: t('activity.view'),
              onClick: () => navigateToNotification(n),
            },
          },
        )
      }
      void markRead(items.map((n) => n.id))
    }

    const poll = async () => {
      if (cancelled) return
      // 防链分叉：在飞期间 kick / visibilitychange 到达只标记补拉，不并发启动第二条
      // 链式调度链（尾部无条件 setTimeout 会让旧链存活、频率只增不减，v3.9 修复）
      if (inFlight) {
        rerunAfter = true
        return
      }
      inFlight = true
      try {
        // 防挂起：invoke 未在时限内 settle 视为失败（退避重试）
        const result = (await Promise.race([
          window.electronAPI.invoke('activities:getStatus'),
          new Promise<never>((_, reject) => {
            invokeTimer = setTimeout(
              () => reject(new Error('activities:getStatus invoke timeout')),
              INVOKE_TIMEOUT,
            )
          }),
        ])) as import('./types').ActivityStatus
        if (cancelled) return
        failCount = 0
        applyStatus(result)

        // 新通知检测：unread + recent 合并后按 id 防重；
        // 首次拉取只建立基线不弹 Toast（避免启动时历史未读轰炸）。
        // 注意：此处不得 return——会跳过末尾调度导致循环死亡（曾致 UI 快照冻结），
        // 用 if/else 区分基线/正常路径，统一走末尾 setTimeout。
        const all = [...result.notifications.recent, ...result.notifications.unread]
        const fresh = all.filter((n) => !seenIds.has(n.id))
        for (const n of all) seenIds.add(n.id)
        if (baselineReady) {
          const unique = fresh.filter((n, i) => fresh.findIndex((x) => x.id === n.id) === i)
          if (unique.length > 0) dispatchToasts(unique)
        } else {
          baselineReady = true
        }
      } catch {
        failCount += 1
      } finally {
        if (invokeTimer) clearTimeout(invokeTimer)
        invokeTimer = null
        inFlight = false // INVOKE_TIMEOUT race 保证必复位，不会永久卡在飞
      }
      if (cancelled) return // 卸载后不再自我调度（残留 timer 已被 cleanup 清除）
      // 在飞期间有触发到达 → 0ms 立即补一轮（终态事件即时性不因防重叠而丢失）
      const immediate = rerunAfter
      rerunAfter = false
      // 延迟优先级：立即补拉 > 失败退避 > 不可见降频（45s 真实拉取）> 正常自适应间隔；
      // hidden 不跳过拉取（Windows 遮挡检测会使被遮挡窗口 hidden=true，见文件头）
      const delay = immediate
        ? 0
        : failCount > 0
          ? BACKOFF_STEPS[Math.min(failCount - 1, BACKOFF_STEPS.length - 1)]
          : document.hidden
            ? IDLE_INTERVAL * 3
            : currentInterval()
      timer = setTimeout(poll, delay)
    }

    // 恢复可见立即拉取：消除不可见期最长 45s 的死区等待，
    // 窗口置前一刻 Toast / 进度 / 呼吸点即为最新；
    // 若 poll 在飞则仅标记补拉（防链分叉，完成后 0ms 补一轮）
    const onVisibility = () => {
      if (!document.hidden && !cancelled) {
        if (timer) clearTimeout(timer)
        poll()
      }
    }
    document.addEventListener('visibilitychange', onVisibility)

    // E3 立即心跳入口：丢弃等待中的低频调度立即拉取（同 visibilitychange 语义）；
    // 200ms 节流合并事件风暴（batch 完成等）为一次拉取
    let kickPending = false
    pollKickRef.current = () => {
      if (cancelled || kickPending) return
      kickPending = true
      setTimeout(() => {
        kickPending = false
        if (cancelled) return
        if (timer) clearTimeout(timer)
        void poll()
      }, 200)
    }

    poll()
    return () => {
      cancelled = true
      pollKickRef.current = () => {}
      if (timer) clearTimeout(timer)
      if (invokeTimer) clearTimeout(invokeTimer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [applyStatus, markRead, showToast, t])

  // E3 订阅（0.4 §6.4，独立于轮询 effect）：终态事件到达 → 立即一次聚合拉取
  // （Toast 即时化）；SSE 不可用时心跳拉兜底，功能无损
  useEffect(() => {
    const off = window.electronAPI.on('activity:stream', () => pollKickRef.current())
    void window.electronAPI.invoke('activities:subscribeStream').catch(() => {
      /* SSE 不可用（通道未注册）：心跳拉兜底 */
    })
    return () => off()
  }, [])

  return null
}
