/**
 * tests/SubscriptionAdminView.test.tsx — 定时订阅管理台单测（2.11 §8.1）。
 *
 * 覆盖（2026-10-22 返回交互改造回归）：
 * - 收件箱期次行点击 → onOpenRead(run 完整对象，携带 schedule_id 供返回恢复选中)；
 * - 期次历史行点击统一进报告页（历史版本按 task_id 分流进回放，已废弃——
 *   回放唯一入口为报告页头部「执行过程」切换）；
 * - restoreScheduleId 挂载恢复选中订阅（read/replay 返回宿主页不落回收件箱）；
 * - 「返回收件箱」按钮 → 右列复位收件箱默认视图。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { SubscriptionAdminView } from '../src/features/schedule/SubscriptionAdminView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => () => {},
}))

vi.mock('../src/components/ConfirmDialog', async (orig) => {
  const mod = await orig<typeof import('../src/components/ConfirmDialog')>()
  return {
    ...mod,
    useConfirm: () => async () => true,
  }
})

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

const wireInvoke = () =>
  vi.fn((channel: string) => {
    if (channel === 'schedules:list') return Promise.resolve({ schedules: [SCHEDULE], has_more: false })
    if (channel === 'schedules:inbox') return Promise.resolve({ runs: [INBOX_RUN], has_more: false })
    if (channel === 'schedules:runs') return Promise.resolve({ runs: [HISTORY_RUN], has_more: false })
    return Promise.resolve({ ok: true })
  })

/** 排空 microtask（usePagedList 三列表拉取链） */
const flush = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
})

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

describe('SubscriptionAdminView（2.11 §8.1 管理台）', () => {
  it('收件箱期次行点击 → onOpenRead(run)：携带 schedule_id 供返回恢复选中', async () => {
    const onOpenRead = vi.fn()
    render(<SubscriptionAdminView onOpenRead={onOpenRead} />)
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
    const { container } = render(<SubscriptionAdminView onOpenRead={onOpenRead} />)
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
    render(<SubscriptionAdminView onOpenRead={() => {}} restoreScheduleId="sch_1" />)
    await flush()
    // 右列直接是详情视图（期次历史），而非收件箱
    expect(screen.queryByText('schedule.inboxTitle')).toBeNull()
    expect(screen.getByText('schedule.runsTitle')).toBeTruthy()
    expect(screen.getByTestId('back-to-inbox')).toBeTruthy()
  })

  it('「返回收件箱」按钮 → 右列复位收件箱默认视图', async () => {
    render(<SubscriptionAdminView onOpenRead={() => {}} restoreScheduleId="sch_1" />)
    await flush()
    fireEvent.click(screen.getByTestId('back-to-inbox'))
    expect(screen.getByText('schedule.inboxTitle')).toBeTruthy()
    expect(screen.queryByTestId('back-to-inbox')).toBeNull()
  })
})
