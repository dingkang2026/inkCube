/** src/features/activity/ActivityMonitor.tsx — 单一常驻活动轮询（0.4lifecycle-design.md §8.1 #0，v2.2）
 *  自适应频率：有进行中活动 5s / 空闲 15s / 窗口不可见降频 45s（仍真实拉取），
 *  恢复可见立即拉取（visibilitychange）。
 *  连续失败指数退避 5→10→20→60s 封顶，成功即重置。
 *  新通知 → Toast（>5 条合并为汇总），弹出即标已读；窗口失焦/不可见时同步发
 *  OS 系统通知（2.11 §4，同一 dispatchToasts 单实现 D8，>5 条同合并，前台不发 D3）。
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
import type { ActiveItem, NotificationItem } from './types'

const ACTIVE_INTERVAL = 5000
const IDLE_INTERVAL = 15000
const BACKOFF_STEPS = [5000, 10000, 20000, 60000]
/** 单次轮询新通知超过该数 → 合并为一条汇总 Toast */
const BATCH_LIMIT = 5
/** 单次 invoke 超时：Electron 侧 fetchWithTimeout 已有 15s 硬超时，
 *  此处 8s 更早放弃并退避，双重保证循环活性 */
const INVOKE_TIMEOUT = 8000

/** 通知点击导航（Toast action 与首页动态条目共用）：
 *  项目类 → 打开对应项目 Tab；任务 → 任务页；模型下载 → 设置页；
 *  填表模板（全局资产）→ 模板管理页；填表实例 → 项目 forms Tab；
 *  PPT 模板任务（container='templates'，全局资产 2.3 §4.2）→ 模板管理页 PPT模板 Tab；
 *  订阅期次（schedule_run，全局资产无项目实体 2.11 §2）→ 订阅管理台；
 *  事务到期（due/overdue，仅通知侧）→ 项目 affairs Tab 并定位到该条（2.11 §3.2）；
 *  项目导出/导入（2.9 §8）→ 项目 materials Tab（同首页进行中卡片落点；
 *  导入失败无 project_id 时无落点可跳，不误导）。
 *  container='baselib' 的项目类活动 → 打开基础库详情页（0.4 §3.6 导航分流）。
 *  entity_type 与后端 notifications 表写入值一致（0.4 §6.5）。 */
export function navigateToNotification(n: NotificationItem) {
  if (n.entity_type === 'task') {
    window.dispatchEvent(new CustomEvent('app:navigate-tasks'))
    return
  }
  if (n.entity_type === 'schedule_run' || n.entity_type === 'schedule') {
    // 期次（schedule_run，2.11 §2）与订阅级（schedule：archive_failed/missed/
    // interrupted）通知统一落点订阅管理台
    window.dispatchEvent(new CustomEvent('app:navigate-schedules'))
    return
  }
  if (n.entity_type === 'model_download') {
    window.dispatchEvent(new CustomEvent('app:navigate-settings', { detail: { section: 'internalModel' } }))
    return
  }
  if (n.entity_type === 'form_template') {
    window.dispatchEvent(new CustomEvent('app:navigate-template-management'))
    return
  }
  // PPT 模板任务（2.3 §4.2 模板工坊）：container='templates' 无项目实体 →
  // 模板管理页 PPT模板 Tab；生成类 ppt 携带 project_id 走下方项目 reports Tab
  if (n.entity_type === 'ppt' && n.container === 'templates') {
    window.dispatchEvent(new CustomEvent('app:navigate-template-management', {
      detail: { section: 'ppt' },
    }))
    return
  }
  // 事务到期提醒（2.11 §3.2）：list_due_phase_tasks 已过滤 kind='project'，
  // container 恒 project；taskId 走 App tabHandler 既有定位链路（3.0 §6.1.8）
  if (n.entity_type === 'affair') {
    if (!n.project_id) return
    window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
      detail: { projectId: n.project_id, tab: 'affairs', container: 'project', taskId: n.entity_id },
    }))
    return
  }
  if (!n.project_id) return
  const tab = n.entity_type === 'file' || n.entity_type === 'project_export' || n.entity_type === 'project_import' ? 'materials'
    : n.entity_type === 'wiki_compile' ? 'wiki'
    : n.entity_type === 'form_fill' ? 'forms'
    : 'reports'
  window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
    detail: { projectId: n.project_id, tab, container: n.container ?? 'project' },
  }))
}

