/** tests/IntelFeed.test.tsx — 情报流卡片流测试（V2-E1a + 页面遍历 review M1-1）。
 *
 * 覆盖：
 * - 空态三分支（M1-1 2026-10-24 回归）：useSchedules 拉取失败此前仅 console.error、
 *   totals 恒 ZERO_TOTALS，失败态与「真实零订阅」不可区分 → 渲染「零订阅」建订阅
 *   引导误导创建重复订阅，且首拉以 totals.n=0 为门被跳过、inbox 永空。现约定：
 *   failed 渲染错误+重试（且照拉 inbox）/ loading 中性占位 / 仅 loaded 零订阅才引导。
 * - inbox 自身首拉失败（静默 catch 排查 2026-10-24）：空列表升级 fetch-error
 *   错误+重试，不得伪装「暂无产出」；重试恢复后错误态消失。
 * - 产业 done 卡快捷提取按钮（0.6 §6.3-12）：分类只驱动突出度，点击带
 *   action='extract' 且 stopPropagation 不冒泡为普通阅读动线；
 * - 卡片键盘可达性（CodeReview Minor 2026-10-24）：卡片自身 Enter/Space 打开，
 *   焦点在嵌套快捷钮时 Enter 走按钮原生 click、不再冒泡误开；
 * - 未读点 / rollup 徽标 / 加载更多分页 offset。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { IntelFeed } from '../src/features/intel/IntelFeed'
import type { SchedulesStatus } from '../src/features/schedule/useSchedules'

const schedulesMock = vi.hoisted(() => ({
  totals: { n: 0, total: 0, abnormal: 0 },
  status: 'loading' as SchedulesStatus,
  refresh: vi.fn(),
}))

const invokeMock = vi.hoisted(() => vi.fn())

vi.mock('../src/features/schedule/useSchedules', () => ({
  useSchedules: () => schedulesMock,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ isActive: false, items: [] }),
}))

vi.mock('../src/hooks/useEdgeRefetch', () => ({
  useEdgeRefetch: () => {},
}))

const runRow = (over: Record<string, unknown> = {}) => ({
  id: 'r1', schedule_id: 's1', task_id: null, status: 'done',
  report_path: null, summary: ['摘要一'], error: null,
  read_at: '2026-10-24T08:00:00Z',
  started_at: '2026-10-24T07:00:00Z', finished_at: '2026-10-24T08:00:00Z',
  schedule_name: '华润舆情', schedule_category: 'corporate', schedule_scope: null,
  ...over,
})

beforeEach(() => {
  schedulesMock.totals = { n: 0, total: 0, abnormal: 0 }
  schedulesMock.status = 'loading'
  schedulesMock.refresh.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async () => ({ runs: [], has_more: false }))
  ;(window as unknown as { electronAPI?: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const renderFeed = (onOpenRead = vi.fn()) => {
  render(<IntelFeed onOpenRead={onOpenRead} onOpenAdmin={() => {}} />)
  return onOpenRead
}

/** loaded + 有订阅：注入 runs 并等待卡片渲染（正常流前置；多卡时用 getAllBy） */
async function renderWithRuns(runs: Record<string, unknown>[], hasMore = false) {
  schedulesMock.status = 'loaded'
  schedulesMock.totals = { n: 2, total: 3, abnormal: 0 }
  invokeMock.mockImplementation(async () => ({ runs, has_more: hasMore }))
  const onOpenRead = renderFeed()
  await waitFor(() => expect(screen.getAllByTestId('intel-card').length).toBe(runs.length))
  return onOpenRead
}

