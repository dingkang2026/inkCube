/** tests/SubscriptionAdminView.test.tsx — 定时订阅管理台（2.11 E3 T7）
 *  计划 Step 1 要点：分类页签服务端过滤计数；搜索防抖触发 schedules:list 带 q；
 *  勾选 2 行批量条出现且删除确认文案含数量；右列默认渲染收件箱（未读在前）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SubscriptionAdminView } from '../src/features/schedule/SubscriptionAdminView'
import { ConfirmProvider } from '../src/components/ConfirmDialog'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, p?: Record<string, unknown>) =>
      p && 'n' in p ? `${k}(${p['n']})` : k,
  }),
}))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ isActive: false }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({ useToast: () => vi.fn() }))

const invokeMock = vi.fn()

const mkSchedule = (i: number, category: string) => ({
  id: `sch_${i}`,
  name: `订阅${i}`,
  prompt: `任务提示词${i}`,
  category,
  interval_type: 'daily',
  interval_value: '09:00',
  target_type: null,
  target_id: null,
  auto_import: 1,
  enabled: 1,
  next_run_at: '2026-10-20T09:00:00',
  last_run_at: null,
  consecutive_failures: 0,
  latest_done: null,
  created_at: '2026-10-19T08:00:00',
})

const INDUSTRY = Array.from({ length: 10 }, (_, i) => mkSchedule(i, 'industry'))
const CORPORATE = Array.from({ length: 20 }, (_, i) => mkSchedule(100 + i, 'corporate'))

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string, args?: Record<string, unknown>) => {
    if (channel === 'schedules:list') {
      let rows = [...INDUSTRY, ...CORPORATE]
      if (args?.category) rows = rows.filter((r) => r.category === args.category)
      if (args?.q) rows = rows.filter((r) => r.name.includes(args.q as string))
      return { schedules: rows }
    }
    if (channel === 'schedules:inbox') {
      return {
        runs: [
          { id: 'run_unread', schedule_id: 'sch_0', task_id: null, status: 'done',
            report_path: null, summary: ['要点一'], read_at: null,
            started_at: '2026-10-19T09:00:00', finished_at: '2026-10-19T09:01:00',
            schedule_name: '订阅0' },
          { id: 'run_read', schedule_id: 'sch_100', task_id: null, status: 'done',
            report_path: null, summary: ['要点二'], read_at: '2026-10-19T10:00:00',
            started_at: '2026-10-18T09:00:00', finished_at: '2026-10-18T09:01:00',
            schedule_name: '订阅100' },
        ],
      }
    }
    if (channel === 'schedules:runs') return { runs: [] }
    if (channel === 'knowledge:listProjects') return { projects: [] }
    if (channel === 'baseLib:list') return { base_libs: [] }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const lastListArgs = () => {
  const calls = invokeMock.mock.calls.filter((c) => c[0] === 'schedules:list')
  return calls[calls.length - 1]?.[1] ?? {}
}

describe('SubscriptionAdminView', () => {
  it('category tab filters via server param (corporate → 20 rows)', async () => {
    render(
      <ConfirmProvider>
        <SubscriptionAdminView onOpenRead={() => {}} onOpenReplay={() => {}} />
      </ConfirmProvider>,
    )
    // 用行内 checkbox 计数断言列表规模（收件箱 RunRow 亦含订阅名文本，不可用）
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(30))
    fireEvent.click(screen.getByRole('tab', { name: 'schedule.tab.corporate' }))
    await waitFor(() => expect(lastListArgs()).toMatchObject({ category: 'corporate' }))
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(20))
  })

  it('search input debounces and passes q to schedules:list', async () => {
    render(
      <ConfirmProvider>
        <SubscriptionAdminView onOpenRead={() => {}} onOpenReplay={() => {}} />
      </ConfirmProvider>,
    )
    await waitFor(() => expect(screen.getByPlaceholderText('schedule.searchPlaceholder')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('schedule.searchPlaceholder'), {
      target: { value: '订阅10' },
    })
    await waitFor(() => expect(lastListArgs()).toMatchObject({ q: '订阅10' }))
    await waitFor(() => expect(screen.getAllByText(/订阅10\d?$/).length).toBeGreaterThan(0))
  })

  it('checking 2 rows shows batch bar; delete confirm mentions the count', async () => {
    render(
      <ConfirmProvider>
        <SubscriptionAdminView onOpenRead={() => {}} onOpenReplay={() => {}} />
      </ConfirmProvider>,
    )
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBeGreaterThanOrEqual(2))
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[0])
    fireEvent.click(boxes[1])
    expect(screen.getByTestId('batch-bar')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.batchDelete'))
    await waitFor(() =>
      expect(screen.getByText(/schedule\.batchDeleteConfirm\(2\)/)).toBeTruthy())
  })

  it('right pane defaults to inbox with unread first', async () => {
    const { container } = render(
      <ConfirmProvider>
        <SubscriptionAdminView onOpenRead={() => {}} onOpenReplay={() => {}} />
      </ConfirmProvider>,
    )
    await waitFor(() => expect(screen.getByText('schedule.inboxTitle')).toBeTruthy())
    const first = screen.getAllByTestId('run-row')[0]
    expect(first.textContent).toContain('订阅0')
    expect(first.querySelector('[data-testid="run-unread-dot"]')).toBeTruthy()
    expect(container).toBeTruthy()
  })

  it('bound schedule shows rebuild-archive button; click invokes channel (§6 T10)', async () => {
    invokeMock.mockImplementation(async (channel: string, args?: Record<string, unknown>) => {
      if (channel === 'schedules:list') {
        return { schedules: [{ ...INDUSTRY[0], target_id: 'p1', target_type: 'project' }] }
      }
      if (channel === 'schedules:inbox') return { runs: [] }
      if (channel === 'schedules:runs') return { runs: [] }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return {}
    })
    render(
      <ConfirmProvider>
        <SubscriptionAdminView onOpenRead={() => {}} onOpenReplay={() => {}} />
      </ConfirmProvider>,
    )
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(1))
    fireEvent.click(screen.getByText('订阅0'))                    // 选中 → 详情面板
    fireEvent.click(await waitFor(() => screen.getByText('schedule.rebuildArchive')))
    await waitFor(() => expect(
      invokeMock.mock.calls.some((c) => c[0] === 'schedules:rebuildArchive'
        && c[1] === 'sch_0')).toBe(true))
  })

  it('unbound schedule has no rebuild-archive button (no dead entry)', async () => {
    // 单行 mock：默认 mock 的收件箱 run-row 也含「订阅0」文本，getByText 会撞重复
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') return { schedules: [INDUSTRY[0]] }
      if (channel === 'schedules:inbox') return { runs: [] }
      if (channel === 'schedules:runs') return { runs: [] }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return {}
    })
    render(
      <ConfirmProvider>
        <SubscriptionAdminView onOpenRead={() => {}} onOpenReplay={() => {}} />
      </ConfirmProvider>,
    )
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(1))
    fireEvent.click(screen.getByText('订阅0'))                    // target_id=null
    await waitFor(() => expect(screen.getByText('schedule.runNow')).toBeTruthy())  // 详情面板已打开
    expect(screen.queryByText('schedule.rebuildArchive')).toBeNull()
  })

  it('rebuild button disabled while in flight (double-click safe, review P1-1)', async () => {
    let resolveRebuild!: (v: unknown) => void
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') {
        return { schedules: [{ ...INDUSTRY[0], target_id: 'p1', target_type: 'project' }] }
      }
      if (channel === 'schedules:rebuildArchive') {
        return new Promise((resolve) => { resolveRebuild = resolve })
      }
      if (channel === 'schedules:inbox') return { runs: [] }
      if (channel === 'schedules:runs') return { runs: [] }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return {}
    })
    render(
      <ConfirmProvider>
        <SubscriptionAdminView onOpenRead={() => {}} onOpenReplay={() => {}} />
      </ConfirmProvider>,
    )
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(1))
    fireEvent.click(screen.getByText('订阅0'))
    const btn = await waitFor(() =>
      screen.getByText('schedule.rebuildArchive') as HTMLButtonElement)
    fireEvent.click(btn)
    await waitFor(() => expect(btn.disabled).toBe(true))   // in-flight 禁用
    resolveRebuild({ ok: true })
    await waitFor(() => expect(btn.disabled).toBe(false))  // 完成恢复
  })
})