/** 无渲染组件：必须挂在 ActivityProvider + ToastProvider 内部 */
export function ActivityMonitor() {
  const { t } = useTranslation()
  const showToast = useToast()
  const { applyStatus, markRead, summary } = useActivity()

  // OS 通知设置缓存（2.11 §6.1）：挂载读一次，设置保存后经 app:settings-changed
  // （DOM 事件，SettingsPage doSave 派发）重读；读取失败/键缺失按后端默认开启
  // scheduleNotify：R4 订阅完成通知开关（review P2-7 提前落地），默认关闭 = 静默
  const osPrefsRef = useRef({ enabled: true, sound: true })
  const scheduleNotifyRef = useRef(false)
  useEffect(() => {
    let cancelled = false
    const load = () => {
      void window.electronAPI
        .invoke('settings:get')
        .then((res) => {
          if (cancelled) return
          const s = res as {
            os_notify_enabled?: boolean; os_notify_sound?: boolean
            schedule_notify?: boolean
          }
          osPrefsRef.current = {
            enabled: s?.os_notify_enabled !== false,
            sound: s?.os_notify_sound !== false,
          }
          scheduleNotifyRef.current = s?.schedule_notify === true
        })
        .catch(() => {
          /* 保持当前值（默认开启） */
        })
    }
    load()
    // DOM 事件监听（与 SettingsPage 的 window.dispatchEvent 同系统）：曾误用
    // electronAPI.on 监听 IPC 通道导致永不触发（设置保存后不生效，review C-1）
    window.addEventListener('app:settings-changed', load)
    return () => {
      cancelled = true
      window.removeEventListener('app:settings-changed', load)
    }
  }, [])

  // E3 activity:stream → 立即心跳入口（0.4 §6.4）：事件体不直接解析（Toast / 基线 /
  // markRead 与轮询共享同一实现，避免分叉），仅作触发信号；轮询 effect 内赋值
  const pollKickRef = useRef<() => void>(() => {})

  // 轮询间隔依赖最新 summary，用 ref 避免重建轮询循环
  const summaryRef = useRef(summary)
  summaryRef.current = summary
  // 活跃名单 ref：transfer（导出/导入）不进 summary 计数字段，直接从快照名单判定
  // 活跃期 5s 心跳（2.9 §7：统一长时活动模型，不降为二等活动）
  const activeRef = useRef<ActiveItem[]>([])

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
      const hasTransfer = activeRef.current.some(
        (a) => a.entity_type === 'project_export' || a.entity_type === 'project_import',
      )
      const hasActive = hasTransfer ||
        s.active_files > 0 ||
        s.active_compiles > 0 ||
        s.generating_reports > 0 ||
        s.running_tasks > 0 ||
        s.downloading_models > 0 ||
        s.analyzing_form_templates > 0 ||
        s.filling_forms > 0 ||
        s.active_ppts > 0 ||
        s.running_schedules > 0
      return hasActive ? ACTIVE_INTERVAL : IDLE_INTERVAL
    }

    // OS 系统通知（2.11 §4）：仅失焦/不可见时发（D3 前台已有 Toast 不重复打扰）；
    // 主进程无状态投递，点击经 app:osNotifyClick 回流导航
    const notifyOs = (title: string, body: string, nav: NotificationItem | null) => {
      if (!osPrefsRef.current.enabled) return
      if (!document.hidden && document.hasFocus()) return
      void window.electronAPI
        .invoke('app:osNotify', {
          title,
          body,
          silent: !osPrefsRef.current.sound,
          nav,
        })
        .catch(() => {
          /* OS 通知失败不影响应用内反馈（Toast/feed 已送达） */
        })
    }

    const dispatchToasts = (rawItems: NotificationItem[]) => {
      // R4（2.11 §5.5）：订阅完成默认 silent——不弹 Toast/OS 通知（通知中心
      // 仍可见，静默已读）；管理台红点/摘要行计数是完成态的消费入口。
      // schedule_notify 开启后解除静默（review P2-7 提前落地，设置保存经
      // app:settings-changed 即时生效）。
      // failed/paused 照常弹（失败/熔断必达）。
      const completedScheduleRun = (n: NotificationItem) =>
        n.entity_type === 'schedule_run' && n.kind === 'completed'
      const silent = scheduleNotifyRef.current
        ? []
        : rawItems.filter(completedScheduleRun)
      const items = rawItems.filter(
        (n) => !completedScheduleRun(n) || scheduleNotifyRef.current,
      )
      if (silent.length > 0) void markRead(silent.map((n) => n.id))
      if (items.length === 0) return
      if (items.length > BATCH_LIMIT) {
        const batchTitle = t('activity.batch', { count: items.length })
        showToast(batchTitle, 'info')
        notifyOs(batchTitle, '', null)
        void markRead(items.map((n) => n.id))
        return
      }
      for (const n of items) {
        // paused（报告级暂停，2.5 §3.1 修订）/ awaiting_input（PPT 门禁待确认，2.3 §3.4）
        // / due（事务今日到期，2.11 §3.2）用 info（蓝）；file_changed（模板源文件变更）
        // / overdue（事务已逾期）/ archive_failed（档案合并失败）/ missed（跳过）
        // / interrupted（中断清扫）用 warning（黄），与完成（绿）/失败（红）区分
        const type = n.kind === 'failed' ? 'error'
          : n.kind === 'paused' || n.kind === 'awaiting_input' || n.kind === 'due' ? 'info'
          : n.kind === 'file_changed' || n.kind === 'overdue' || n.kind === 'archive_failed'
            || n.kind === 'missed' || n.kind === 'interrupted' ? 'warning'
          : 'success'
        const title = t(`activity.${n.entity_type}.${n.kind}`, { title: n.entity_title || '' })
        showToast(
          title,
          type,
          {
            detail: n.detail || undefined,
            action: {
              label: t('activity.view'),
              onClick: () => navigateToNotification(n),
            },
          },
        )
        notifyOs(title, n.detail || '', n)
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
        activeRef.current = result.active ?? []

        // 新通知检测：unread + recent 合并后按 id 防重；
        // 首次拉取只建立基线不弹 Toast（避免启动时历史未读轰炸）。
        // 注意：此处不得 return——会跳过末尾调度导致循环死亡（曾致 UI 快照冻结），
        // 用 if/else 区分基线/正常路径，统一走末尾 setTimeout。
        const all = [...result.notifications.recent, ...result.notifications.unread]
        const fresh = all.filter((n) => !seenIds.has(n.id))
        for (const n of all) seenIds.add(n.id)
        if (baselineReady) {
          const unique = fresh.filter((n, i) => fresh.findIndex((x) => x.id === n.id) === i)
          if (unique.length > 0) {
            dispatchToasts(unique)
            // 2.10 §5.3 第三发信点：新终态条目 → 全局用量刷新信号（批量终态由
            // useUsageSummary 1.5s 去抖合并为一次重取；信号是加速器非正确性依赖）
            window.dispatchEvent(new Event('app:usage-changed'))
          }
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
