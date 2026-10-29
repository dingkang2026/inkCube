/** tests/PhaseBoard.test.tsx — 3.0 二期 Task 7(分组/折叠/状态色/pending/general)+ 拉齐补丁:
 *  recurring 过滤/头部计数/条件开关/周期提示条(§6.1.3) */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/project/affairs/PhaseBoard.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { PhaseBoard } from '../src/features/project/affairs/PhaseBoard'
import type { PhaseTask } from '../src/features/project/affairs/types'

const mk = (over: Partial<PhaseTask> = {}): PhaseTask => ({
  id: over.id ?? 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: over.title ?? '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
} as PhaseTask)

const base = { stage: 'before' as const, projectType: 'recruitment' as const,
  onCreateTask: vi.fn(), onOpenDrawer: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  // 固定时钟:逾期/7 日内断言不得依赖真实系统时间(否则跨日运行必炸)
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-10T12:00:00'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('PhaseBoard', () => {
  it('groups by phase, expands current stage, collapses others to header counts', () => {
    const tasks = [
      mk({}),
      mk({ id: 't2', phase: 'during', title: '尽调材料' }),
      mk({ id: 't3', phase: 'after', title: '企业月报', task_type: 'recurring', occurrence_key: '2026-10' }),
    ]
    const { container } = render(<PhaseBoard {...base} tasks={tasks} />)
    const text = container.textContent!
    expect(text).toContain('上传BP')                              // 当前期展开
    expect(text).not.toContain('尽调材料')                          // 其他时期折叠为计数
    expect(text).toContain('project.stage.recruitmentDuring')     // 折叠组头仍可见
  })

  it('marks overdue row with error class and days text', () => {
    const { container } = render(<PhaseBoard {...base} tasks={[mk({ due_date: '2026-10-01' })]} />)
    expect(container.querySelector('.rowErr')).toBeTruthy()
    expect(container.textContent).toContain('affairs.overdueDays')
  })

  it('renders general type as flat list without phase grouping', () => {
    const { container } = render(
      <PhaseBoard {...base} projectType="general" stage="none"
        tasks={[mk({ title: '通用资料' }), mk({ id: 't9', title: '通用报告', task_type: 'report' })]} />
    )
    expect(container.textContent).toContain('通用资料')
    expect(container.textContent).toContain('通用报告')
  })

  it('renders pending_activation dimmed with hint text', () => {
    const { container } = render(
      <PhaseBoard {...base} tasks={[mk({ status: 'pending_activation', title: '推介方案' })]} />
    )
    expect(container.querySelector('.rowPending')).toBeTruthy()
    expect(container.textContent).toContain('affairs.pendingHint')
  })

  it('expands collapsed group on header click (view-only, §6.4-2)', () => {
    const tasks = [mk({}), mk({ id: 't2', phase: 'during', title: '尽调材料' })]
    const { container } = render(<PhaseBoard {...base} tasks={tasks} />)
    expect(container.textContent).not.toContain('尽调材料')
    // 组头用类名定位(顶部时期进度条已撤下,组头是期名唯一出现处,类名定位保留稳健)
    const head = Array.from(container.querySelectorAll('.groupHead'))
      .find((b) => b.textContent!.includes('recruitmentDuring'))!
    fireEvent.click(head)
    expect(container.textContent).toContain('尽调材料')   // 仅展开视图,不激活任务
  })

  it('fires onCreateTask and onOpenDrawer', () => {
    const { getByTitle, container } = render(<PhaseBoard {...base} tasks={[mk()]} />)
    fireEvent.click(getByTitle('affairs.addTask'))
    expect(base.onCreateTask).toHaveBeenCalled()
    fireEvent.click(container.querySelector('.row')!)
    expect(base.onOpenDrawer).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }))
  })

  it('renders malformed due_date verbatim without NaN days, and puts no-due last', () => {
    const { container } = render(
      <PhaseBoard {...base} tasks={[
        mk({ id: 'bad', due_date: 'abcd-xy-zw' }),   // API 直调坏值
        mk({ id: 'nodue' }),
        mk({ id: 'due', due_date: '2026-10-05' }),
      ]} />
    )
    const text = container.textContent!
    expect(text).toContain('abcd-xy-zw')              // 坏值原文呈现,不产碎片/NaN
    expect(text).not.toContain('NaN')
    const dues = Array.from(container.querySelectorAll('.row .due')).map((el) => el.textContent)
    expect(dues.indexOf('—')).toBeGreaterThan(dues.indexOf('10-05'))  // 无 due 殿后(主键位排序)
  })

  it('done 行日期列显示完成时间;存量缺 completed_at 回落原口径(2026-10-28 补齐)', () => {
    const { container } = render(
      <PhaseBoard {...base} tasks={[
        mk({ id: 'd1', status: 'done', due_date: '2026-10-01', completed_at: '2026-10-08T14:30:00' }),
        mk({ id: 'd2', status: 'done', due_date: '2026-10-02' }),   // 存量防御:无 completed_at
      ]} />
    )
    const dues = Array.from(container.querySelectorAll('.row .due')).map((el) => el.textContent)
    expect(dues[0]).toBe('affairs.doneAtShort:{"date":"10-08"}')   // 完成时间优先于截止日
    expect(dues[1]).toBe('10-02')                                   // 回落显示截止日,不产碎片
  })

  // ── 3.0 拉齐补丁(§6.1.3)──

  it('filters recurring instances out of the board (边界:周期事务唯一入口在节奏视图)', () => {
    const { container } = render(
      <PhaseBoard {...base} tasks={[
        mk({ id: 'm1', title: '普通任务' }),
        mk({ id: 'r1', task_type: 'recurring', title: '企业月报实例', recurrence_rule: { freq: 'monthly', due_day: 15 } }),
      ]} />)
    const text = container.textContent!
    expect(text).toContain('普通任务')
    expect(text).not.toContain('企业月报实例')
  })

  it('shows recurring hint only when recurring instances exist', () => {
    const withRec = render(
      <PhaseBoard {...base} tasks={[
        mk({ id: 'm1' }),
        mk({ id: 'r1', task_type: 'recurring', recurrence_rule: { freq: 'monthly', due_day: 15 } }),
      ]} />)
    expect(withRec.container.textContent).toContain('affairs.recurringHint')
    withRec.unmount()

    const noRec = render(<PhaseBoard {...base} tasks={[mk({ id: 'm1' })]} />)
    expect(noRec.container.textContent).not.toContain('affairs.recurringHint')
  })

  it('renders skipped toggle only when a skipped board task exists; toggle reveals the row', () => {
    const noSkip = render(<PhaseBoard {...base} tasks={[mk({ id: 'm1', status: 'open' })]} />)
    expect(noSkip.container.textContent).not.toContain('affairs.showSkipped')
    noSkip.unmount()

    const { container, getByLabelText } = render(
      <PhaseBoard {...base} tasks={[
        mk({ id: 'm1', status: 'open', title: '活跃任务' }),
        mk({ id: 'm2', status: 'skipped', title: '已跳过任务' }),
      ]} />)
    expect(container.textContent).not.toContain('已跳过任务')   // 默认隐藏
    const toggle = getByLabelText('affairs.showSkipped') as HTMLInputElement
    expect(toggle).toBeTruthy()
    fireEvent.click(toggle)
    expect(container.textContent).toContain('已跳过任务')
  })

  it('head count scopes to current phase: total excludes skipped/pending, week counts dueWithin 7d', () => {
    const { container } = render(
      <PhaseBoard {...base} stage="during" projectType="investment" tasks={[
        mk({ id: 'a', phase: 'during', status: 'open', due_date: '2026-10-12' }),   // week
        mk({ id: 'b', phase: 'during', status: 'open', due_date: '2026-10-20' }),
        mk({ id: 'c', phase: 'during', status: 'done', due_date: '2026-10-08' }),
        mk({ id: 'd', phase: 'during', status: 'skipped' }),
        mk({ id: 'e', phase: 'during', status: 'pending_activation' }),
        mk({ id: 'f', phase: 'before', status: 'open', due_date: '2026-10-11' }),   // 非当前期不计
      ]} />)
    const text = container.textContent!
    expect(text).toContain('project.stage.investmentDuring')   // 期名前缀
    expect(text).toContain('"total":3')
    expect(text).toContain('"week":1')
  })

  it('keeps general-type head count phase-name-free (全量口径)', () => {
    const { container } = render(
      <PhaseBoard {...base} projectType="general" stage="none" tasks={[
        mk({ id: 'g1', status: 'open', due_date: '2026-10-12' }),
        mk({ id: 'g2', status: 'open', due_date: '2026-10-30' }),
      ]} />)
    const text = container.textContent!
    expect(text).toContain('"total":2')
    expect(text).toContain('"week":1')
  })
})
