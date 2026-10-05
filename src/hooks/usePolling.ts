/** src/hooks/usePolling.ts — 通用轮询 hook（0.4lifecycle-design.md §5.8，v1.7）
 *
 * 统一收敛页面级轮询（#2/#4-#12）的四项能力：
 *  - 防重叠：setTimeout 链式调度，上一次 task 未完成不调度下一次；
 *  - 指数退避：task 抛错视为失败，间隔按 backoffMultipliers 递增（成功即重置）；
 *  - 可见性感知：document.hidden 时跳过执行并按 hiddenMultiplier 降频保心跳，
 *    visibilitychange 回前台立即刷新一次；
 *  - dev 注册表：window.__inkcubePollings（name → 状态）供运行期排查。
 *
 * 约定：task 抛错 = 本次失败（触发退避）；task 内部自行 catch 且不抛出 = 视为成功。
 * 防挂起：task 超过 taskTimeoutMs 未 settle 视为失败（触发退避），防止底层 IPC 挂起
 * （如 Node fetch 复用半关闭 keep-alive 连接）永久杀死轮询循环——
 * 与防重叠共同保证循环活性：上一次未完成不发起下一次，但「未完成」不可能无限期。
 * interval 支持函数（自适应频率，如 Wiki 编译 5/10/30s），每次调度时求值；
 * interval 变化经 ref 生效（下一次调度读取新值），不重建轮询循环。
 */
import { useEffect, useRef } from 'react'

export interface UsePollingOptions {
  /** 轮询任务；抛错视为失败（触发退避） */
  task: () => Promise<void> | void
  /** 基础间隔 ms；函数形态用于自适应频率（每次调度求值） */
  interval: number | (() => number)
  /** 注册表键名（唯一，dev 排查用） */
  name: string
  /** 是否启用；false 时停止，true 时（重新）立即启动。默认 true */
  enabled?: boolean
  /** 失败退避倍数步进（× interval），默认 [2, 4, 8, 16]，越界取末位 */
  backoffMultipliers?: number[]
  /** 退避封顶 ms，默认 60s */
  maxBackoffMs?: number
  /** 页面隐藏时的间隔倍数，默认 3 */
  hiddenMultiplier?: number
  /** 单次 task 超时 ms（防底层 IPC 挂起永久杀死循环），默认 15s */
  taskTimeoutMs?: number
}

/** dev 注册表条目（window.__inkcubePollings） */
export interface PollingRegistryEntry {
  name: string
  /** 下一次调度的实际间隔（含退避/降频后） */
  intervalMs: number
  /** 连续失败次数 */
  fails: number
  /** 最近一次执行完成时间戳 */
  lastRunAt: number | null
}

declare global {
  interface Window {
    __inkcubePollings?: Map<string, PollingRegistryEntry>
  }
}

const DEFAULT_BACKOFF: number[] = [2, 4, 8, 16]
const DEFAULT_MAX_BACKOFF_MS = 60_000
const DEFAULT_HIDDEN_MULTIPLIER = 3
const DEFAULT_TASK_TIMEOUT_MS = 15_000

export function usePolling({
  task,
  interval,
  name,
  enabled = true,
  backoffMultipliers = DEFAULT_BACKOFF,
  maxBackoffMs = DEFAULT_MAX_BACKOFF_MS,
  hiddenMultiplier = DEFAULT_HIDDEN_MULTIPLIER,
  taskTimeoutMs = DEFAULT_TASK_TIMEOUT_MS,
}: UsePollingOptions): void {
  // task/interval 经 ref 读取最新值，避免调用方传 inline 函数导致轮询循环重建
  const taskRef = useRef(task)
  taskRef.current = task
  const intervalRef = useRef(interval)
  intervalRef.current = interval

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let taskTimer: ReturnType<typeof setTimeout> | null = null
    let fails = 0
    let running = false

    // dev 注册表（开销可忽略；测试/生产同样可用，便于问题定位）
    const registry = (window.__inkcubePollings ??= new Map<string, PollingRegistryEntry>())
    const entry: PollingRegistryEntry = { name, intervalMs: 0, fails: 0, lastRunAt: null }
    registry.set(name, entry)

    const baseInterval = () => {
      const v = intervalRef.current
      return typeof v === 'function' ? v() : v
    }

    const schedule = () => {
      if (cancelled) return
      let delay = baseInterval()
      if (fails > 0) {
        const mult =
          backoffMultipliers[Math.min(fails - 1, backoffMultipliers.length - 1)] ??
          backoffMultipliers[backoffMultipliers.length - 1]
        delay = Math.min(delay * mult, maxBackoffMs)
      }
      if (document.hidden) delay *= hiddenMultiplier
      entry.intervalMs = delay
      entry.fails = fails
      timer = setTimeout(tick, delay)
    }

    const tick = async () => {
      if (cancelled) return
      if (document.hidden) {
        // 隐藏时不请求，保持最低心跳（回前台由 visibilitychange 立即刷新）
        schedule()
        return
      }
      if (running) {
        // 双保险：链式调度下理论不可达
        schedule()
        return
      }
      running = true
      try {
        // 防挂起：task 未在时限内 settle 视为失败（退避重试），循环不可能被底层挂起杀死
        await Promise.race([
          Promise.resolve(taskRef.current()),
          new Promise<never>((_, reject) => {
            taskTimer = setTimeout(
              () => reject(new Error(`polling task timeout: ${name}`)),
              taskTimeoutMs,
            )
          }),
        ])
        if (!cancelled) fails = 0
      } catch {
        if (!cancelled) fails += 1
      } finally {
        if (taskTimer) clearTimeout(taskTimer)
        taskTimer = null
        running = false
        entry.lastRunAt = Date.now()
        schedule()
      }
    }

    const onVisibility = () => {
      if (cancelled || document.hidden) return
      // 回前台：丢弃等待中的低频调度，立即刷新一次
      if (timer) clearTimeout(timer)
      void tick()
    }
    document.addEventListener('visibilitychange', onVisibility)

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      if (taskTimer) clearTimeout(taskTimer)
      document.removeEventListener('visibilitychange', onVisibility)
      registry.delete(name)
    }
    // 仅 enabled/name 变化时重建循环；task/interval 经 ref 动态读取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, name])
}
