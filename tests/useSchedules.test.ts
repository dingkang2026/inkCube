/** tests/useSchedules.test.ts — 定时订阅摘要数据 hook（2.11 E3 T6）
 *  覆盖：挂载拉 schedules:summary；task 活动 isActive true→false 终态边沿
 *  补拉恰一次（0.4 §6.3，影子任务完成 → 未读计数上屏）；invoke 失败降级
 *  零值不抛（侧栏不阻塞）；refresh 手动重拉供写操作后调用。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useSchedules } from '../src/features/schedule/useSchedules'

const activityState = vi.hoisted(() => ({ isActive: false }))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ isActive: activityState.isActive }),
}))

const TOTALS = { n: 3, total: 2, abnormal: 1 }

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockResolvedValue({ totals: TOTALS })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
  activityState.isActive = false
})

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('useSchedules', () => {
  it('fetches summary on mount', async () => {
    const { result } = renderHook(() => useSchedules())
    await waitFor(() => expect(result.current.totals).toEqual(TOTALS))
    expect(invokeMock).toHaveBeenCalledWith('schedules:summary')
  })

  it('refetches once on task activity terminal edge (shadow task finished)', async () => {
    activityState.isActive = true
    const { result, rerender } = renderHook(() => useSchedules())
    await waitFor(() => expect(result.current.totals).toEqual(TOTALS))
    invokeMock.mockClear()
    activityState.isActive = false
    rerender()
    await waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(1))
    expect(invokeMock).toHaveBeenCalledWith('schedules:summary')
  })

  it('degrades to zero totals when invoke fails (sidebar unblocked)', async () => {
    invokeMock.mockRejectedValue(new Error('backend down'))
    const { result } = renderHook(() => useSchedules())
    await waitFor(() => expect(invokeMock).toHaveBeenCalled())
    expect(result.current.totals).toEqual({ n: 0, total: 0, abnormal: 0 })
  })

  it('manual refresh re-pulls summary', async () => {
    const { result } = renderHook(() => useSchedules())
    await waitFor(() => expect(result.current.totals).toEqual(TOTALS))
    invokeMock.mockClear()
    invokeMock.mockResolvedValue({ totals: { n: 5, total: 0, abnormal: 0 } })
    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.totals).toEqual({ n: 5, total: 0, abnormal: 0 })
  })
})
