/** tests/ProjectCardWall.test.tsx — 3.0 三期 Task 5:首页项目卡片墙 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/home/ProjectCardWall.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/project/affairs/HealthDot.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { ProjectCardWall } from '../src/features/home/ProjectCardWall'
import type { Project } from '../src/features/knowledge/types'

afterEach(() => cleanup())

const proj = { id: 'p1', name: '招聘项目', description: '', type: 'general', stage: 'before', file_count: 0 } as Project

describe('ProjectCardWall (3.0 三期 Task 5)', () => {
  it('renders card per project with stage label fallback and next due date', () => {
    const { container, getByText } = render(
      <ProjectCardWall projects={[proj]}
        healthData={{ p1: { overdue: 0, weekDue: 2, reviewsPending: 0 } }}
        nextDueByProject={{ p1: { date: '2099-01-01', title: '上传BP' } }}
        onSelect={vi.fn()} />)
    expect(getByText('招聘项目')).toBeTruthy()
    const text = container.textContent!
    expect(text).toContain('project.stage.before')   // general 走 STAGE_MAP fallback
    expect(text).toContain('homeTodo.nextDue')
    expect(text).toContain('01/01')
    expect(text).toContain('上传BP')                  // 下一事务名(四期 T9)
    expect(text).not.toContain('home.entry.overdue') // 未来日期不逾期
    expect(container.querySelector('span[class*="orange"]')).toBeTruthy()   // 色点=橙
  })

  it('omits stage pill and next-due when absent', () => {
    const { container, getByText } = render(
      <ProjectCardWall projects={[{ ...proj, stage: 'none' }]} onSelect={vi.fn()} />)
    expect(getByText('招聘项目')).toBeTruthy()
    expect(container.textContent).not.toContain('project.stage')
    expect(container.textContent).not.toContain('homeTodo.nextDue')
  })

  it('clicking card calls onSelect', () => {
    const onSelect = vi.fn()
    const { getByText } = render(<ProjectCardWall projects={[proj]} onSelect={onSelect} />)
    fireEvent.click(getByText('招聘项目'))
    expect(onSelect).toHaveBeenCalledWith('p1')
  })

  it('renders empty hint when no projects', () => {
    const { container } = render(<ProjectCardWall projects={[]} onSelect={vi.fn()} />)
    expect(container.textContent).toContain('home.noProjects')
  })
})

describe('ProjectCardWall ledger entry + due title (3.0 四期 T9)', () => {
  it('shows overdue suffix for past due date', () => {
    const { container } = render(
      <ProjectCardWall projects={[proj]}
        nextDueByProject={{ p1: { date: '2020-01-01', title: '上传BP' } }}
        onSelect={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('01/01')
    expect(text).toContain('home.entry.overdue')     // 2020 → 必然逾期
  })

  it('shows due date only when title absent', () => {
    const { container } = render(
      <ProjectCardWall projects={[proj]}
        nextDueByProject={{ p1: { date: '2099-01-01', title: null } }}
        onSelect={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('01/01')
    expect(text).not.toContain('home.entry.overdue')
    expect(text).not.toContain('·')                  // 无名不拼分隔符
  })

  it('renders latest ledger entry line; hides when absent', () => {
    const { container } = render(
      <ProjectCardWall projects={[proj]}
        latestEntryByProject={{
          p1: { project_id: 'p1', page_key: 'risk_watchlist', entry_date: '2026-09', content: '企业被列入经营异常名录' },
        }}
        onSelect={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('2026-09')
    expect(text).toContain('企业被列入经营异常名录')
    const { container: c2 } = render(<ProjectCardWall projects={[proj]} onSelect={vi.fn()} />)
    expect(c2.textContent).not.toContain('企业被列入经营异常名录')
  })
})
