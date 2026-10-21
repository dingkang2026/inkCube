/** tests/UpcomingCalendar.test.tsx — 3.0 三期 Task 4:首页近期日历 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) =>
      (o ? `${k}:${JSON.stringify(o)}` : k) as string,
    i18n: { language: 'zh-CN' },
  }),
}))
vi.mock('../src/features/home/UpcomingCalendar.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { UpcomingCalendar } from '../src/features/home/UpcomingCalendar'
import type { DueTask } from '../src/features/project/affairs/types'

const mk = (id: string, due_date: string, project_name = 'A 项目'): DueTask => ({
  id, project_id: 'pa', phase: 'before', task_type: 'milestone', title: `任务${id}`,
  description: '', template_id: null, status: 'open', occurrence_key: null, due_date,
  recurrence_rule: null, auto_complete: null, linked_entity_type: null,
  linked_entity_id: null, completed_at: null, completed_by: null,
  created_at: '2026-10-01T00:00:00', project_name,
} as DueTask)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-10T10:00:00'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('UpcomingCalendar (3.0 三期 Task 4)', () => {
  it('groups by day: includes today, excludes overdue and beyond 7 days', () => {
    const { container } = render(
      <UpcomingCalendar
        tasks={[mk('a', '2026-10-10'), mk('b', '2026-10-08'), mk('c', '2026-10-18'), mk('d', '2026-10-12')]}
        onOpenProject={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('10/10')       // 今日含
    expect(text).toContain('10/12')       // 周内含
    expect(text).not.toContain('任务b')   // 逾期不含
    expect(text).not.toContain('任务c')   // 8 天后不含
    expect(text).toContain('affairs.dow') // 周几复用 affairs.dowN
  })

  it('clicking an item navigates to affairs tab', () => {
    const onOpenProject = vi.fn()
    const { getByText } = render(
      <UpcomingCalendar tasks={[mk('a', '2026-10-12')]} onOpenProject={onOpenProject} />)
    fireEvent.click(getByText(/任务a/))
    expect(onOpenProject).toHaveBeenCalledWith('pa', 'affairs')
  })

  it('keeps section with empty state when no tasks in range (Ruling 4 空数据保留)', () => {
    const { container } = render(<UpcomingCalendar tasks={[mk('b', '2026-10-08')]} onOpenProject={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('homeTodo.calendarTitle')
    expect(text).toContain('homeTodo.calendarEmpty')
  })
})
