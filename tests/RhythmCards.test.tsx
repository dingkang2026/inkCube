/** tests/RhythmCards.test.tsx — 3.0 二期 Task 8:聚合/期次横排/空态/回调 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/affairs/RhythmCards.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { RhythmCards } from '../src/features/project/affairs/RhythmCards'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'after', task_type: 'recurring',
  title: '企业月报', description: '', template_id: 'tpl_monthly', status: 'open',
  occurrence_key: '2026-10', due_date: '2026-10-15',
  recurrence_rule: { freq: 'monthly', due_day: 15 }, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const base = { onOpenDrawer: vi.fn(), onEditRhythm: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  // 固定时钟:occErr 逾期断言不得依赖真实系统时间(同 PhaseBoard 用例 2 加固口径)
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-10T12:00:00'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('RhythmCards', () => {
  it('groups by template_id and renders one card per rhythm', () => {
    const tasks = [
      task({ id: 'a1', occurrence_key: '2026-09', status: 'done' }),
      task({ id: 'a2', occurrence_key: '2026-10' }),
      task({ id: 'b1', title: '企业季报', template_id: 'tpl_q', occurrence_key: '2026-Q4',
        recurrence_rule: { freq: 'quarterly', due_day: 'end' } }),
    ]
    const { container } = render(<RhythmCards {...base} tasks={tasks} />)
    expect(container.querySelectorAll('.card').length).toBe(2)
    expect(container.querySelectorAll('.occCell').length).toBe(3)   // 2 期 + 1 期
    expect(container.textContent).toContain('affairs.freqMonthly')
    expect(container.textContent).toContain('affairs.freqQuarterly')
  })

  it('shows empty state without recurring tasks', () => {
    const { container } = render(<RhythmCards {...base} tasks={[task({ recurrence_rule: null, task_type: 'material' })]} />)
    expect(container.textContent).toContain('affairs.rhythmEmpty')
    expect(container.querySelector('.card')).toBeNull()
  })

  it('fires onOpenDrawer per occurrence and onEditRhythm with latest', () => {
    const tasks = [
      task({ id: 'a1', occurrence_key: '2026-09', status: 'done' }),
      task({ id: 'a2', occurrence_key: '2026-10' }),
    ]
    const { container } = render(<RhythmCards {...base} tasks={tasks} />)
    const cells = container.querySelectorAll('.occCell')
    fireEvent.click(cells[0])
    expect(base.onOpenDrawer).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1' }))
    fireEvent.click(container.querySelector('.editBtn')!)
    expect(base.onEditRhythm).toHaveBeenCalledWith(expect.objectContaining({ id: 'a2' }))  // 最新期
  })

  it('marks overdue occurrence with error class', () => {
    const { container } = render(
      <RhythmCards {...base} tasks={[task({ id: 'x1', occurrence_key: '2026-09', due_date: '2026-09-15', status: 'open' })]} />
    )
    expect(container.querySelector('.occErr')).toBeTruthy()
  })

  it('dims pending_activation occurrence instead of open blue (铺入侧非当前期实例)', () => {
    const { container } = render(
      <RhythmCards {...base} tasks={[task({ id: 'px', status: 'pending_activation', occurrence_key: null, due_date: null })]} />
    )
    expect(container.querySelector('.occPending')).toBeTruthy()
    expect(container.querySelector('.occOpen')).toBeNull()
  })

  it('orders mixed-format occurrence keys chronologically in the strip', () => {
    // 换档混排(评审 M-2):字典序会把 Q1 排在 07 后,跨格式 rank 才是时间序
    const tasks = [
      task({ id: 'm6', occurrence_key: '2026-06' }),
      task({ id: 'q1', occurrence_key: '2026-Q1' }),
      task({ id: 'm7', occurrence_key: '2026-07' }),
    ]
    const { container } = render(<RhythmCards {...base} tasks={tasks} />)
    // 月度格文本带 i18n 后缀(mock t 返回键名),取前两字符比对期次序
    const keys = Array.from(container.querySelectorAll('.occCell .occKey'))
      .map((el) => el.textContent!.slice(0, 2))
    expect(keys).toEqual(['Q1', '06', '07'])
  })
})
