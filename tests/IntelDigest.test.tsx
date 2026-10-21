/**
 * tests/IntelDigest.test.tsx — 2.11 E3 T9: IntelDigest 首页情报速览单测（§8.1 动线②）。
 *
 * 覆盖：无订阅不渲染（0.5 §4.2：E1/E2 此区块不渲染）；行渲染（要点首行/订阅名
 * + 相对时间 + 未读点）；行点击 → onOpen(runId)；running 行点击 → onOpenAdmin()
 * （无报告可读，去管理台看进度）；「查看全部」→ onOpenAdmin()；空态「暂无产出」
 * （有订阅无产出）；状态点三态与暂停行灰显（§8.4 状态-界面映射）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { IntelDigest } from '../src/features/schedule/IntelDigest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ isActive: false }),
}))

const hAgo = (hours: number) => new Date(Date.now() - hours * 3600_000).toISOString()

const ROW_UNREAD_DONE = {
  id: 'run_u', schedule_id: 's1', task_id: 't1', status: 'done',
  report_path: 'x.md', summary: ['要点一：负面舆情'], read_at: null,
  started_at: hAgo(5), finished_at: hAgo(5),
  schedule_name: '华润舆情', schedule_enabled: 1, error: null,
}
const ROW_RUNNING = {
  id: 'run_r', schedule_id: 's2', task_id: 't2', status: 'running',
  report_path: null, summary: null, read_at: null,
  started_at: hAgo(2), finished_at: null,
  schedule_name: '竞品监控', schedule_enabled: 1, error: null,
}
const ROW_PAUSED = {
  id: 'run_p', schedule_id: 's3', task_id: 't3', status: 'done',
  report_path: 'y.md', summary: ['已读要点'], read_at: '2026-10-19T09:00:00',
  started_at: hAgo(20), finished_at: hAgo(20),
  schedule_name: '产业情报', schedule_enabled: 0, error: null,
}
const DIGEST = [ROW_UNREAD_DONE, ROW_RUNNING, ROW_PAUSED]

const wireInvoke = (over: {
  totals?: { n: number; total: number; abnormal: number }
  digest?: unknown[]
} = {}) =>
  vi.fn((channel: string) => {
    if (channel === 'schedules:summary') {
      return Promise.resolve({ totals: over.totals ?? { n: 3, total: 1, abnormal: 0 } })
    }
    if (channel === 'schedules:digest') {
      return Promise.resolve({ digest: over.digest ?? DIGEST })
    }
    return Promise.resolve({ ok: true })
  })

/** 排空 microtask（挂载 summary/digest 均为 async 链） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const mount = (over?: Parameters<typeof wireInvoke>[0]) => {
  const invoke = wireInvoke(over)
  ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
  return invoke
}

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

describe('IntelDigest（2.11 §8.1 动线②）', () => {
  it('无订阅（n=0）→ 区块不渲染（0.5 §4.2 E1/E2 不渲染）', async () => {
    mount({ totals: { n: 0, total: 0, abnormal: 0 } })
    const { container } = render(<IntelDigest onOpen={() => {}} onOpenAdmin={() => {}} />)
    await flush()
    expect(container.querySelector('[data-testid="intel-digest"]')).toBeNull()
  })

  it('渲染行：要点首行/订阅名 + 相对时间 + 未读红点', async () => {
    mount()
    render(<IntelDigest onOpen={() => {}} onOpenAdmin={() => {}} />)
    await flush()
    expect(screen.getByText('要点一：负面舆情')).toBeTruthy()       // done 行标题 = summary[0]
    expect(screen.getByText('竞品监控')).toBeTruthy()               // running 行无摘要 → 订阅名
    expect(screen.getAllByText('activity.time.hoursAgo').length).toBeGreaterThanOrEqual(1) // 相对时间键复用（三行均落在小时档）
    const row = screen.getByText('要点一：负面舆情').closest('[data-testid="digest-row"]') as HTMLElement
    expect(row.querySelector('[data-testid="unread-dot"]')).toBeTruthy()
  })

  it('行点击 → onOpen(runId)', async () => {
    mount()
    const onOpen = vi.fn()
    render(<IntelDigest onOpen={onOpen} onOpenAdmin={() => {}} />)
    await flush()
    fireEvent.click(screen.getByText('要点一：负面舆情'))
    expect(onOpen).toHaveBeenCalledWith('run_u')
  })

  it('running 行点击 → onOpenAdmin()（无报告可读，去管理台看进度）', async () => {
    mount()
    const onOpen = vi.fn()
    const onOpenAdmin = vi.fn()
    render(<IntelDigest onOpen={onOpen} onOpenAdmin={onOpenAdmin} />)
    await flush()
    fireEvent.click(screen.getByText('竞品监控'))
    expect(onOpen).not.toHaveBeenCalled()
    expect(onOpenAdmin).toHaveBeenCalledTimes(1)
  })

  it('「查看全部」→ onOpenAdmin()', async () => {
    mount()
    const onOpenAdmin = vi.fn()
    render(<IntelDigest onOpen={() => {}} onOpenAdmin={onOpenAdmin} />)
    await flush()
    fireEvent.click(screen.getByText('schedule.digest.viewAll'))
    expect(onOpenAdmin).toHaveBeenCalledTimes(1)
  })

  it('有订阅无产出 → 空态「暂无产出」', async () => {
    mount({ totals: { n: 1, total: 0, abnormal: 0 }, digest: [] })
    render(<IntelDigest onOpen={() => {}} onOpenAdmin={() => {}} />)
    await flush()
    expect(screen.getByText('schedule.digest.empty')).toBeTruthy()
  })

  it('状态点三态与暂停行灰显（§8.4 状态-界面映射）', async () => {
    mount()
    render(<IntelDigest onOpen={() => {}} onOpenAdmin={() => {}} />)
    await flush()
    // running → 呼吸点
    const runningRow = screen.getByText('竞品监控').closest('[data-testid="digest-row"]') as HTMLElement
    expect(runningRow.querySelector('[data-testid="dot-running"]')).toBeTruthy()
    // paused 订阅行 → 灰显
    const pausedRow = screen.getByText('已读要点').closest('[data-testid="digest-row"]') as HTMLElement
    expect(pausedRow.className).toContain('rowPaused')
    expect(pausedRow.querySelector('[data-testid="unread-dot"]')).toBeNull() // 已读无点
  })
})
