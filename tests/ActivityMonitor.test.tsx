/** tests/ActivityMonitor.test.tsx — ActivityMonitor 回归用例（0.4lifecycle-design.md §5.5）
 *  核心回归：底层 IPC 挂起（invoke 永不 settle，如 Node fetch 复用半关闭
 *  keep-alive 连接且无超时）时，轮询循环不得死亡——INVOKE_TIMEOUT 超时判定
 *  失败 → 指数退避 → 继续轮询。防止「任务完成后首页/徽章快照冻结」复发。
 *  另覆盖正常路径：成功拉取后按空闲间隔调度下一次。
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
})

describe('ActivityMonitor', () => {
  it('polls on success and schedules next run at idle interval', async () => {
    const invoke = vi.fn().mockResolvedValue(IDLE_STATUS)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke }
    render(<ActivityMonitor />)
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1)
    // 空闲状态无进行中活动 → 15s 后下一次
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(2)
  })

  it('keeps polling when invoke hangs forever (regression: poll loop death)', async () => {
    // 模拟 IPC 永久挂起：invoke 返回永不 settle 的 promise
    const invoke = vi.fn().mockImplementation(() => new Promise(() => {}))
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke }
    render(<ActivityMonitor />)
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1)
    // 8s 超时判定失败 → 退避首档 5s → 13s 处第二次 invoke（循环未死）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(2)
    // 第二次仍挂起 → 超时 → 退避 10s → 第三次 invoke
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000)
    })
    await flush()
    expect(invoke).toHaveBeenCalledTimes(3)
  })
})
