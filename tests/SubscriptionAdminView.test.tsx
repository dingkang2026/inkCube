/**
 * tests/SubscriptionAdminView.test.tsx — 定时订阅管理台单测（2.11 §8.1）。
 *
 * 覆盖（2026-10-23 恢复合并：既有 7 类行为回归 + 返回交互改造 5 用例）。
 * 既有行为（原 E3 T7 版用例，自改动前基线恢复并按新签名适配——删 onOpenReplay、
 * 真 ConfirmProvider 弹层、重建档案按钮前置去向行懒解析）：
 * - 分类页签 → schedules:list 服务端 category 参数 + 行数收窄；
 * - 搜索防抖 300ms → q 参数下沉；
 * - 勾选 2 行 → 批量条出现 + 删除确认文案含数量（真 ConfirmProvider）；
 * - 收件箱未读优先 + 未读点（run-unread-dot）；
 * - 绑定订阅 → 重建档案按钮调 schedules:rebuildArchive；
 * - 未绑定 → 无重建档案死入口；
 * - 重建 in-flight 禁用（防双击）。
 * 返回交互改造回归（2026-10-22）：
 * - 收件箱期次行点击 → onOpenRead(run 完整对象，携带 schedule_id 供返回恢复选中)；
 * - 期次历史行点击统一进报告页（历史版本按 task_id 分流进回放，已废弃——
 *   回放唯一入口为报告页头部「执行过程」切换）；
 * - restoreScheduleId 挂载恢复选中订阅（read/replay 返回宿主页不落回收件箱）；
 * - 「返回收件箱」按钮 → 右列复位收件箱默认视图；
 * - 暂停行文字灰显（rowPaused）+ 状态点互斥（2026-10-23，§8.1/§9.2 不变量 1）。
 *
 * 时序说明：既有筛选类用例含 300ms 真实防抖，用 waitFor（真实计时器）；
 * 返回交互类用例无防抖依赖，用 flush（排空 microtask，更快）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { SubscriptionAdminView } from '../src/features/schedule/SubscriptionAdminView'
import { ConfirmProvider } from '../src/components/ConfirmDialog'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, p?: Record<string, unknown>) =>
      p && 'n' in p ? `${k}(${p['n']})` : k,
  }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => () => {},
}))

// ScheduleFormModal 打桩：管理台另引用 parseInterval/WEEKDAYS 渲染周期文案
vi.mock('../src/features/schedule/ScheduleFormModal', () => ({
  ScheduleFormModal: (props: { open?: boolean }) => (props.open ? <div data-testid="schedule-form-modal" /> : null),
  parseInterval: () => ({ days: [1], time: '09:00' }),
  WEEKDAYS: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
}))

const SCHEDULE = {
  id: 'sch_1', name: '华润舆情', prompt: '扫描并输出报告', category: 'corporate',
  interval_type: 'daily', interval_value: '09:00',
  target_type: null, target_id: null, auto_import: 1, enabled: 1,
  next_run_at: null, last_run_at: null, consecutive_failures: 0,
  archive_last_run_id: null, archive_file_id: null, is_running: false,
  latest_done: null, created_at: '', updated_at: '',
}

/** 批量构造（既有筛选用例需 30 行规模验证服务端过滤 + 行数收窄） */
const mkSchedule = (i: number, category: string) => ({
  ...SCHEDULE,
  id: `sch_${i}`,
  name: `订阅${i}`,
  category,
  next_run_at: '2026-10-20T09:00:00',
  created_at: '2026-10-19T08:00:00',
})

const INDUSTRY = Array.from({ length: 10 }, (_, i) => mkSchedule(i, 'industry'))
const CORPORATE = Array.from({ length: 20 }, (_, i) => mkSchedule(100 + i, 'corporate'))

const INBOX_RUN = {
  id: 'run_1', schedule_id: 'sch_1', task_id: 't1', status: 'done',
  report_path: 'x.md', summary: ['要点A'], read_at: null,
  started_at: '2026-10-22T09:00:00', finished_at: '2026-10-22T09:02:00',
  schedule_name: '华润舆情', archive_last_run_id: null, error: null,
}

