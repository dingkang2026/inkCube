/** tests/StageLaneView.test.tsx — V2-E4 T3:首页组合泳道(0.6 §3.7)
 *  覆盖:STAGE_COLUMNS 列序分组/空列折叠/卡上 chip+脉搏句+下一事务(逾期红字)/点击回调/全空态 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/home/StageLaneView.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
// StageLaneView 复用 ProjectCardWall.daysOverdue(§3.7 同源同口径),连带加载其模块依赖
vi.mock('../src/features/home/ProjectCardWall.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/project/affairs/HealthDot.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { StageLaneView } from '../src/features/home/StageLaneView'
import type { Project } from '../src/features/knowledge/types'

afterEach(() => cleanup())

const proj = (over: Partial<Project> = {}): Project =>
  ({ id: 'p1', name: '招商项目', description: '', type: 'recruitment', stage: 'before', file_count: 0, ...over } as Project)

describe('StageLaneView (V2-E4 §3.7)', () => {
  it('按 STAGE_COLUMNS 列序分组,列头带计数,未知阶段归「其他」列', () => {
    const { container } = render(
      <StageLaneView
        projects={[
          proj({ id: 'a', stage: 'before' }),
          proj({ id: 'b', type: 'investment', stage: 'during' }),
          proj({ id: 'c', stage: 'weird' }),   // 非法值 → none 列(§3.7 列语义)
        ]}
        onSelect={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('project.stage.before')
    expect(text).toContain('project.stage.during')
    expect(text).toContain('project.stage.after')
    expect(text).toContain('project.stage.other')
    const counts = Array.from(container.querySelectorAll('[class*="laneCount"]')).map((n) => n.textContent)
    expect(counts).toEqual(['1', '1', '0', '1'])
  })

  it('空列折叠为窄列(laneEmpty),阶段语义仍可见', () => {
    const { container } = render(<StageLaneView projects={[proj()]} onSelect={vi.fn()} />)
    const empties = container.querySelectorAll('[class*="laneEmpty"]')
    expect(empties.length).toBe(3)   // during/after/none 空
    expect(empties[0].textContent).toContain('project.stage.during')
  })

  it('卡片含类型 chip/脉搏句/下一事务行,逾期红字信号', () => {
    const { container } = render(
      <StageLaneView
        projects={[proj({ pulse_ai_status: '尽调材料齐备' })]}
        nextDueByProject={{ p1: { date: '2020-01-01', title: '签约推进' } }}
        onSelect={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('project.type.recruitment')   // 混型同列 chip 区分(§6.3-9)
    expect(text).toContain('招商项目')
    expect(text).toContain('尽调材料齐备')
    expect(text).toContain('homeTodo.nextDue')
    expect(text).toContain('01/01')
    expect(text).toContain('签约推进')
    expect(text).toContain('home.entry.overdue')          // 2020 必然逾期
    expect(container.querySelector('[class*="dueOverdue"]')).toBeTruthy()
  })

  it('无脉搏句/下一事务不渲染对应行', () => {
    const { container } = render(<StageLaneView projects={[proj()]} onSelect={vi.fn()} />)
    expect(container.textContent).not.toContain('homeTodo.nextDue')
    expect(container.querySelector('[class*="pulseLine"]')).toBeNull()
    expect(container.querySelector('[class*="dueLine"]')).toBeNull()
  })

  it('点击卡片触发 onSelect', () => {
    const onSelect = vi.fn()
    const { getByText } = render(<StageLaneView projects={[proj()]} onSelect={onSelect} />)
    fireEvent.click(getByText('招商项目'))
    expect(onSelect).toHaveBeenCalledWith('p1')
  })

  it('无项目渲染空态(无回调时仅文案,无引导钮)', () => {
    const { container } = render(<StageLaneView projects={[]} onSelect={vi.fn()} />)
    expect(container.textContent).toContain('home.noProjects')
    expect(container.querySelector('button')).toBeNull()
  })

  it('无项目且有回调时空态引导新建项目(CodeReview Major #2,§3.7)', () => {
    const onCreateProject = vi.fn()
    const { getByText } = render(
      <StageLaneView projects={[]} onSelect={vi.fn()} onCreateProject={onCreateProject} />)
    fireEvent.click(getByText('home.quick.newProject'))
    expect(onCreateProject).toHaveBeenCalledTimes(1)
  })
})
