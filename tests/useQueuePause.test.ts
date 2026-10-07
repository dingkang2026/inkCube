/** tests/useQueuePause.test.ts — 队列暂停开关 hook（0.4 §队列暂停）
 *  覆盖：初始态从 settings 读取（暂停持久化于 settings，重启保持）、
 *  toggle 经 knowledge:queuePause 写后端 + 翻转本地态、busy 防重入、
 *  settings 读取失败时降级为未暂停（不阻塞页面）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useQueuePause } from '../src/hooks/useQueuePause'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('useQueuePause', () => {
  it('reads the persisted pause flag from settings on mount', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'settings:get') return { parsing_paused: true, describe_paused: false }
      return {}
    })
    const a = renderHook(() => useQueuePause('parsing'))
    await waitFor(() => expect(a.result.current.paused).toBe(true))
    const b = renderHook(() => useQueuePause('describe'))
    await waitFor(() => expect(b.result.current.paused).toBe(false))
  })

  it('toggles via knowledge:queuePause and flips local state', async () => {
    invokeMock.mockResolvedValue({})
    const { result } = renderHook(() => useQueuePause('describe'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('settings:get'))
    await act(async () => {
      await result.current.toggle()
    })
    expect(invokeMock).toHaveBeenCalledWith('knowledge:queuePause', { kind: 'describe', paused: true })
    expect(result.current.paused).toBe(true)
  })

  it('ignores re-entrant toggle while busy', async () => {
    let resolvePause!: () => void
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:queuePause') {
        return new Promise<void>((resolve) => { resolvePause = resolve }).then(() => ({}))
      }
      return {}
    })
    const { result } = renderHook(() => useQueuePause('report'))
    await waitFor(() => expect(result.current.busy).toBe(false))
    let first!: Promise<void>
    act(() => { first = result.current.toggle() })
    expect(result.current.busy).toBe(true)
    // busy 期间的第二次 toggle 直接 return（不发起第二次 IPC）
    await act(async () => { await result.current.toggle() })
    expect(invokeMock.mock.calls.filter((c) => c[0] === 'knowledge:queuePause').length).toBe(1)
    resolvePause()
    await act(async () => { await first })
    expect(result.current.paused).toBe(true)
    expect(result.current.busy).toBe(false)
  })

  it('survives settings load failure (defaults to not paused)', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'settings:get') throw new Error('backend down')
      return {}
    })
    const { result } = renderHook(() => useQueuePause('parsing'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalled())
    expect(result.current.paused).toBe(false)
  })
})