describe('IntelFeed 空态三分支（M1-1）', () => {
  it('failed：渲染错误+重试而非「零订阅」引导，且照拉 inbox', async () => {
    schedulesMock.status = 'failed'
    schedulesMock.totals = { n: 0, total: 0, abnormal: 0 }
    renderFeed()
    // 错误态 + 重试按钮（复用 knowledge.retry 文案）
    expect(screen.getByTestId('intel-feed-error')).toBeTruthy()
    expect(screen.getByText('intel.summaryLoadFailed')).toBeTruthy()
    // 「零订阅」引导空态不得出现（误导创建重复订阅）
    expect(screen.queryByTestId('intel-feed-empty')).toBeNull()
    expect(screen.queryByText('intel.emptyNoSubs')).toBeNull()
    // 首拉门放行：summary 失败时 inbox 照拉（真实有订阅场景不永挂空态）
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('schedules:inbox', expect.anything()))
    // 点击重试 → refresh 被调
    fireEvent.click(screen.getByText('knowledge.retry'))
    expect(schedulesMock.refresh).toHaveBeenCalledTimes(1)
  })

  it('loading：中性占位，不闪「零订阅」引导', () => {
    schedulesMock.status = 'loading'
    renderFeed()
    expect(screen.getByTestId('intel-feed-loading')).toBeTruthy()
    expect(screen.queryByTestId('intel-feed-empty')).toBeNull()
    // loading 态不白拉 inbox（summary 未回，原优化保留）
    expect(invokeMock).not.toHaveBeenCalledWith('schedules:inbox', expect.anything())
  })

  it('loaded + 零订阅：才渲染建订阅引导空态', () => {
    schedulesMock.status = 'loaded'
    renderFeed()
    expect(screen.getByTestId('intel-feed-empty')).toBeTruthy()
    expect(screen.getByText('intel.emptyNoSubs')).toBeTruthy()
    expect(screen.queryByTestId('intel-feed-error')).toBeNull()
  })

  it('loaded + 有订阅：正常渲染情报流', async () => {
    schedulesMock.status = 'loaded'
    schedulesMock.totals = { n: 2, total: 3, abnormal: 0 }
    renderFeed()
    expect(screen.getByTestId('intel-feed')).toBeTruthy()
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('schedules:inbox', expect.anything()))
  })

  it('inbox 首拉失败且列表空 → fetch-error 错误+重试，不伪装「暂无产出」（静默 catch 排查）', async () => {
    schedulesMock.status = 'loaded'
    schedulesMock.totals = { n: 2, total: 3, abnormal: 0 }
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:inbox') throw new Error('boom')
      return {}
    })
    renderFeed()
    await waitFor(() => expect(screen.getByTestId('intel-feed-fetch-error')).toBeTruthy())
    expect(screen.getByText('intel.feedLoadFailed')).toBeTruthy()
    // 不得伪装「暂无产出」空态（有订阅但 inbox 失败 ≠ 无产出）
    expect(screen.queryByText('intel.emptyNoOutputs')).toBeNull()
    // 重试恢复：错误态消失、卡片渲染
    invokeMock.mockImplementation(async () => ({ runs: [runRow()], has_more: false }))
    fireEvent.click(screen.getByText('knowledge.retry'))
    await waitFor(() => expect(screen.getByTestId('intel-card')).toBeTruthy())
    expect(screen.queryByTestId('intel-feed-fetch-error')).toBeNull()
  })
})

describe('IntelFeed 卡片功能点', () => {
  it('产业 done 卡渲染快捷提取按钮；非产业/非 done 不渲染', async () => {
    await renderWithRuns([
      runRow({ id: 'r1', schedule_category: 'industry' }),
      runRow({ id: 'r2', schedule_category: 'corporate' }),
      runRow({ id: 'r3', schedule_category: 'industry', status: 'running' }),
    ])
    const btns = screen.getAllByTestId('intel-card-extract')
    // 仅产业 done 卡（r1）有快捷钮
    expect(btns).toHaveLength(1)
  })

  it('卡片点击 → onOpenRead(run) 普通阅读动线（无 action）', async () => {
    const onOpenRead = await renderWithRuns([runRow()])
    fireEvent.click(screen.getByTestId('intel-card'))
    expect(onOpenRead).toHaveBeenCalledTimes(1)
    expect(onOpenRead.mock.calls[0][0].id).toBe('r1')
    expect(onOpenRead.mock.calls[0].length).toBe(1)
  })

  it('快捷钮点击 → onOpenRead(run, "extract") 且 stopPropagation 不冒泡', async () => {
    const onOpenRead = await renderWithRuns([runRow({ schedule_category: 'industry' })])
    fireEvent.click(screen.getByTestId('intel-card-extract'))
    // 恰 1 次（冒泡被拦截，不叠加卡片动线）且带 extract 动作
    expect(onOpenRead).toHaveBeenCalledTimes(1)
    expect(onOpenRead.mock.calls[0][1]).toBe('extract')
  })

  it('卡片自身 Enter/Space 键盘打开；焦点在嵌套快捷钮时不冒泡误开', async () => {
    const onOpenRead = await renderWithRuns([runRow({ schedule_category: 'industry' })])
    const card = screen.getByTestId('intel-card')
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(onOpenRead).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(card, { key: ' ' })
    expect(onOpenRead).toHaveBeenCalledTimes(2)
    // 焦点在嵌套快捷钮上：target ≠ currentTarget → 卡片处理器早退
    fireEvent.keyDown(screen.getByTestId('intel-card-extract'), { key: 'Enter' })
    expect(onOpenRead).toHaveBeenCalledTimes(2)
  })

  it('未读点（未读行）与 rollup 徽标渲染', async () => {
    await renderWithRuns([
      runRow({ id: 'r1', read_at: null, schedule_scope: 'rollup' }),
    ])
    expect(screen.getByTestId('unread-dot')).toBeTruthy()
    expect(screen.getByText('schedule.typeRollup')).toBeTruthy()
  })

  it('未读行（read_at=null）不渲染未读点当已读', async () => {
    await renderWithRuns([runRow({ read_at: '2026-10-24T08:00:00Z' })])
    expect(screen.queryByTestId('unread-dot')).toBeNull()
  })

  it('hasMore：渲染加载更多，点击拉下一页 offset=rows.length', async () => {
    await renderWithRuns([runRow()], true)
    const more = screen.getByText('task.loadMore')
    fireEvent.click(more)
    await waitFor(() => {
      const last = invokeMock.mock.calls.filter((c) => c[0] === 'schedules:inbox').at(-1)
      expect(last?.[1]).toMatchObject({ offset: 1 })
    })
  })
})