const HISTORY_RUN = {
  ...INBOX_RUN, id: 'run_2', read_at: '2026-10-21T10:00:00',
  started_at: '2026-10-21T09:00:00', finished_at: '2026-10-21T09:01:00',
}

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string, args?: Record<string, unknown>) => {
    if (channel === 'schedules:list') {
      let rows = [...INDUSTRY, ...CORPORATE]
      if (args?.category) rows = rows.filter((r) => r.category === args.category)
      if (args?.q) rows = rows.filter((r) => r.name.includes(args.q as string))
      return { schedules: rows, has_more: false }
    }
    if (channel === 'schedules:inbox') return { runs: [INBOX_RUN], has_more: false }
    if (channel === 'schedules:runs') return { runs: [HISTORY_RUN], has_more: false }
    if (channel === 'knowledge:listProjects') return { projects: [] }
    if (channel === 'baseLib:list') return { base_libs: [] }
    return { ok: true }
  })
  ;(window as { electronAPI?: unknown }).electronAPI = { invoke: invokeMock, on: () => () => {} }
})

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

/** 统一经真 ConfirmProvider 挂载（删除确认文案断言依赖真弹层渲染） */
const renderView = (props: React.ComponentProps<typeof SubscriptionAdminView>) =>
  render(
    <ConfirmProvider>
      <SubscriptionAdminView {...props} />
    </ConfirmProvider>,
  )

const lastListArgs = () => {
  const calls = invokeMock.mock.calls.filter((c) => c[0] === 'schedules:list')
  return calls[calls.length - 1]?.[1] ?? {}
}

/** 排空 microtask（usePagedList 三列表拉取链；无防抖依赖的用例用） */
const flush = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

