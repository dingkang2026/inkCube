/** tests/HomePage.test.tsx — 3.0 三期 Task 5:首页装配(待办/日历/卡片墙;0.5 §4.2 打样修订 2026-10-18:
 *  概览统计卡 + 全宽 dashboard 网格,服务告警上移 SystemAlerts 后不再消费 getHealth) */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
const ledgerItems: Array<{ project_id?: string }> = []
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) =>
    kind === 'ledger_parse'
      ? { items: ledgerItems }        // 四期 T9:台账解析心跳(终态边沿重拉 latest)
      : { status: null, feed: [], markRead: vi.fn() },
}))
const cssMock = vi.hoisted(() => ({ default: new Proxy({}, { get: (_t: unknown, k: unknown) => String(k) }) }))
vi.mock('../src/features/home/HomePage.module.css', () => cssMock)
vi.mock('../src/features/home/TodayTodo.module.css', () => cssMock)
vi.mock('../src/features/home/UpcomingCalendar.module.css', () => cssMock)
vi.mock('../src/features/home/HomeOverviewBar.module.css', () => cssMock)
vi.mock('../src/features/home/QuickActions.module.css', () => cssMock)
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

const base = { onSelectProject: vi.fn(), onCreateTask: vi.fn(), onCreateProject: vi.fn() }
const projects = [{ id: 'p1', name: '招聘项目', description: '', type: 'general', stage: 'before', file_count: 0 }] as any[]

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-10T10:00:00'))
  ledgerItems.length = 0
  ;(window as any).electronAPI = { invoke: vi.fn(async () => ({})) }
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
    // Ruling 4(2026-10-18):空数据模块保留区块,渲染空态而非隐藏
    expect(r2.container.textContent).toContain('homeTodo.title')
    expect(r2.container.textContent).toContain('homeTodo.empty')
  })

  it('overview bar derives counts from dueData and projects (0.5 E1 T3)', async () => {
    const { container } = render(
      <HomePage {...base} projects={projects}
        dueData={{ tasks: [mk('t1', '2026-10-08'), mk('t2', '2026-10-10')], reviews_counts: {} }} />)
    await flush()
    const text = container.textContent!
    expect(text).toContain('home.stat.projects')
    expect(text).toContain('home.stat.overdue')
    expect(text).toContain('home.stat.dueToday')
    // 大数字派生:projects=1,逾期=1(t1,系统时间 2026-10-10),今日=1(t2);进行中=0(mock 无 active)
    const nums = Array.from(container.querySelectorAll('.num')).map((n) => n.textContent)
    expect(nums).toEqual(['1', '1', '1', '0'])
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

  it('wires latest ledger entry + due title into card wall (3.0 四期 T9)', async () => {
    ;(window as any).electronAPI = { invoke: vi.fn(async (channel: string) => {
      if (channel === 'ledger:listLatest')
        return { latest: [{ project_id: 'p1', page_key: 'risk_watchlist', entry_date: '2026-09', entry_type: 'risk', content: '企业被列入经营异常名录' }] }
      return {}
    }) }
    const { container } = render(
      <HomePage {...base} projects={projects}
        dueData={{ tasks: [mk('t1', '2026-10-08')], reviews_counts: {} }} />)
    await flush()
    const text = container.textContent!
    expect(text).toContain('企业被列入经营异常名录')   // 台账摘要行透传卡片墙
    expect(text).toContain('10/08')                   // 下一事务日期(卡片墙 nextDue 行)
    expect(text).toContain('任务t1')                   // 下一事务名(dueData 派生)
  })
})
