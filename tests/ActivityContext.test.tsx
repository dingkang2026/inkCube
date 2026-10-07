/** tests/ActivityContext.test.tsx — ActivityContext 用例（0.4lifecycle-design.md §6.2，v1.7）
 *  覆盖：applyStatus → status/summary/feed/byProject 派生（含 container 分流）、feed 去重降序、
 *  markRead 乐观更新（含 IPC 失败不影响 UI）、无 Provider 抛错。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { ActivityProvider, useActivity } from '../src/features/activity/ActivityContext'
import type { ActivityStatus, NotificationItem } from '../src/features/activity/types'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  ;(window as any).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  delete (window as any).electronAPI
})

function notif(partial: Partial<NotificationItem>): NotificationItem {
  return {
    id: 1,
    entity_type: 'file',
    entity_id: 'e1',
    project_id: 'p1',
    kind: 'completed',
    entity_title: '标题',
    detail: null,
    read_at: null,
    created_at: new Date().toISOString(),
    ...partial,
  }
}

function makeStatus(partial: {
  unread?: NotificationItem[]
  recent?: NotificationItem[]
  active?: ActivityStatus['active']
}): ActivityStatus {
  return {
    active: partial.active ?? [],
    notifications: {
      unread: partial.unread ?? [],
      recent: partial.recent ?? [],
    },
    summary: {
      active_files: 2,
      active_compiles: 1,
      generating_reports: 1,
      running_tasks: 0,
      downloading_models: 0,
    },
  }
}

describe('ActivityContext', () => {
  it('applyStatus populates status/summary/feed', () => {
    const { result } = renderHook(() => useActivity(), { wrapper: ActivityProvider })
    expect(result.current.status).toBeNull()
    expect(result.current.summary.active_files).toBe(0)
    act(() => {
      result.current.applyStatus(makeStatus({ unread: [notif({ id: 5 })] }))
    })
    expect(result.current.status).not.toBeNull()
    expect(result.current.summary.active_files).toBe(2)
    // 未读态由条目自身 read_at 承载（unreadIds 已移除，0.4 §6.2）
    expect(result.current.feed.find((n) => n.id === 5)?.read_at).toBeNull()
  })

  it('feed merges unread+recent with dedupe and descending id order', () => {
    const { result } = renderHook(() => useActivity(), { wrapper: ActivityProvider })
    act(() => {
      result.current.applyStatus(
        makeStatus({
          unread: [notif({ id: 2 }), notif({ id: 9 })],
          recent: [notif({ id: 2 }), notif({ id: 1 })],
        }),
      )
    })
    // id=2 同现两侧 → 去重保留一份；降序排列
    expect(result.current.feed.map((n) => n.id)).toEqual([9, 2, 1])
  })

  it('byProject aggregates per-project counts', () => {
    const { result } = renderHook(() => useActivity(), { wrapper: ActivityProvider })
    act(() => {
      result.current.applyStatus(
        makeStatus({
          active: [
            { entity_type: 'file', project_id: 'p1', project_name: 'P1', count: 3, image_describing: 1 },
            { entity_type: 'wiki_compile', project_id: 'p1', status: 'ingesting' },
            { entity_type: 'report', project_id: 'p2', project_name: 'P2' },
            { entity_type: 'file', project_id: 'lib1', project_name: 'L1', container: 'baselib', count: 2 },
          ],
        }),
      )
    })
    const p1 = result.current.byProject.get('p1')
    expect(p1).toMatchObject({
      projectId: 'p1',
      files: 3,
      imageDescribing: 1,
      compiles: 1,
    })
    expect(result.current.byProject.get('p2')?.reports).toBe(1)
    // container 分流（0.4 §3.6）：基础库活动进桶时标记 baselib，供 BaseLibFooter 活跃点取数
    const lib = result.current.byProject.get('lib1')
    expect(lib?.container).toBe('baselib')
    expect(lib?.files).toBe(2)
    // 未标 container 的活跃项默认 project
    expect(result.current.byProject.get('p1')?.container).toBe('project')
    expect(result.current.activeProjectIds.has('p1')).toBe(true)
    expect(result.current.activeProjectIds.has('p2')).toBe(true)
  })

  it('markRead invokes IPC and applies optimistic update', async () => {
    invokeMock.mockResolvedValue(null)
    const { result } = renderHook(() => useActivity(), { wrapper: ActivityProvider })
    act(() => {
      result.current.applyStatus(makeStatus({ unread: [notif({ id: 3 }), notif({ id: 4 })] }))
    })
    await act(async () => {
      await result.current.markRead([3])
    })
    expect(invokeMock).toHaveBeenCalledWith('notifications:markRead', { ids: [3] })
    // 未读态由条目自身 read_at 承载：id=3 已读、id=4 仍末读
    expect(result.current.feed.find((n) => n.id === 3)?.read_at).toBeTruthy()
    expect(result.current.feed.find((n) => n.id === 4)?.read_at).toBeNull()
  })

  it('markRead keeps optimistic update even when IPC fails', async () => {
    invokeMock.mockRejectedValue(new Error('ipc down'))
    const { result } = renderHook(() => useActivity(), { wrapper: ActivityProvider })
    act(() => {
      result.current.applyStatus(makeStatus({ unread: [notif({ id: 8 })] }))
    })
    await act(async () => {
      await result.current.markRead([8])
    })
    // best-effort：失败不影响 UI（0.4 §6.2）
    expect(result.current.feed.find((n) => n.id === 8)?.read_at).toBeTruthy()
  })

  it('throws when useActivity is used without provider', () => {
    expect(() => renderHook(() => useActivity())).toThrow(
      'useActivity must be used within ActivityProvider',
    )
  })
})
