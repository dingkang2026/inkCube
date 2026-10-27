/**
 * tests/IntelCardZone.test.tsx — 首页情报卡区单测（0.6 §3.2 V2-E1a T10，
 * 前身 tests/IntelDigest.test.tsx 2.11 E3 T9；V2-E1b 2026-10-24 1 大 N 小改版）。
 *
 * 覆盖：零订阅空态引导；1 大 + N 小编辑排版（大卡 = 最新一条，带摘要两行预览 /
 * 类别 chip / 订阅名 meta；紧凑行 = 其余：要点首行/订阅名 + 相对时间 + 未读点）；
 * 行点击 → onOpen(runId)；running 点击 → dispatch app:navigate-schedules；
 * 「查看全部」→ dispatch app:navigate-intel；空态「暂无产出」；状态点三态与
 * 暂停行灰显（§8.4 状态-界面映射）；app:schedules-changed 已读边沿 → digest
 * 重拉（ReadView markRead 广播，首页卡区未读点即时消失，2026-10-26）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { IntelCardZone } from '../src/features/schedule/IntelCardZone'
import { useActivity } from '../src/features/activity/ActivityContext'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  // 记录 kind：边沿源断言依赖（无参 mock 曾放过误订 task 源，2026-10-24 回归补）
  useActivity: vi.fn(() => ({ isActive: false })),
}))
// mock useSchedules：真实 hook 内部同样调 useActivity('schedule')，不 mock 则
// 「边沿源订阅 schedule」断言恒真、防不住被测组件误订 task 源（2026-10-24 补）
const mockTotals = { n: 3, total: 1, abnormal: 0 }
vi.mock('../src/features/schedule/useSchedules', () => ({
  useSchedules: () => ({ totals: mockTotals, refresh: vi.fn() }),
}))

const hAgo = (hours: number) => new Date(Date.now() - hours * 3600_000).toISOString()

const ROW_UNREAD_DONE = {
  id: 'run_u', schedule_id: 's1', task_id: 't1', status: 'done',
  report_path: 'x.md', summary: ['要点一：负面舆情', '要点二：新竞争者入场'], read_at: null,
  started_at: hAgo(1), finished_at: hAgo(1),
  schedule_name: '华润舆情', schedule_enabled: 1, error: null,
  schedule_category: 'corporate',
}
const ROW_RUNNING = {
  id: 'run_r', schedule_id: 's2', task_id: 't2', status: 'running',
  report_path: null, summary: null, read_at: null,
  started_at: hAgo(2), finished_at: null,
  schedule_name: '竞品监控', schedule_enabled: 1, error: null,
  schedule_category: 'industry',
}
const ROW_PAUSED = {
  id: 'run_p', schedule_id: 's3', task_id: 't3', status: 'done',
  report_path: 'y.md', summary: ['已读要点'], read_at: '2026-10-19T09:00:00',
  started_at: hAgo(20), finished_at: hAgo(20),
  schedule_name: '产业情报', schedule_enabled: 0, error: null,
  schedule_category: 'region',
}
const DIGEST = [ROW_UNREAD_DONE, ROW_RUNNING, ROW_PAUSED]

const wireInvoke = (over: { digest?: unknown[] } = {}) =>
  vi.fn((channel: string) => {
    if (channel === 'schedules:digest') {
      return Promise.resolve({ digest: over.digest ?? DIGEST })
    }
    return Promise.resolve({ ok: true })
  })

/** 排空 microtask（digest 为 async 链） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

/** 全局事件收集器（组件内 dispatch 的导航事件断言） */
const listenEvent = (name: string) => {
  const events: Event[] = []
  const handler = (e: Event) => events.push(e)
  window.addEventListener(name, handler)
  return { events, cleanup: () => window.removeEventListener(name, handler) }
}

const mount = (over?: { totals?: { n: number; total: number; abnormal: number }; digest?: unknown[] }) => {
  const invoke = wireInvoke(over)
  if (over?.totals) Object.assign(mockTotals, over.totals)
  ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
  return invoke
}

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
  Object.assign(mockTotals, { n: 3, total: 1, abnormal: 0 })
})

