/** tests/CalendarView.test.tsx — 3.0 二期 Task 9:网格/任务条/点格预填/月切换 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  // 带参调用序列化参数(calTitle 月切换断言依赖参数差);无参返回键名
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) => (o ? `${k}:${JSON.stringify(o)}` : k),
  }),
}))
vi.mock('../src/features/project/affairs/CalendarView.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { CalendarView } from '../src/features/project/affairs/CalendarView'
import type { PhaseTask } from '../src/features/project/affairs/types'

// 固定时钟当月的 15 日:42 格/today 高亮/落格断言不得依赖真实系统时间(Task 7/8 同款口径)
const month15 = '2026-10-15'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const base = { onOpenDrawer: vi.fn(), onCreateTask: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-10T12:00:00'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('CalendarView', () => {
  it('renders 42-cell grid with 7 dow headers and today highlight', () => {
    const { container } = render(<CalendarView {...base} tasks={[]} />)
    expect(container.querySelector('.calGrid')).toBeTruthy()
    expect(container.querySelectorAll('.dow').length).toBe(7)
    expect(container.querySelectorAll('.cell').length).toBe(42)
    expect(container.querySelector('.cellToday')).toBeTruthy()
  })

  it('renders due task in its day cell and opens drawer on click', () => {
    const { container, getByText } = render(
      <CalendarView {...base} tasks={[task({ due_date: month15 })]} />
    )
    // 锁落格正确性:任务条必须落在 15 日格内(分组键错如误用 created_at 时立拏)
    const cell15 = [...container.querySelectorAll('.cell')]
      .find((c) => c.querySelector('.dayNum')?.textContent === '15')
    expect(cell15?.textContent).toContain('上传BP')
    fireEvent.click(getByText('上传BP').closest('button')!)
    expect(base.onOpenDrawer).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }))
  })

  it('fires onCreateTask with the clicked date on empty cell', () => {
    const { container } = render(<CalendarView {...base} tasks={[]} />)
    fireEvent.click(container.querySelectorAll('.cell')[10])
    // fake 时钟 2026-10-10:当月首日周四,start=09-27,cell[10]=10-07(精确锁周起点/42 格偏移回归)
    expect(base.onCreateTask).toHaveBeenCalledWith('2026-10-07')
  })

  it('caps entries per cell at 3 with +N overflow, done entries dimmed', () => {
    // done 实例置于前三可显位:计划数据排末位会被 slice(0,3) 截掉,evDone 断言必失败
    const tasks = [
      task({ id: 'd1', title: '已完成项', due_date: month15, status: 'done' }),
      ...[1, 2, 3, 4].map((i) => task({ id: `m${i}`, title: `任务${i}`, due_date: month15 })),
    ]
    const { container } = render(<CalendarView {...base} tasks={tasks} />)
    expect(container.textContent!).toContain('+2')     // 5 条 → 显 3 + +2
    expect(container.querySelector('.evDone')).toBeTruthy()
  })

  it('done 任务条 hover title 带完成时间;存量缺 completed_at 回落纯标题(2026-10-28 补齐)', () => {
    const { container } = render(
      <CalendarView {...base} tasks={[
        task({ id: 'd1', title: '已完成项', due_date: month15, status: 'done', completed_at: '2026-10-16T09:00:00' }),
        task({ id: 'd2', title: '存量完成', due_date: month15, status: 'done' }),
      ]} />)
    const bars = container.querySelectorAll('.evBtn')
    expect((bars[0] as HTMLElement).title).toBe('已完成项 · affairs.doneAt:{"date":"2026-10-16"}')
    expect((bars[1] as HTMLElement).title).toBe('存量完成')
  })

  it('navigates months and back to today', () => {
    const { container, getByText } = render(<CalendarView {...base} tasks={[]} />)
    const before = container.querySelector('.calTitle')!.textContent
    fireEvent.click(container.querySelector('.navBtn')!)
    expect(container.querySelector('.calTitle')!.textContent).not.toBe(before)
    fireEvent.click(getByText('affairs.calToday'))
    expect(container.querySelector('.calTitle')!.textContent).toBe(before)
  })
})