describe('SubscriptionAdminView（2.11 §8.1 管理台）', () => {
  /* ── 既有行为回归（waitFor：含 300ms 真实防抖） ── */

  it('分类页签经服务端 category 过滤（corporate → 20 行）', async () => {
    renderView({ onOpenRead: () => {} })
    // 用行内 checkbox 计数断言列表规模（收件箱 RunRow 无 checkbox，不干扰）
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(30))
    fireEvent.click(screen.getByRole('tab', { name: 'schedule.tab.corporate' }))
    await waitFor(() => expect(lastListArgs()).toMatchObject({ category: 'corporate' }))
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(20))
  })

  it('搜索防抖后 q 下沉服务端 schedules:list', async () => {
    renderView({ onOpenRead: () => {} })
    await waitFor(() => expect(screen.getByPlaceholderText('schedule.searchPlaceholder')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('schedule.searchPlaceholder'), {
      target: { value: '订阅10' },
    })
    await waitFor(() => expect(lastListArgs()).toMatchObject({ q: '订阅10' }))
    await waitFor(() => expect(screen.getAllByText(/订阅10\d?$/).length).toBeGreaterThan(0))
  })

  it('勾选 2 行出批量条；删除确认文案含数量（真 ConfirmProvider 弹层）', async () => {
    renderView({ onOpenRead: () => {} })
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBeGreaterThanOrEqual(2))
    const boxes = screen.getAllByRole('checkbox')
    fireEvent.click(boxes[0])
    fireEvent.click(boxes[1])
    expect(screen.getByTestId('batch-bar')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.batchDelete'))
    await waitFor(() =>
      expect(screen.getByText(/schedule\.batchDeleteConfirm\(2\)/)).toBeTruthy())
  })

  it('右列默认收件箱：未读在前 + 未读点渲染', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') return { schedules: [], has_more: false }
      if (channel === 'schedules:inbox') {
        return {
          runs: [
            { id: 'run_unread', schedule_id: 'sch_0', task_id: null, status: 'done',
              report_path: null, summary: ['要点一'], read_at: null,
              started_at: '2026-10-19T09:00:00', finished_at: '2026-10-19T09:01:00',
              schedule_name: '订阅0', archive_last_run_id: null, error: null },
            { id: 'run_read', schedule_id: 'sch_100', task_id: null, status: 'done',
              report_path: null, summary: ['要点二'], read_at: '2026-10-19T10:00:00',
              started_at: '2026-10-18T09:00:00', finished_at: '2026-10-18T09:01:00',
              schedule_name: '订阅100', archive_last_run_id: null, error: null },
          ],
          has_more: false,
        }
      }
      if (channel === 'schedules:runs') return { runs: [], has_more: false }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return { ok: true }
    })
    renderView({ onOpenRead: () => {} })
    await waitFor(() => expect(screen.getByText('schedule.inboxTitle')).toBeTruthy())
    const rows = screen.getAllByTestId('run-row')
    expect(rows[0].textContent).toContain('订阅0')
    expect(rows[0].querySelector('[data-testid="run-unread-dot"]')).toBeTruthy()
    expect(rows[1].textContent).toContain('订阅100')
    expect(rows[1].querySelector('[data-testid="run-unread-dot"]')).toBeNull() // 已读无未读点
  })

  it('绑定订阅显示重建档案按钮；点击调 schedules:rebuildArchive（§6 T10）', async () => {
    // 重建档案按钮位于去向行内（selected.target_id && targetName 懒解析命中才渲染）
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') {
        return { schedules: [{ ...mkSchedule(0, 'industry'), target_id: 'p1', target_type: 'project' }], has_more: false }
      }
      if (channel === 'schedules:inbox') return { runs: [], has_more: false }
      if (channel === 'schedules:runs') return { runs: [], has_more: false }
      if (channel === 'knowledge:listProjects') return { projects: [{ id: 'p1', name: '项目一' }] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return { ok: true }
    })
    renderView({ onOpenRead: () => {} })
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(1))
    fireEvent.click(screen.getByText('订阅0'))                    // 选中 → 详情面板
    fireEvent.click(await waitFor(() => screen.getByText('schedule.rebuildArchive')))
    await waitFor(() => expect(
      invokeMock.mock.calls.some((c) => c[0] === 'schedules:rebuildArchive'
        && c[1] === 'sch_0')).toBe(true))
  })

  it('未绑定订阅无重建档案死入口', async () => {
    // 单行 mock：默认 mock 的收件箱 run-row 也含「订阅0」文本，getByText 会撞重复
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') return { schedules: [mkSchedule(0, 'industry')], has_more: false }
      if (channel === 'schedules:inbox') return { runs: [], has_more: false }
      if (channel === 'schedules:runs') return { runs: [], has_more: false }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return { ok: true }
    })
    renderView({ onOpenRead: () => {} })
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(1))
    fireEvent.click(screen.getByText('订阅0'))                    // target_id=null
    await waitFor(() => expect(screen.getByText('schedule.runNow')).toBeTruthy())  // 详情面板已打开
    expect(screen.queryByText('schedule.rebuildArchive')).toBeNull()
  })

  it('重建按钮 in-flight 禁用（防双击，review P1-1）', async () => {
    let resolveRebuild!: (v: unknown) => void
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') {
        return { schedules: [{ ...mkSchedule(0, 'industry'), target_id: 'p1', target_type: 'project' }], has_more: false }
      }
      if (channel === 'schedules:rebuildArchive') {
        return new Promise((resolve) => { resolveRebuild = resolve })
      }
      if (channel === 'schedules:inbox') return { runs: [], has_more: false }
      if (channel === 'schedules:runs') return { runs: [], has_more: false }
      if (channel === 'knowledge:listProjects') return { projects: [{ id: 'p1', name: '项目一' }] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return { ok: true }
    })
    renderView({ onOpenRead: () => {} })
    await waitFor(() => expect(screen.getAllByRole('checkbox').length).toBe(1))
    fireEvent.click(screen.getByText('订阅0'))
    const btn = await waitFor(() =>
      screen.getByText('schedule.rebuildArchive') as HTMLButtonElement)
    fireEvent.click(btn)
    await waitFor(() => expect(btn.disabled).toBe(true))   // in-flight 禁用
    resolveRebuild({ ok: true })
    await waitFor(() => expect(btn.disabled).toBe(false))  // 完成恢复
  })

  /* ── 返回交互改造回归（flush：无防抖依赖） ── */

  it('收件箱期次行点击 → onOpenRead(run)：携带 schedule_id 供返回恢复选中', async () => {
    const onOpenRead = vi.fn()
    renderView({ onOpenRead })
    await flush()
    expect(screen.getByText('schedule.inboxTitle')).toBeTruthy() // 默认右列 = 收件箱
    fireEvent.click(screen.getByTestId('run-row'))
    expect(onOpenRead).toHaveBeenCalledTimes(1)
    const run = onOpenRead.mock.calls[0][0] as { id: string; schedule_id: string }
    expect(run.id).toBe('run_1')
    expect(run.schedule_id).toBe('sch_1')
  })

  it('期次历史行点击（run 带 task_id）统一进报告页：onOpenRead，不再分流回放', async () => {
    const onOpenRead = vi.fn()
    const { container } = renderView({ onOpenRead })
    await flush()
    // 选中订阅 → 右列切详情 + 期次历史（左列订阅名与收件箱行订阅名同名，
    // 按类名子串定位左列 schedMain 按钮，不受 CSS modules hash 影响）
    const schedMain = container.querySelector('button[class*="schedMain"]') as HTMLElement
    fireEvent.click(schedMain)
    await flush()
    expect(screen.getByText('schedule.runsTitle')).toBeTruthy()
    fireEvent.click(screen.getByTestId('run-row'))
    expect(onOpenRead).toHaveBeenCalledTimes(1)
    expect((onOpenRead.mock.calls[0][0] as { task_id?: string }).task_id).toBe('t1')
  })

  it('restoreScheduleId 挂载恢复选中订阅（返回宿主页不落回收件箱）', async () => {
    renderView({ onOpenRead: () => {}, restoreScheduleId: 'sch_1' })
    await flush()
    // 右列直接是详情视图（期次历史），而非收件箱
    expect(screen.queryByText('schedule.inboxTitle')).toBeNull()
    expect(screen.getByText('schedule.runsTitle')).toBeTruthy()
    expect(screen.getByTestId('back-to-inbox')).toBeTruthy()
  })

  it('「返回收件箱」按钮 → 右列复位收件箱默认视图', async () => {
    renderView({ onOpenRead: () => {}, restoreScheduleId: 'sch_1' })
    await flush()
    fireEvent.click(screen.getByTestId('back-to-inbox'))
    expect(screen.getByText('schedule.inboxTitle')).toBeTruthy()
    expect(screen.queryByTestId('back-to-inbox')).toBeNull()
  })

  it('暂停行文字灰显（rowPaused）+ 状态点互斥（2026-10-23，§8.1/§9.2 不变量 1）', async () => {
    const rows = [
      { ...SCHEDULE, id: 'sch_paused', name: '已暂停', enabled: 0, consecutive_failures: 0 },
      { ...SCHEDULE, id: 'sch_breaker', name: '已熔断', enabled: 0, consecutive_failures: 3 },
      { ...SCHEDULE, id: 'sch_active', name: '运行中', enabled: 1 },
    ]
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') return { schedules: rows, has_more: false }
      if (channel === 'schedules:inbox') return { runs: [], has_more: false }
      if (channel === 'schedules:runs') return { runs: [], has_more: false }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return { ok: true }
    })
    renderView({ onOpenRead: () => {} })
    await flush()
    const mainOf = (name: string) =>
      screen.getByText(name).closest('button[class*="schedMain"]') as HTMLElement
    // 手动暂停：schedMain 带 rowPaused，状态点淡灰（dotPaused）非红
    const paused = mainOf('已暂停')
    expect(paused.className).toContain('rowPaused')
    expect(paused.querySelector('span[class*="dotPaused"]')).toBeTruthy()
    expect(paused.querySelector('span[class*="dotAbnormal"]')).toBeNull()
    // 熔断：行文字仍灰显（enabled=0），但状态点红色不淡显（需行动）
    const breaker = mainOf('已熔断')
    expect(breaker.className).toContain('rowPaused')
    expect(breaker.querySelector('span[class*="dotAbnormal"]')).toBeTruthy()
    expect(breaker.querySelector('span[class*="dotPaused"]')).toBeNull()
    // 活跃行：无灰显
    expect(mainOf('运行中').className).not.toContain('rowPaused')
  })
})
