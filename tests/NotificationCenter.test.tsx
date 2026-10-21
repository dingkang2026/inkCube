/** tests/NotificationCenter.test.tsx — 通知中心全局面板（2.11 §6.1 D）
 *  覆盖：打开拉首页分页 / 加载更多按 offset 追加（含与 feed 新到达交错的去重，review M-1） /
 *  feed 实时注入去重上浮 /
 *  全部已读（markAllRead 一次 IPC）/ 条目点击（markRead + 关面板 + 导航分流）/ 空态。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))

const activityMocks = vi.hoisted(() => ({
  feed: { current: [] as unknown[] },
  markRead: vi.fn(async () => {}),
  markAllRead: vi.fn(async () => {}),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({
    feed: activityMocks.feed.current,
    markRead: activityMocks.markRead,
    markAllRead: activityMocks.markAllRead,
  }),
}))
const cssMock = vi.hoisted(() => ({ default: new Proxy({}, { get: (_t: unknown, k: unknown) => String(k) }) }))
vi.mock('../src/features/activity/NotificationCenter.module.css', () => cssMock)
vi.mock('../src/features/activity/NotificationRow.module.css', () => cssMock)

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { NotificationCenter } from '../src/features/activity/NotificationCenter'
import type { NotificationItem } from '../src/features/activity/types'

const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve() }) }

const mk = (id: number, over: Partial<NotificationItem> = {}): NotificationItem => ({
  id,
  entity_type: 'report',
  entity_id: `r${id}`,
  project_id: 'p1',
  kind: 'completed',
  entity_title: `报告${id}`,
  detail: null,
  read_at: null,
  created_at: new Date().toISOString(),
  ...over,
})

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  activityMocks.feed.current = []
  activityMocks.markRead.mockClear()
  activityMocks.markAllRead.mockClear()
  ;(window as { electronAPI?: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as { electronAPI?: unknown }).electronAPI
  vi.restoreAllMocks()
})

describe('NotificationCenter (2.11 §6.1 D)', () => {
  it('打开拉首页分页渲染条目；未满一页时不显示加载更多', async () => {
    invokeMock.mockResolvedValue({ items: [mk(3), mk(2)], total: 2 })
    const { container } = render(<NotificationCenter open onClose={() => {}} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('notifications:list', { offset: 0, limit: 50 })
    expect(container.textContent).toContain('报告3')
    expect(container.textContent).toContain('报告2')
    expect(container.textContent).not.toContain('notificationCenter.loadMore')
    expect(container.textContent).not.toContain('notificationCenter.empty')
  })

  it('加载更多按 offset 追加下一页，取完后按钮消失', async () => {
    invokeMock.mockImplementation(async (_ch: string, args?: { offset?: number }) =>
      (args?.offset ?? 0) === 0
        ? { items: [mk(3), mk(2)], total: 3 }
        : { items: [mk(1)], total: 3 })
    const { container, getByText } = render(<NotificationCenter open onClose={() => {}} />)
    await flush()
    fireEvent.click(getByText('notificationCenter.loadMore'))
    await flush()
    expect(invokeMock).toHaveBeenLastCalledWith('notifications:list', { offset: 2, limit: 50 })
    expect(container.textContent).toContain('报告1')
    expect(container.textContent).not.toContain('notificationCenter.loadMore')  // paged 3 = total 3
  })

  it('加载更多与新到达交错：append 按 id 去重防重复渲染（review M-1）', async () => {
    // 面板打开后新通知 id=4 经 feed 注入（服务端列表头部插入）→ 此时 offset 基于
    // 旧快照（paged.length=2），第二页返回会与已加载尾部重叠（报告2 已在第一页）
    invokeMock.mockImplementation(async (_ch: string, args?: { offset?: number }) =>
      (args?.offset ?? 0) === 0
        ? { items: [mk(3), mk(2)], total: 4 }
        : { items: [mk(2), mk(1)], total: 4 })
    activityMocks.feed.current = [mk(4)]
    const { container, getByText } = render(<NotificationCenter open onClose={() => {}} />)
    await flush()
    fireEvent.click(getByText('notificationCenter.loadMore'))
    await flush()
    // 无去重实现下报告2 渲染两行（旧 append 直拼）；每条恰好一次
    const text = container.textContent!
    for (const id of [4, 3, 2, 1]) {
      expect((text.match(new RegExp(`报告${id}`, 'g')) ?? []).length).toBe(1)
    }
  })

  it('新到达后 hasMore 按渲染条数收敛：点到底按钮消失，不空转（review 问题 1）', async () => {
    // 打开时刻快照 [3,2] total=3；随后 id=4 经 feed 到达（服务端 [4,3,2,1] total=4）。
    // 旧口径 paged.length(3) < total(4) → 按钮常驻，再点返回的 [1] 全被去重 → 空转；
    // 修复后 items.length(4) = total(4) → 按钮消失
    invokeMock.mockImplementation(async (_ch: string, args?: { offset?: number }) =>
      (args?.offset ?? 0) === 0
        ? { items: [mk(3), mk(2)], total: 3 }
        : { items: [mk(2), mk(1)], total: 4 })
    activityMocks.feed.current = [mk(4)]
    const { getByText, queryByText } = render(<NotificationCenter open onClose={() => {}} />)
    await flush()
    fireEvent.click(getByText('notificationCenter.loadMore'))
    await flush()
    expect(queryByText('notificationCenter.loadMore')).toBeNull()
  })

  it('面板打开期间新通知经 feed 实时注入（与分页按 id 去重，上浮在前）', async () => {
    invokeMock.mockResolvedValue({ items: [mk(2)], total: 1 })
    activityMocks.feed.current = [mk(5), mk(2)]  // id=5 打开后新到达；id=2 与分页重复
    const { container } = render(<NotificationCenter open onClose={() => {}} />)
    await flush()
    const text = container.textContent!
    expect(text).toContain('报告5')
    expect(text.indexOf('报告5')).toBeLessThan(text.indexOf('报告2'))  // 实时面在上
  })

  it('全部已读：markAllRead 一次调用（feed/徽章乐观全清由 Context 负责）', async () => {
    invokeMock.mockResolvedValue({ items: [mk(2), mk(1)], total: 2 })
    activityMocks.feed.current = [mk(2), mk(1)]
    const { getByText } = render(<NotificationCenter open onClose={() => {}} />)
    await flush()
    const btn = getByText('notificationCenter.markAllRead').closest('button')!
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    await flush()
    expect(activityMocks.markAllRead).toHaveBeenCalledTimes(1)
  })

  it('点击条目：markRead([id]) + onClose + navigateToNotification 分流', async () => {
    invokeMock.mockResolvedValue({
      items: [mk(2, { entity_type: 'affair', entity_id: 'pt9', kind: 'due' })],
      total: 1,
    })
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const onClose = vi.fn()
    const { getByText } = render(<NotificationCenter open onClose={onClose} />)
    await flush()
    fireEvent.click(getByText(/activity\.affair\.due/))
    await flush()
    expect(activityMocks.markRead).toHaveBeenCalledWith([2])
    expect(onClose).toHaveBeenCalledTimes(1)
    const ev = dispatchSpy.mock.calls
      .map(([e]) => e as CustomEvent)
      .find((e) => e.type === 'app:navigate-project-tab')
    expect((ev as CustomEvent | undefined)?.detail).toEqual({
      projectId: 'p1', tab: 'affairs', container: 'project', taskId: 'pt9',
    })
  })

  it('空态渲染占位文案', async () => {
    invokeMock.mockResolvedValue({ items: [], total: 0 })
    const { container } = render(<NotificationCenter open onClose={() => {}} />)
    await flush()
    expect(container.textContent).toContain('notificationCenter.empty')
  })

  it('open=false 不渲染面板', () => {
    const { container } = render(<NotificationCenter open={false} onClose={() => {}} />)
    expect(container.textContent).toBe('')
  })
})