describe('IntelCardZone（0.6 §3.2 首页情报卡区）', () => {
  it('零订阅（n=0）→ 空态引导，「新建订阅」dispatch app:navigate-schedules', async () => {
    mount({ totals: { n: 0, total: 0, abnormal: 0 } })
    const nav = listenEvent('app:navigate-schedules')
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    // 不再无声消失：空态引导渲染（0.6 §3.2 情报第一公民）
    expect(screen.getByTestId('intel-zone-empty')).toBeTruthy()
    expect(screen.getByText('intel.emptyNoSubs')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.newSubscription'))
    expect(nav.events).toHaveLength(1)
    nav.cleanup()
  })

  it('渲染行：要点首行/订阅名 + 相对时间 + 未读红点', async () => {
    mount()
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    expect(screen.getByText('要点一：负面舆情')).toBeTruthy()       // 大卡标题 = summary[0]
    expect(screen.getByText('竞品监控')).toBeTruthy()               // running 紧凑行无摘要 → 订阅名
    expect(screen.getAllByText('activity.time.hoursAgo').length).toBeGreaterThanOrEqual(1) // 相对时间键复用（大卡+两行均落在小时档）
    const card = screen.getByText('要点一：负面舆情').closest(
      '[data-testid="digest-big"],[data-testid="digest-row"]',
    ) as HTMLElement
    expect(card.querySelector('[data-testid="unread-dot"]')).toBeTruthy()
  })

  it('1 大 N 小：大卡 = 最新一条，带摘要两行预览 + 类别 chip + 订阅名 meta', async () => {
    mount()
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    const big = screen.getByTestId('digest-big') as HTMLElement
    // 最新（1h 前）= run_u；预览 = summary[1..] 拼接；chip 直读订阅分类；meta 补订阅名（标题取自摘要时）
    expect(big.textContent).toContain('要点二：新竞争者入场')
    expect(big.textContent).toContain('intel.cat.corporate')
    expect(big.textContent).toContain('华润舆情')
    // 紧凑行 = 其余两条（running + paused），保持原行形态
    expect(screen.getAllByTestId('digest-row')).toHaveLength(2)
  })

  it('rollup 期次「聚合简报」徽标随行：大卡 meta 与紧凑行（2.11 §12.4）', async () => {
    // 最新行（大卡）= rollup；紧凑行注入一条 rollup done 行验证随行
    mount({ digest: [
      { ...ROW_UNREAD_DONE, schedule_scope: 'rollup' },
      { ...ROW_PAUSED, schedule_scope: 'rollup' },
      ROW_RUNNING,
    ] })
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    const big = screen.getByTestId('digest-big') as HTMLElement
    expect(big.textContent).toContain('schedule.typeRollup')
    const pausedRow = screen.getByText('已读要点').closest('[data-testid="digest-row"]') as HTMLElement
    expect(pausedRow.textContent).toContain('schedule.typeRollup')
    // 普通（情报）订阅行无徽标
    const runningRow = screen.getByText('竞品监控').closest('[data-testid="digest-row"]') as HTMLElement
    expect(runningRow.textContent).not.toContain('schedule.typeRollup')
  })

  it('done 行点击 → onOpen(runId)', async () => {
    mount()
    const onOpen = vi.fn()
    render(<IntelCardZone onOpen={onOpen} />)
    await flush()
    fireEvent.click(screen.getByText('要点一：负面舆情'))
    expect(onOpen).toHaveBeenCalledWith('run_u')
  })

  it('running 行点击 → dispatch app:navigate-schedules（去管理台看进度）', async () => {
    mount()
    const nav = listenEvent('app:navigate-schedules')
    const onOpen = vi.fn()
    render(<IntelCardZone onOpen={onOpen} />)
    await flush()
    fireEvent.click(screen.getByText('竞品监控'))
    expect(onOpen).not.toHaveBeenCalled()
    expect(nav.events).toHaveLength(1)
    nav.cleanup()
  })

  it('app:schedules-changed 已读边沿 → digest 重拉（读报告后首页卡区未读点即时消失）', async () => {
    const invoke = mount()
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    const digestCalls = () => invoke.mock.calls.filter((c) => c[0] === 'schedules:digest').length
    const before = digestCalls()
    await act(async () => {
      window.dispatchEvent(new CustomEvent('app:schedules-changed'))
    })
    await flush()
    expect(digestCalls()).toBe(before + 1)
  })

  it("边沿源订阅 'schedule' 活动源（订阅期次终态注册在 schedule 源；误订 task 源则新产出永不触发重拉）", async () => {
    mount()
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    expect(vi.mocked(useActivity)).toHaveBeenCalledWith('schedule')
  })

  it('「查看全部」→ dispatch app:navigate-intel（情报中心 feed）', async () => {
    mount()
    const nav = listenEvent('app:navigate-intel')
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    fireEvent.click(screen.getByText('schedule.digest.viewAll'))
    expect(nav.events).toHaveLength(1)
    nav.cleanup()
  })

  it('有订阅无产出 → 空态「暂无产出」', async () => {
    mount({ totals: { n: 1, total: 0, abnormal: 0 }, digest: [] })
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    expect(screen.getByText('schedule.digest.empty')).toBeTruthy()
  })

  it('状态点三态与暂停行灰显（§8.4 状态-界面映射）', async () => {
    mount()
    render(<IntelCardZone onOpen={() => {}} />)
    await flush()
    // running → 呼吸点（紧凑行；大卡同构共享 onClick 语义）
    const runningRow = screen.getByText('竞品监控').closest('[data-testid="digest-row"]') as HTMLElement
    expect(runningRow.querySelector('[data-testid="dot-running"]')).toBeTruthy()
    // paused 订阅行 → 灰显
    const pausedRow = screen.getByText('已读要点').closest('[data-testid="digest-row"]') as HTMLElement
    expect(pausedRow.className).toContain('rowPaused')
    expect(pausedRow.querySelector('[data-testid="unread-dot"]')).toBeNull() // 已读无点
  })
})
