/** tests/usePolling.test.ts — usePolling 通用 hook 用例（0.4lifecycle-design.md §5.8，v1.7）
 *  覆盖：mount 立即执行、interval 调度、函数形态自适应、防重叠、指数退避、
 *  document.hidden 跳过+降频、visibilitychange 回前台立即刷新、enabled 启停与注册表清理、
 *  task 挂起超时退避（回归：底层 IPC 挂起不得永久杀死轮询循环）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { usePolling } from '../src/hooks/usePolling'

/** 排空 microtask（usePolling 的 tick 为 async，fake timers 不会自动推进） */
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
  // 恢复测试中覆盖的 document.hidden（jsdom 定义于 Document.prototype，实例属性删除即还原）
  delete (document as any).hidden
})

describe('usePolling', () => {
  it('runs task immediately on mount', async () => {
    const task = vi.fn().mockResolvedValue(undefined)
    renderHook(() => usePolling({ task, interval: 1000, name: 'poll-mount' }))
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
  })

  it('schedules subsequent runs after interval', async () => {
    const task = vi.fn().mockResolvedValue(undefined)
    renderHook(() => usePolling({ task, interval: 1000, name: 'poll-schedule' }))
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(task).toHaveBeenCalledTimes(2)
  })

  it('evaluates function-form interval per schedule', async () => {
    const task = vi.fn().mockResolvedValue(undefined)
    let fast = true
    renderHook(() =>
      usePolling({ task, interval: () => (fast ? 500 : 5000), name: 'poll-adaptive' }),
    )
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(task).toHaveBeenCalledTimes(2)
    // 切换为慢速：tick#2 已在 advance(500) 内按旧值调度(fast=true → @1000)，
    // tick#3 完成时重新求值 → 下一次用 5000（若不重新求值，tick#4 会在 1500 处提前触发）
    fast = false
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999)
    })
    expect(task).toHaveBeenCalledTimes(3)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(task).toHaveBeenCalledTimes(3)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(task).toHaveBeenCalledTimes(4)
  })

  it('does not overlap when previous run is still in flight', async () => {
    let resolveFirst!: () => void
    const task = vi
      .fn()
      .mockImplementationOnce(() => new Promise<void>((r) => { resolveFirst = r }))
      .mockResolvedValue(undefined)
    renderHook(() => usePolling({ task, interval: 500, name: 'poll-overlap' }))
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
    // 到点时首次仍在挂起：不重入
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(task).toHaveBeenCalledTimes(1)
    // 完成后按 interval 调度下一次
    await act(async () => {
      resolveFirst()
      await Promise.resolve()
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(task).toHaveBeenCalledTimes(2)
  })

  it('backs off exponentially on failures and resets on success', async () => {
    const task = vi
      .fn()
      .mockRejectedValueOnce(new Error('fail-1'))
      .mockRejectedValueOnce(new Error('fail-2'))
      .mockResolvedValue(undefined)
    renderHook(() => usePolling({ task, interval: 1000, name: 'poll-backoff' }))
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
    // 失败 1 次 → 下一次 2×1000ms
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999)
    })
    expect(task).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(task).toHaveBeenCalledTimes(2)
    // 失败 2 次 → 下一次 4×1000ms
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3999)
    })
    expect(task).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(task).toHaveBeenCalledTimes(3)
    // 成功 → 退避重置回基础间隔
    await act(async () => {
      await vi.advanceTimersByTimeAsync(999)
    })
    expect(task).toHaveBeenCalledTimes(3)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(task).toHaveBeenCalledTimes(4)
  })

  it('caps backoff at maxBackoffMs', async () => {
    // interval 5000 × mult 16 = 80000 > 60000 → 封顶生效，每次恰为 60s 一轮
    const task = vi.fn().mockRejectedValue(new Error('always-fail'))
    renderHook(() =>
      usePolling({ task, interval: 5000, name: 'poll-cap', backoffMultipliers: [16] }),
    )
    await flush()
    // 首败在 mount 时已发生，后续每 60s 恰好执行一次
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
      })
      await flush()
    }
    const countAtCap = task.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(59_999)
    })
    expect(task.mock.calls.length).toBe(countAtCap)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(task.mock.calls.length).toBe(countAtCap + 1)
  })

  it('skips execution and multiplies interval while document is hidden', async () => {
    let hidden = true
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    const task = vi.fn().mockResolvedValue(undefined)
    renderHook(() => usePolling({ task, interval: 1000, name: 'poll-hidden' }))
    await flush()
    // 隐藏时不执行任务（保心跳）
    expect(task).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2999)
    })
    expect(task).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(task).not.toHaveBeenCalled()
    // 回前台：visibilitychange 立即刷新一次
    hidden = false
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await Promise.resolve()
    })
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
  })

  it('treats a hung task as failure and keeps polling after taskTimeoutMs', async () => {
    // 模拟底层 IPC 永久挂起（如 Node fetch 复用半关闭 keep-alive 连接且无超时）：
    // 超时判定失败 → 退避 → 继续轮询，循环不得死亡
    const task = vi.fn().mockImplementation(() => new Promise<void>(() => {}))
    renderHook(() =>
      usePolling({ task, interval: 1000, name: 'poll-hang', taskTimeoutMs: 5000 }),
    )
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
    // 5s 超时判定失败 → 退避 2× → 2s 后第二次执行
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    await flush()
    expect(task).toHaveBeenCalledTimes(2)
    // 第二次仍挂起 → 超时 → fails=2 → 退避 4× → 4s 后第三次执行
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    await flush()
    expect(task).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000)
    })
    await flush()
    expect(task).toHaveBeenCalledTimes(3)
  })

  it('starts/stops with enabled and cleans up registry on unmount', async () => {
    const task = vi.fn().mockResolvedValue(undefined)
    const { rerender, unmount } = renderHook(
      ({ enabled }) => usePolling({ task, interval: 1000, name: 'poll-enabled', enabled }),
      { initialProps: { enabled: false } },
    )
    await flush()
    expect(task).not.toHaveBeenCalled()
    expect(window.__inkcubePollings?.has('poll-enabled')).toBe(false)
    rerender({ enabled: true })
    await flush()
    expect(task).toHaveBeenCalledTimes(1)
    expect(window.__inkcubePollings?.get('poll-enabled')?.name).toBe('poll-enabled')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(task).toHaveBeenCalledTimes(2)
    unmount()
    expect(window.__inkcubePollings?.has('poll-enabled')).toBe(false)
    // 卸载后不再执行
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(task).toHaveBeenCalledTimes(2)
  })
})
