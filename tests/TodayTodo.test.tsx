/** tests/TodayTodo.test.tsx — 3.0 三期 Task 4:首页今日待办 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/home/TodayTodo.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { TodayTodo } from '../src/features/home/TodayTodo'
import type { DueTask } from '../src/features/project/affairs/types'

const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve() }) }

const mk = (over: Partial<DueTask>): DueTask => ({
  id: over.id ?? 't1', project_id: 'pa', phase: 'before', task_type: 'milestone',
  title: over.title ?? '拜访客户', description: '', template_id: null, status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-01T00:00:00', project_name: '招聘项目',
  ...over,
} as DueTask)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-10T10:00:00'))
  ;(window as any).electronAPI = { invoke: vi.fn(async () => ({})) }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks() })

describe('TodayTodo (3.0 三期 Task 4)', () => {
  it('renders due text per row: overdue / today / days-left, with cross-project names', () => {
    const tasks = [
      mk({ id: 't1', title: '逾期任务', due_date: '2026-10-08' }),
      mk({ id: 't2', title: '今日任务', due_date: '2026-10-10' }),
      mk({ id: 't3', title: '未来任务', due_date: '2026-10-13', task_type: 'material', project_name: 'B 项目' }),
    ]
    const { container, getByTitle } = render(<TodayTodo tasks={tasks} onOpenProject={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('homeTodo.title')
    expect(text).toContain('逾期任务')
    expect(text).toContain('homeTodo.overdueDays')
    expect(text).toContain('homeTodo.dueToday')
    expect(text).toContain('homeTodo.dueInDays')
    expect(getByTitle('homeTodo.upload')).toBeTruthy()          // material 行动作
    expect(text).toContain('B 项目')                            // 跨项目名称
  })

  it('upload button navigates to materials without triggering row nav (stopPropagation)', () => {
    const onOpenProject = vi.fn()
    const { getByTitle } = render(
      <TodayTodo tasks={[mk({ task_type: 'material', due_date: '2026-10-10' })]} onOpenProject={onOpenProject} />)
    fireEvent.click(getByTitle('homeTodo.upload'))
    expect(onOpenProject).toHaveBeenCalledWith('pa', 'materials')
    expect(onOpenProject).toHaveBeenCalledTimes(1)
  })

  it('check button invokes affairs:updateTask and dispatches app:tasks-changed', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const { getByTitle } = render(
      <TodayTodo tasks={[mk({ id: 't9', due_date: '2026-10-10' })]} onOpenProject={vi.fn()} />)
    fireEvent.click(getByTitle('homeTodo.done'))
    await flush()
    expect((window as any).electronAPI.invoke).toHaveBeenCalledWith(
      'affairs:updateTask', { projectId: 'pa', taskId: 't9', status: 'done' })
    const ev = dispatchSpy.mock.calls.map(([e]) => e as CustomEvent).find((e) => e.type === 'app:tasks-changed')
    expect(ev).toBeTruthy()
  })

  it('renders nothing when tasks empty', () => {
    const { container } = render(<TodayTodo tasks={[]} onOpenProject={vi.fn()} />)
    expect(container.querySelector('section')).toBeNull()
  })
})
