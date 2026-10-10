/** tests/HomePage.test.tsx — 3.0 三期 Task 5:首页装配(待办/日历/卡片墙替换最近项目) */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ status: null, feed: [], markRead: vi.fn() }),
}))
const cssMock = vi.hoisted(() => ({ default: new Proxy({}, { get: (_t: unknown, k: unknown) => String(k) }) }))
vi.mock('../src/features/home/HomePage.module.css', () => cssMock)
vi.mock('../src/features/home/TodayTodo.module.css', () => cssMock)
vi.mock('../src/features/home/UpcomingCalendar.module.css', () => cssMock)
vi.mock('../src/features/home/ProjectCardWall.module.css', () => cssMock)
vi.mock('../src/features/project/affairs/HealthDot.module.css', () => cssMock)
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { HomePage } from '../src/features/home/HomePage'
import type { DueTask } from '../src/features/project/affairs/types'

const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve() }) }

const mk = (id: string, due_date: string): DueTask => ({
  id, project_id: 'p1', phase: 'before', task_type: 'milestone', title: `任务${id}`,
  description: '', template_id: null, status: 'open', occurrence_key: null, due_date,
  recurrence_rule: null, auto_complete: null, linked_entity_type: null,
  linked_entity_id: null, completed_at: null, completed_by: null,
  created_at: '2026-10-01T00:00:00', project_name: '招聘项目',
} as DueTask)

const base = { onNavigate: vi.fn(), onOpenSettings: vi.fn(), onSelectProject: vi.fn() }
const projects = [{ id: 'p1', name: '招聘项目', description: '', type: 'general', stage: 'before', file_count: 0 }] as any[]

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-10T10:00:00'))
  ;(window as any).electronAPI = { invoke: vi.fn(async (channel: string) =>
    channel === 'system:getHealth'
      ? { status: 'ok', services: { llm: { status: 'ok' }, vlm: { status: 'ok' }, embedding: { status: 'ok' }, parsing: { status: 'ok' } } }
      : {}) }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks() })

describe('HomePage workbench assembly (3.0 三期 Task 5)', () => {
  it('renders today todo section with dueData; hidden without', async () => {
    const r1 = render(<HomePage {...base} projects={projects} dueData={{ tasks: [mk('t1', '2026-10-08')], reviews_counts: {} }} />)
    await flush()
    expect(r1.container.textContent).toContain('homeTodo.title')
    expect(r1.container.textContent).toContain('任务t1')
    r1.unmount()
    const r2 = render(<HomePage {...base} projects={projects} />)
    await flush()
    expect(r2.container.textContent).not.toContain('homeTodo.title')
  })

  it('card wall replaces recent projects list', async () => {
    const { container } = render(
      <HomePage {...base} projects={projects}
        dueData={{ tasks: [], reviews_counts: {} }}
        healthByProject={{ p1: { overdue: 0, weekDue: 0, reviewsPending: 0 } }} />)
    await flush()
    expect(container.textContent).toContain('homeTodo.cardWallTitle')
    expect(container.textContent).not.toContain('home.recentProjects')   // 旧列表标题退场
    expect(container.textContent).toContain('招聘项目')
  })

  it('card click navigates to project detail', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const onSelectProject = vi.fn()
    const { getByText } = render(
      <HomePage {...base} projects={projects} onSelectProject={onSelectProject}
        dueData={{ tasks: [], reviews_counts: {} }} />)
    await flush()
    fireEvent.click(getByText('招聘项目'))
    const ev = dispatchSpy.mock.calls.map(([e]) => e as CustomEvent).find((e) => e.type === 'app:navigate-project-tab')
    expect(ev).toBeTruthy()
    expect((ev as CustomEvent).detail).toEqual({ projectId: 'p1', container: 'project' })
    expect(onSelectProject).toHaveBeenCalledWith('p1')
  })
})
