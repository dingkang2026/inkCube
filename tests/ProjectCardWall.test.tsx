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
        healthData={{ p1: { overdue: 0, weekDue: 2, reviewsPending: 0, contradiction: 0, gap: 0 } }}
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

describe('ProjectCardWall 新产出角标 (0.5 E2 T7)', () => {
  it('计数>0 渲染角标（计数文本 + tooltip）', () => {
    const { container, getByText } = render(
      <ProjectCardWall projects={[proj]} newOutputsByProject={{ p1: 3 }} onSelect={vi.fn()} />)
    expect(getByText('3')).toBeTruthy()
    expect(container.querySelector('span[title*="home.newOutputs"]')).toBeTruthy()
  })

  it('无计数/零计数项目不渲染角标', () => {
    const { container } = render(
      <ProjectCardWall projects={[proj]} newOutputsByProject={{}} onSelect={vi.fn()} />)
    expect(container.querySelector('span[title*="home.newOutputs"]')).toBeNull()
    const { container: c2 } = render(
      <ProjectCardWall projects={[proj]} newOutputsByProject={{ p1: 0 }} onSelect={vi.fn()} />)
    expect(c2.querySelector('span[title*="home.newOutputs"]')).toBeNull()
  })
})

describe('ProjectCardWall V2-E4 升级 (阶段序数/脉搏句/矛盾缺口行)', () => {
  it('阶段 pill 附 n/m 序数（STAGE_MAP 序位）', () => {
    const { container } = render(
      <ProjectCardWall projects={[{ ...proj, type: 'investment', stage: 'during' }]} onSelect={vi.fn()} />)
    expect(container.textContent!).toContain('2/3')   // investment during = 第 2/3 阶段
  })

  it('general 类型无阶段集不显示序数', () => {
    const { container } = render(
      <ProjectCardWall projects={[proj]} onSelect={vi.fn()} />)   // general + before
    expect(container.textContent!).toContain('project.stage.before')
    expect(container.textContent!).not.toMatch(/\d\/\d/)
  })

  it('AI 脉搏句非空渲染、空不渲染', () => {
    const { container } = render(
      <ProjectCardWall projects={[{ ...proj, pulse_ai_status: '企业月报已解析，发现 3 条新风险' }]} onSelect={vi.fn()} />)
    expect(container.textContent!).toContain('企业月报已解析，发现 3 条新风险')
    const { container: c2 } = render(
      <ProjectCardWall projects={[proj]} onSelect={vi.fn()} />)
    expect(c2.textContent!).not.toContain('企业月报已解析')
  })

  it('矛盾/缺口计数行 >0 渲染对应项、全 0 不渲染', () => {
    const { container } = render(
      <ProjectCardWall projects={[proj]}
        healthData={{ p1: { overdue: 0, weekDue: 0, reviewsPending: 3, contradiction: 2, gap: 1 } }}
        onSelect={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('home.wall.contradiction')
    expect(text).toContain('home.wall.gap')
    const { container: c2 } = render(
      <ProjectCardWall projects={[proj]}
        healthData={{ p1: { overdue: 0, weekDue: 0, reviewsPending: 0, contradiction: 0, gap: 0 } }}
        onSelect={vi.fn()} />)
    expect(c2.textContent!).not.toContain('home.wall.contradiction')
    expect(c2.textContent!).not.toContain('home.wall.gap')
  })
})
