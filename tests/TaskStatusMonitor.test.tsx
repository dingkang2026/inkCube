/** tests/TaskStatusMonitor.test.tsx — 任务状态轮询谓词收编回归（0.4 §8.1 #2，S5）
 *
 * #2 谓词从 App 手动布尔（activeNav==='tasks' || taskIsGenerating）收编为
 * useActivity('task') 心跳派生：后台任务（非本窗口发起 / 崩溃恢复后重启）
 * 同样驱动轮询。覆盖：全 false 基线（无轮询 + 重置派生标记 + 挂载 500ms 补拉）、
 * 心跳出现立即首跑、心跳翻 false 终态边沿补拉恰一次且轮询自停、
 * 任务页激活 3s 轮询、本地生成结束 500ms 延迟补拉（SSE done 早于 finally 清理）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { TaskStatusMonitor } from '../src/features/task/TaskStatusMonitor'

// 心跳可控：useActivity('task') 的 isActive 由测试翻转（vi.mock 工厂经 vi.hoisted 取状态）
const activityState = vi.hoisted(() => ({ isActive: false }))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ isActive: activityState.isActive }),
}))

const RUNNING_STATUS = { can_start: false, running_task_ids: ['t1'] }
const RESET_STATUS = { can_start: true, running_task_ids: [] }

/** 排空 microtask（fetchStatus 为 async，fake timers 不会自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

interface Props {
  activeOnTasks: boolean
  localGenerating: boolean
  onStatus: (status: unknown) => void
}

const props = (over: Partial<Props> = {}): Props => ({
  activeOnTasks: false,
  localGenerating: false,
  onStatus: vi.fn(),
  ...over,
})

const wireWindow = (status: unknown) => {
  const invoke = vi.fn(() => Promise.resolve(status))
  ;(window as { electronAPI?: unknown }).electronAPI = { invoke }
  return invoke
}

beforeEach(() => {
  vi.useFakeTimers()
  activityState.isActive = false
})

afterEach(() => {
  vi.useRealTimers()
  delete (window as { electronAPI?: unknown }).electronAPI
  window.__inkcubePollings?.clear()
})

describe('TaskStatusMonitor', () => {
  it('idle baseline: no polling, resets derived flags, one mount catch-up at 500ms', async () => {
    const invoke = wireWindow(RUNNING_STATUS)
    const onStatus = vi.fn()
    render(<TaskStatusMonitor {...props({ onStatus })} />)
    await flush()
    // 轮询未启动；重置派生标记（原 App shouldPoll=false 分支）
    expect(invoke).toHaveBeenCalledTimes(0)
    expect(onStatus).toHaveBeenCalledWith(RESET_STATUS)
    // 挂载 500ms 补拉（原 App 行为保留：localGenerating 初始 false 即设 timer）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(499)
    })
    expect(invoke).toHaveBeenCalledTimes(0)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1)
    // 无轮询：长时间推进不再有 invoke
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1)
  })

  it('heartbeat-derived predicate: activity appearing starts polling immediately', async () => {
    const invoke = wireWindow(RUNNING_STATUS)
    const onStatus = vi.fn()
    const utils = render(<TaskStatusMonitor {...props({ onStatus })} />)
    await flush()
    expect(invoke).toHaveBeenCalledTimes(0)
    // 心跳出现：后台任务运行中（非本窗口发起）→ enabled 翻真 → 立即首跑
    activityState.isActive = true
    await act(async () => {
      utils.rerender(<TaskStatusMonitor {...props({ onStatus })} />)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1)
    expect(onStatus).toHaveBeenCalledWith(RUNNING_STATUS)
    // 3s 间隔持续轮询（t=3000 轮询 + 挂载 500ms 补拉）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(3)
  })

  it('terminal edge: activity clearing triggers one catch-up and stops polling', async () => {
    const invoke = wireWindow(RUNNING_STATUS)
    const onStatus = vi.fn()
    activityState.isActive = true
    const utils = render(<TaskStatusMonitor {...props({ onStatus })} />)
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1) // 轮询首跑
    // 先让挂载 500ms 补拉跑完，排除与边沿计数的交叉
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(2)
    // 心跳翻 false：边沿补拉恰一次 + 重置派生标记 + 轮询自停
    activityState.isActive = false
    await act(async () => {
      utils.rerender(<TaskStatusMonitor {...props({ onStatus })} />)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(3)
    expect(onStatus).toHaveBeenCalledWith(RESET_STATUS)
    expect(onStatus).toHaveBeenLastCalledWith(RUNNING_STATUS) // 补拉结果最终生效
    // 轮询已停（§6.3 边沿后 enabled=false）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(3)
  })

  it('tasks page active: polls at 3s interval', async () => {
    const invoke = wireWindow(RUNNING_STATUS)
    render(<TaskStatusMonitor {...props({ activeOnTasks: true })} />)
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1) // enabled 挂载即真 → 立即首跑
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    await flush()
    // t=500 挂载补拉 + t=3000 轮询第二次
    expect(invoke).toHaveBeenCalledTimes(3)
  })

  it('local generation ending: catch-up once after 500ms delay', async () => {
    const invoke = wireWindow(RUNNING_STATUS)
    const onStatus = vi.fn()
    const utils = render(<TaskStatusMonitor {...props({ localGenerating: true, onStatus })} />)
    await flush()
    // 生成中：localGenerating 本身是谓词一项 → 轮询立即首跑（生成期即时刷新状态）
    expect(invoke).toHaveBeenCalledTimes(1)
    // 生成结束（SSE done）：轮询自停（enabled 翻 false），500ms 延迟后补拉恰一次
    // （立即拉会读到 finally 清理 running 集之前的旧值，故带延迟）
    await act(async () => {
      utils.rerender(<TaskStatusMonitor {...props({ localGenerating: false, onStatus })} />)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(499)
    })
    expect(invoke).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(2)
    expect(onStatus).toHaveBeenCalledWith(RUNNING_STATUS)
  })
})
