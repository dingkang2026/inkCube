/** tests/AffairsTab.test.tsx — 3.0 二期 Task 6:容器拉取/三视图切换/快切守卫
 *  Task 11 增:抽屉数据同步用例(TaskDrawer mock 为探针,记录 task prop)。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const invokeMock = vi.fn()
const drawerProps = vi.hoisted(() => ({ task: null as { id: string; title: string } | null }))
vi.mock('../src/features/project/affairs/TaskDrawer', () => ({
  // 探针渲染真实 DOM(卸载即消失,断言存在性;仅记 props 无法区分卸载)
  TaskDrawer: (props: { task: { id: string; title: string } }) => {
    drawerProps.task = props.task
    return <div data-testid="drawer-probe">{props.task.id}:{props.task.title}</div>
  },
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k,
    i18n: { language: 'zh-CN' },
  }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ byProject: new Map(), items: [] }),
}))
vi.mock('../src/features/project/affairs/AffairsTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
// 真实现子组件的 CSS module 也需 mock(否则 s.xxx 为 undefined,类名断言如 .calGrid 失效)
vi.mock('../src/features/project/affairs/PhaseBoard.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/project/affairs/RhythmCards.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/project/affairs/CalendarView.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { AffairsTab } from '../src/features/project/affairs/AffairsTab'
import { summarizeDueTasks, healthLevel, summarizeDueByProject } from '../src/features/project/affairs/types'
import type { PhaseTask, DueTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve() })
  }
}

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (ch: string) =>
    ch === 'affairs:listByProject'
      ? { tasks: [task({}), task({ id: 't2', task_type: 'recurring', occurrence_key: '2026-10' })] }
      : {})
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const base = { projectId: 'p1', projectType: 'recruitment' as const, stage: 'before' as const }

describe('AffairsTab container', () => {
  it('loads tasks on mount and switches three views', async () => {
    const { container, getByText } = render(<AffairsTab {...base} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:listByProject', { projectId: 'p1' })
    expect(container.textContent).toContain('上传BP')
    fireEvent.click(getByText('affairs.viewRhythm'))
    expect(container.textContent).toContain('affairs.rhythmEmpty') // 无已收节奏时的空态(月报卡片在 Task 8)
    fireEvent.click(getByText('affairs.viewCalendar'))
    expect(container.querySelector('.calGrid')).toBeTruthy()
  })

  it('refetches when refreshSignal bumps', async () => {
    const { rerender } = render(<AffairsTab {...base} refreshSignal={0} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledTimes(1)
    rerender(<AffairsTab {...base} refreshSignal={1} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledTimes(2)
  })

  it('drops stale responses when switching projects fast', async () => {
    let resolveA: (v: unknown) => void
    invokeMock.mockImplementationOnce(() => new Promise((r) => { resolveA = r }))
    const { rerender, container } = render(<AffairsTab {...base} />)
    rerender(<AffairsTab {...base} projectId="p2" />)
    await flush()
    resolveA!({ tasks: [task({ title: 'STALE-A' })] })
    await flush()
    expect(container.textContent).not.toContain('STALE-A')
  })

  it('clears previous project tasks on switch even while refetch is pending', async () => {
    const { rerender, container } = render(<AffairsTab {...base} />)
    await flush()
    expect(container.textContent).toContain('上传BP')
    let resolveB: (v: unknown) => void
    invokeMock.mockImplementationOnce(() => new Promise((r) => { resolveB = r }))
    rerender(<AffairsTab {...base} projectId="p2" />)
    // 切项目即清残留(评审 minor①):新项目响应未回前不得显示上一项目任务
    expect(container.textContent).not.toContain('上传BP')
    resolveB!({ tasks: [task({ id: 'b1', title: 'B任务', project_id: 'p2' })] })
    await flush()
    expect(container.textContent).toContain('B任务')
    expect(container.textContent).not.toContain('affairs.loadFailed')
  })

  it('shows load-failed state instead of fake-empty when fetch rejects', async () => {
    invokeMock.mockRejectedValueOnce(new Error('boom'))
    const { getByText, queryByText } = render(<AffairsTab {...base} />)
    await flush()
    // 失败不得伪装成空态(对齐一期 PulseBar pulseLoadFailed 原则)
    expect(getByText('affairs.loadFailed')).toBeTruthy()
    expect(queryByText('上传BP')).toBeNull()
  })

  it('syncs open drawer task to refreshed data and closes when task vanishes', async () => {
    const panelSpy = vi.fn()
    const { container, rerender } = render(
      <AffairsTab {...base} refreshSignal={0} onPanelOpenChange={panelSpy} />)
    await flush()
    const probe = () => container.querySelector('[data-testid="drawer-probe"]')
    // 打开抽屉:点看板任务卡
    fireEvent.click(container.querySelector('.row')!)
    expect(probe()).toBeTruthy()
    expect(probe()!.textContent).toBe('t1:上传BP')
    // 抽屉开着时重拉返回新数据 → 抽屉同步为最新对象(否则完成后状态徽章谎报)
    invokeMock.mockImplementation(async (ch: string) =>
      ch === 'affairs:listByProject' ? { tasks: [task({ title: '新标题' })] } : {})
    rerender(<AffairsTab {...base} refreshSignal={1} onPanelOpenChange={panelSpy} />)
    await flush()
    expect(probe()!.textContent).toBe('t1:新标题')
    // 重拉不含该任务(已删/被系统滚动清理)→ 抽屉自动卸载 + 复位 Chat 面板
    invokeMock.mockImplementation(async (ch: string) =>
      ch === 'affairs:listByProject' ? { tasks: [] } : {})
    rerender(<AffairsTab {...base} refreshSignal={2} onPanelOpenChange={panelSpy} />)
    await flush()
    expect(probe()).toBeNull()
    expect(panelSpy).toHaveBeenCalledWith(false)
  })
})

describe('summarizeDueTasks (Task 12)', () => {
  it('counts overdue and week-due, finds nearest overdue', () => {
    const now = new Date('2026-10-10T12:00:00')
    const mk = (over: Partial<PhaseTask>): PhaseTask => ({
      id: 'x', project_id: 'p1', phase: 'before', task_type: 'material',
      title: 'X', description: '', template_id: null, status: 'open',
      occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
      linked_entity_type: null, linked_entity_id: null, completed_at: null,
      completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
    })
    const sum = summarizeDueTasks([
      mk({ id: 'a', title: '过期项', due_date: '2026-10-08' }),
      mk({ id: 'b', title: '更早项', due_date: '2026-10-01' }),
      mk({ id: 'c', due_date: '2026-10-12' }),
      mk({ id: 'd', due_date: '2026-10-20' }),
      mk({ id: 'e', due_date: '2026-10-08', status: 'done' }),
    ], now)
    expect(sum.overdue).toBe(2)
    expect(sum.weekDue).toBe(1)
    expect(sum.nearestOverdueTitle).toBe('更早项')
    expect(sum.nearestOverdueDays).toBe(9)
  })
})

describe('healthLevel / summarizeDueByProject (3.0 三期 Task 2)', () => {
  const now = new Date('2026-10-10T10:00:00')
  const mk = (id: string, project_id: string, due_date: string | null): DueTask => ({
    id, project_id, phase: 'before', task_type: 'milestone', title: id, description: '',
    template_id: null, status: 'open', occurrence_key: null, due_date,
    recurrence_rule: null, auto_complete: null, linked_entity_type: null,
    linked_entity_id: null, completed_at: null, completed_by: null,
    created_at: '2026-10-01T00:00:00', project_name: 'P-' + project_id,
  } as DueTask)

  it('healthLevel: red > orange(weekDue/reviews) > green', () => {
    expect(healthLevel({ overdue: 1, weekDue: 0, reviewsPending: 5 })).toBe('red')
    expect(healthLevel({ overdue: 0, weekDue: 2, reviewsPending: 0 })).toBe('orange')
    expect(healthLevel({ overdue: 0, weekDue: 0, reviewsPending: 3 })).toBe('orange')
    expect(healthLevel({ overdue: 0, weekDue: 0, reviewsPending: 0 })).toBe('green')
  })

  it('summarizeDueByProject: groups with same overdue/weekDue semantics as summarizeDueTasks', () => {
    const tasks = [
      mk('t1', 'pa', '2026-10-08'),   // 逾期(now=10-10)
      mk('t2', 'pa', '2026-10-10'),   // 今日:非逾期、dueWithin
      mk('t3', 'pb', '2026-10-12'),   // 周内
      mk('t4', 'pb', '2026-10-20'),   // 界外不计数
    ]
    const sum = summarizeDueByProject(tasks, { pc: 4 }, now)
    expect(sum.pa).toEqual({ overdue: 1, weekDue: 1, reviewsPending: 0 })
    expect(sum.pb).toEqual({ overdue: 0, weekDue: 1, reviewsPending: 0 })
    expect(sum.pc).toEqual({ overdue: 0, weekDue: 0, reviewsPending: 4 })   // 纯发现项项目也出现
  })
})
