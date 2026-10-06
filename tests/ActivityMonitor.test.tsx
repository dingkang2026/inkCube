/** tests/ActivityMonitor.test.tsx — ActivityMonitor 回归用例（0.4lifecycle-design.md §5.5 / §6.4 E3）
 *  核心回归：底层 IPC 挂起（invoke 永不 settle，如 Node fetch 复用半关闭
 *  keep-alive 连接且无超时）时，轮询循环不得死亡——INVOKE_TIMEOUT 超时判定
 *  失败 → 指数退避 → 继续轮询。防止「任务完成后首页/徽章快照冻结」复发。
 *  另覆盖正常路径：成功拉取后按空闲间隔调度下一次。
 *  另覆盖可见性：窗口不可见（Windows 遮挡检测 hidden=true）时仍拉取且降频 45s、
 *  恢复可见立即拉取（0.4 附录 A 决策表「禁止 hidden 早退跳过拉取」）。
 *  E3：activity:stream 事件到达 → 200ms 节流后立即心跳（Toast 即时化）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { ActivityMonitor } from '../src/features/activity/ActivityMonitor'
import type { ActivityStatus } from '../src/features/activity/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({
    applyStatus: vi.fn(),
    markRead: vi.fn().mockResolvedValue(undefined),
    summary: {
      active_files: 0,
      active_compiles: 0,
      generating_reports: 0,
      running_tasks: 0,
      downloading_models: 0,
    },
  }),
}))

const IDLE_STATUS = {
  notifications: { unread: [], recent: [] },
} as unknown as ActivityStatus

/** invoke 按 channel 分流：activities:getStatus 返回状态，其余（subscribeStream）返回 ok */
const wireInvoke = () =>
  vi.fn((channel: string) =>
    channel === 'activities:getStatus'
      ? Promise.resolve(IDLE_STATUS)
      : Promise.resolve({ ok: true }),
  )

/** activities:getStatus 通道的调用次数（SSE 订阅 invoke 不计入心跳计数） */
const heartbeatCalls = (invoke: ReturnType<typeof wireInvoke>) =>
  invoke.mock.calls.filter((c) => c[0] === 'activities:getStatus').length

/** 排空 microtask（poll 为 async，fake timers 不会自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  delete (window as { electronAPI?: unknown }).electronAPI
  // 移除用例内定义的实例属性，恢复 jsdom 原型上的 hidden getter
  delete (document as { hidden?: unknown }).hidden
})

describe('ActivityMonitor', () => {
  it('polls on success and schedules next run at idle interval', async () => {
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    // 空闲状态无进行中活动 → 15s 后下一次
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  it('keeps polling when invoke hangs forever (regression: poll loop death)', async () => {
    // 模拟 IPC 永久挂起：getStatus 返回永不 settle 的 promise（其余通道正常）
    const invoke = vi.fn((channel: string) =>
      channel === 'activities:getStatus'
        ? new Promise(() => {})
        : Promise.resolve({ ok: true }),
    )
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    // 8s 超时判定失败 → 退避首档 5s → 13s 处第二次 invoke（循环未死）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
    // 第二次仍挂起 → 超时 → 退避 10s → 第三次 invoke
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(3)
  })

  it('still polls when document.hidden (regression: occluded-window heartbeat stall)', async () => {
    // Windows Chromium 遮挡检测：被完全遮挡的窗口 document.hidden=true，
    // 旧实现早退跳过拉取 → 心跳断流（进度冻结 0% / 呼吸点消失 / Toast 延迟到置前）
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    // hidden 时挂载仍立即首拉（不跳过）
    expect(heartbeatCalls(invoke)).toBe(1)
    // 不可见降频 45s（IDLE 15s ×3）：44.9s 不拉，45s 整点拉
    await act(async () => {
      await vi.advanceTimersByTimeAsync(44999)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  it('polls immediately when window becomes visible again', async () => {
    // 遮挡期降频 45s；恢复可见立即拉取（visibilitychange），不等 timer 到点
    let hidden = true
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    // 置前：hidden→false + visibilitychange → 立即再拉（不推进 timer）
    hidden = false
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  it('subscribes activity stream and kicks immediate heartbeat on event (E3)', async () => {
    // 终态事件到达 → 200ms 节流 → 立即一次聚合拉取（Toast 即时化，不等 15s 心跳）
    let streamHandler: (() => void) | null = null
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = {
      invoke,
      on: (channel: string, cb: () => void) => {
        if (channel === 'activity:stream') streamHandler = cb
        return () => {}
      },
    }
    render(<ActivityMonitor />)
    await flush()
    // 挂载即订阅 SSE（E3）+ 首次心跳
    expect(invoke).toHaveBeenCalledWith('activities:subscribeStream')
    expect(heartbeatCalls(invoke)).toBe(1)

    // 事件到达：200ms 节流窗口内不拉，窗口结束后立即拉
    await act(async () => {
      streamHandler!()
      await vi.advanceTimersByTimeAsync(199)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)

    // 事件风暴（batch 完成）在节流窗口内合并为一次拉取
    await act(async () => {
      streamHandler!()
      streamHandler!()
      streamHandler!()
      await vi.advanceTimersByTimeAsync(200)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(3)
  })

  it('does not fork polling chain when kick arrives during in-flight poll (regression: chain fork)', async () => {
    // P1-1 回归（v3.9 修复）：poll 无在飞守卫时，kick / visibilitychange 在 poll
    // 在飞期间 void poll() 会分叉出第二条链式调度链（尾部无条件 setTimeout，
    // clearTimeout 只能清到最后一条），频率只增不减；修复后在飞触发仅标记补拉，
    // 完成后 0ms 补一轮，单链语义不变
    let resolveFirst!: (v: unknown) => void
    const first = new Promise((r) => { resolveFirst = r })
    let call = 0
    const invoke = vi.fn((channel: string) => {
      if (channel !== 'activities:getStatus') return Promise.resolve({ ok: true })
      call += 1
      return call === 1 ? first : Promise.resolve(IDLE_STATUS)
    })
    let streamHandler: (() => void) | null = null
    ;(window as { electronAPI?: unknown }).electronAPI = {
      invoke,
      on: (channel: string, cb: () => void) => {
        if (channel === 'activity:stream') streamHandler = cb
        return () => {}
      },
    }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1) // 首拉在飞（pending）

    // SSE 事件 → 200ms 后 kick，此刻首拉仍在飞：不并发启动第二条链
    await act(async () => {
      streamHandler!()
      await vi.advanceTimersByTimeAsync(200)
    })
    expect(heartbeatCalls(invoke)).toBe(1)

    // 首拉完成 → 在飞期间标记的补拉立即执行（0ms，终态即时性不丢）
    await act(async () => {
      resolveFirst(IDLE_STATUS)
      await Promise.resolve()
    })
    await flush() // finally 完成，0ms 补拉 timer 已注册
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)

    // 推进一个空闲周期（15s）：单链恰 +1（分叉实现会双链各拉一次 → 4）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(3)
  })
})
