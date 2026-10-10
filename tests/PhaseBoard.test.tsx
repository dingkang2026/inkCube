/** tests/PhaseBoard.test.tsx — 3.0 二期 Task 7:时期分组/折叠/状态色/pending 灰态/general 单组 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/affairs/PhaseBoard.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { PhaseBoard } from '../src/features/project/affairs/PhaseBoard'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

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
      task({}),
      task({ id: 't2', phase: 'during', title: '尽调材料' }),
      task({ id: 't3', phase: 'after', title: '企业月报', task_type: 'recurring', occurrence_key: '2026-10' }),
    ]
    const { container } = render(<PhaseBoard {...base} tasks={tasks} />)
    const text = container.textContent!
    expect(text).toContain('上传BP')                              // 当前期展开
    expect(text).not.toContain('尽调材料')                          // 其他时期折叠为计数
    expect(text).toContain('project.stage.recruitmentDuring')     // 折叠组头仍可见
  })

  it('marks overdue row with error class and days text', () => {
    const { container } = render(<PhaseBoard {...base} tasks={[task({ due_date: '2026-10-01' })]} />)
    expect(container.querySelector('.rowErr')).toBeTruthy()
    expect(container.textContent).toContain('affairs.overdueDays')
  })

  it('renders general type as flat list without phase bar', () => {
    const { container } = render(
      <PhaseBoard {...base} projectType="general" stage="none"
        tasks={[task({ title: '通用资料' }), task({ id: 't9', title: '通用报告', task_type: 'report' })]} />
    )
    expect(container.textContent).toContain('通用资料')
    expect(container.textContent).toContain('通用报告')
    expect(container.querySelector('.phaseBar')).toBeNull()
  })

  it('renders pending_activation dimmed with hint text', () => {
    const { container } = render(
      <PhaseBoard {...base} tasks={[task({ status: 'pending_activation', title: '推介方案' })]} />
    )
    expect(container.querySelector('.rowPending')).toBeTruthy()
    expect(container.textContent).toContain('affairs.pendingHint')
  })

  it('expands collapsed group on header click (view-only, §6.4-2)', () => {
    const tasks = [task({}), task({ id: 't2', phase: 'during', title: '尽调材料' })]
    const { container } = render(<PhaseBoard {...base} tasks={tasks} />)
    expect(container.textContent).not.toContain('尽调材料')
    // 组头用类名定位:同文案也出现在顶部 phaseBar 段,getByText 会多匹配(计划测试 bug)
    const head = Array.from(container.querySelectorAll('.groupHead'))
      .find((b) => b.textContent!.includes('recruitmentDuring'))!
    fireEvent.click(head)
    expect(container.textContent).toContain('尽调材料')   // 仅展开视图,不激活任务
  })

  it('fires onCreateTask and onOpenDrawer', () => {
    const { getByTitle, container } = render(<PhaseBoard {...base} tasks={[task()]} />)
    fireEvent.click(getByTitle('affairs.addTask'))
    expect(base.onCreateTask).toHaveBeenCalled()
    fireEvent.click(container.querySelector('.row')!)
    expect(base.onOpenDrawer).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }))
  })

  it('renders malformed due_date verbatim without NaN days, and puts no-due last', () => {
    const { container } = render(
      <PhaseBoard {...base} tasks={[
        task({ id: 'bad', due_date: 'abcd-xy-zw' }),   // API 直调坏值
        task({ id: 'nodue' }),
        task({ id: 'due', due_date: '2026-10-05' }),
      ]} />
    )
    const text = container.textContent!
    expect(text).toContain('abcd-xy-zw')              // 坏值原文呈现,不产碎片/NaN
    expect(text).not.toContain('NaN')
    const dues = Array.from(container.querySelectorAll('.row .due')).map((el) => el.textContent)
    expect(dues.indexOf('—')).toBeGreaterThan(dues.indexOf('10-05'))  // 无 due 殿后(主键位排序)
  })
})
