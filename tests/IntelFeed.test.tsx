/**
 * IntelFeed 单元测试（0.6 §3.2 V2-E1a）：inbox 卡片流行为。
 * 覆盖：首屏调用契约（sort/category/limit/offset）、类别 chips 过滤、
 * 排序切换、加载更多 offset 递增、零订阅空态引导（onOpenAdmin）、
 * 卡片渲染（类别 chip + 未读点）。
 * 隔离：mock electronAPI.invoke / useSchedules / useActivity / useEdgeRefetch；
 * i18n mock 透传 key（断言 key 而非文案）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { IntelFeed } from '../src/features/intel/IntelFeed'
import { useActivity } from '../src/features/activity/ActivityContext'
import type { ScheduleRunRow, ScheduleTotals } from '../src/features/schedule/useSchedules'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

vi.mock('../src/features/activity/ActivityContext', () => ({
  // 记录 kind：边沿源断言依赖（无参 mock 曾放过误订 task 源，2026-10-24 回归补）
  useActivity: vi.fn(() => ({ isActive: false })),
}))

vi.mock('../src/hooks/useEdgeRefetch', () => ({
  useEdgeRefetch: () => {},
}))

const invokeMock = vi.fn()
const mockTotals: ScheduleTotals = { n: 2, total: 1, abnormal: 0 }

vi.mock('../src/features/schedule/useSchedules', () => ({
  useSchedules: () => ({ totals: mockTotals, refresh: vi.fn() }),
}))

function run(overrides: Partial<ScheduleRunRow> = {}): ScheduleRunRow {
  return {
    id: 'run-1',
    schedule_id: 's-1',
    task_id: null,
    status: 'done',
    report_path: null,
    summary: ['第一条情报摘要'],
    read_at: null,
    started_at: '2026-10-23T08:00:00Z',
    finished_at: '2026-10-23T08:03:00Z',
    schedule_name: '产业周报',
    schedule_category: 'industry',
    ...overrides,
  }
}

function mockInbox(runs: ScheduleRunRow[], hasMore = false) {
  invokeMock.mockResolvedValue({ runs, has_more: hasMore, total: runs.length })
}

beforeEach(() => {
  invokeMock.mockReset()
  ;(window as unknown as { electronAPI: { invoke: typeof invokeMock } }).electronAPI = {
    invoke: invokeMock,
  }
  mockTotals.n = 2
  mockTotals.total = 1
})

describe('IntelFeed', () => {
  it('首屏按未读优先拉第一页并渲染卡片（未读点 + 类别 chip）', async () => {
    mockInbox([
      run(),
      run({ id: 'run-2', read_at: '2026-10-23T09:00:00Z', summary: null, schedule_name: '已读期次', schedule_category: 'corporate' }),
    ])
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getAllByTestId('intel-card')).toHaveLength(2)
    })
    expect(invokeMock).toHaveBeenCalledWith('schedules:inbox', {
      sort: 'unread',
      category: undefined,
      limit: 20,
      offset: 0,
    })
    // 未读行有点，已读行无点
    expect(screen.getAllByTestId('unread-dot')).toHaveLength(1)
    // 卡片内类别 chip 文案 = key 透传（schedule_category → intel.cat.*；
    // 筛选区固定 chips 同名共存，须限定卡片范围断言）
    const cards = screen.getAllByTestId('intel-card')
    expect(cards[0]?.textContent).toContain('intel.cat.industry')
    expect(cards[1]?.textContent).toContain('intel.cat.corporate')
  })

  it('点击类别 chip 以 category 过滤重拉第一页', async () => {
    mockInbox([])
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)
    await waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByText('intel.cat.corporate'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(2))
    expect(invokeMock).toHaveBeenLastCalledWith('schedules:inbox', {
      sort: 'unread',
      category: 'corporate',
      limit: 20,
      offset: 0,
    })
  })

  it('切换「最新」排序以 sort=latest 重拉', async () => {
    mockInbox([])
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)
    await waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByText('intel.sort.latest'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(2))
    expect(invokeMock).toHaveBeenLastCalledWith('schedules:inbox', {
      sort: 'latest',
      category: undefined,
      limit: 20,
      offset: 0,
    })
  })

  it('has_more 时「加载更多」以累计 offset 追加下一页', async () => {
    // 首屏满页 20 条（真实分页语义：offset 递增 = 已加载行数）
    const page1 = Array.from({ length: 20 }, (_, i) => run({ id: `run-${i}` }))
    invokeMock
      .mockResolvedValueOnce({ runs: page1, has_more: true, total: 25 })
      .mockResolvedValueOnce({ runs: [run({ id: 'run-20' })], has_more: false, total: 25 })
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)

    const loadMore = await screen.findByText('task.loadMore')
    fireEvent.click(loadMore)

    await waitFor(() => {
      expect(screen.getAllByTestId('intel-card')).toHaveLength(21)
    })
    expect(invokeMock).toHaveBeenLastCalledWith('schedules:inbox', {
      sort: 'unread',
      category: undefined,
      limit: 20,
      offset: 20,
    })
  })

  it("边沿源订阅 'schedule' 活动源（订阅期次终态注册在 schedule 源；误订 task 源则新产出永不触发重拉）", async () => {
    mockInbox([])
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)
    await waitFor(() => expect(invokeMock).toHaveBeenCalledTimes(1))
    expect(vi.mocked(useActivity)).toHaveBeenCalledWith('schedule')
  })

  it('零订阅空态引导建订阅（onOpenAdmin）', async () => {
    mockTotals.n = 0
    const onOpenAdmin = vi.fn()
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={onOpenAdmin} />)

    expect(screen.getByTestId('intel-feed-empty')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.newSubscription'))
    expect(onOpenAdmin).toHaveBeenCalledTimes(1)
    // 空态路径不请求 inbox
    expect(invokeMock).not.toHaveBeenCalled()
  })

  it('rollup 期次卡「组合周报」徽标随行（2.11 §12.4）；普通期次无', async () => {
    mockInbox([
      run({ id: 'run-r', schedule_scope: 'rollup', schedule_name: '组合周报', schedule_category: 'general' }),
      run({ id: 'run-f', schedule_scope: 'focus' }),
    ])
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)
    const cards = await screen.findAllByTestId('intel-card')
    expect(within(cards[0]).getByText('schedule.scope.rollup')).toBeTruthy()
    expect(within(cards[1]).queryByText('schedule.scope.rollup')).toBeNull()
  })

  // ── 产业卡「提取线索」快捷钮（0.6 §3.2/§6.3-12 V2-E2）──

  it('产业类 done 卡「提取线索」快捷钮：直达阅读页并带 extract 动线（不触发卡片点击）', async () => {
    mockInbox([run({ schedule_category: 'industry' })])
    const onOpenRead = vi.fn()
    render(<IntelFeed onOpenRead={onOpenRead} onOpenAdmin={vi.fn()} />)
    const cards = await screen.findAllByTestId('intel-card')
    const btn = within(cards[0]).getByTestId('intel-card-extract')
    fireEvent.click(btn)
    expect(onOpenRead).toHaveBeenCalledTimes(1)
    expect(onOpenRead).toHaveBeenCalledWith(expect.objectContaining({ id: 'run-1' }), 'extract')
  })

  it('非产业卡无提取快捷钮（分类只驱动突出度，不裁剪阅读页操作集）', async () => {
    mockInbox([run({ schedule_category: 'corporate' })])
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)
    const cards = await screen.findAllByTestId('intel-card')
    expect(within(cards[0]).queryByTestId('intel-card-extract')).toBeNull()
  })

  it('running 产业卡无提取快捷钮（无报告不可提取）', async () => {
    mockInbox([run({ schedule_category: 'industry', status: 'running', finished_at: null })])
    render(<IntelFeed onOpenRead={vi.fn()} onOpenAdmin={vi.fn()} />)
    const cards = await screen.findAllByTestId('intel-card')
    expect(within(cards[0]).queryByTestId('intel-card-extract')).toBeNull()
  })
})
