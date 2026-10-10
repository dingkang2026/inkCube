/** tests/ProjectList.test.tsx — 3.0 二期 Task 13:创建确认态(裁决 8) */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  // 带参序列化(Task 9 口径):插值断言(如确认态 seeded count '24')才可达
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ byProject: new Map(), items: [] }),
}))
vi.mock('../src/features/knowledge/ProjectList.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { ProjectList } from '../src/features/knowledge/ProjectList'

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve() })
  }
}

const base = { projects: [], selectedProject: null, onSelect: vi.fn(), onDelete: vi.fn() }

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

const openAndFill = (getByPlaceholderText: (s: string) => HTMLElement, getByText: (s: string | RegExp) => HTMLElement, name = '新园区') => {
  fireEvent.click(getByText('knowledge.newProject'))
  fireEvent.change(getByPlaceholderText('knowledge.projectName'), { target: { value: name } })
}

describe('ProjectList create confirm (裁决 8)', () => {
  it('switches to confirm state with seeded count after create resolves', async () => {
    const onCreate = vi.fn().mockResolvedValue({ id: 'p9', seeded_tasks_count: 24 })
    const { getByPlaceholderText, getByText, container, queryByPlaceholderText } = render(
      <ProjectList {...base} onCreate={onCreate} />)
    openAndFill(getByPlaceholderText, getByText)
    fireEvent.click(getByText('knowledge.create'))
    await flush()
    const text = container.textContent!
    expect(text).toContain('knowledge.wizardConfirmDesc')
    expect(text).toContain('24')
    expect(queryByPlaceholderText('knowledge.projectName')).toBeNull()   // 表单已切走,modal 未关
  })

  it('adjust button dispatches navigate-project-tab with affairs tab and closes', async () => {
    const onCreate = vi.fn().mockResolvedValue({ id: 'p9', seeded_tasks_count: 24 })
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const { getByPlaceholderText, getByText, queryByText } = render(
      <ProjectList {...base} onCreate={onCreate} />)
    openAndFill(getByPlaceholderText, getByText)
    fireEvent.click(getByText('knowledge.create'))
    await flush()
    dispatchSpy.mockClear()
    fireEvent.click(getByText('knowledge.wizardConfirmAdjust'))
    const ev = dispatchSpy.mock.calls.map(([e]) => e as CustomEvent)
      .find((e) => e.type === 'app:navigate-project-tab')
    expect(ev).toBeTruthy()
    expect((ev as CustomEvent).detail).toEqual({ projectId: 'p9', tab: 'affairs' })
    expect(queryByText('knowledge.wizardConfirmAdjust')).toBeNull()      // modal 已关
  })

  it('falls back to legacy close when onCreate resolves without result', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined)
    const { getByPlaceholderText, getByText, queryByPlaceholderText } = render(
      <ProjectList {...base} onCreate={onCreate} />)
    openAndFill(getByPlaceholderText, getByText)
    fireEvent.click(getByText('knowledge.create'))
    await flush()
    expect(queryByPlaceholderText('knowledge.projectName')).toBeNull()   // 直接关,原行为
  })

  it('discards stale confirm state if modal closed while create is in-flight (M1)', async () => {
    let resolveCreate!: (v: { id: string; seeded_tasks_count: number }) => void
    const onCreate = vi.fn().mockImplementation(
      () => new Promise<{ id: string; seeded_tasks_count: number }>((res) => { resolveCreate = res }))
    const { getByPlaceholderText, getByText, queryByText } = render(
      <ProjectList {...base} onCreate={onCreate} />)
    openAndFill(getByPlaceholderText, getByText)
    fireEvent.click(getByText('knowledge.create'))
    // 在途期间 Escape 关闭 modal(resetAndClose)
    fireEvent.keyDown(getByPlaceholderText('knowledge.projectName'), { key: 'Escape' })
    await act(async () => { resolveCreate({ id: 'p9', seeded_tasks_count: 24 }) })
    await flush()
    // 重开 modal 必须是表单态,不得残留上一个项目的确认态(陈旧 projectId 可致误导航)
    fireEvent.click(getByText('knowledge.newProject'))
    expect(queryByText('knowledge.wizardConfirmTitle')).toBeNull()
    expect(getByPlaceholderText('knowledge.projectName')).toBeTruthy()
  })
})

describe('ProjectList health dot (3.0 三期 Task 3)', () => {
  const proj = { id: 'p1', name: 'P1', file_count: 0, description: '', type: 'general', stage: 'before' }

  it('renders health dot with level class and i18n title when healthData provided', () => {
    const { container } = render(
      <ProjectList {...base} projects={[proj]}
        healthData={{ p1: { overdue: 2, weekDue: 1, reviewsPending: 0 } }} />)
    const dot = container.querySelector('span[class*="red"]')   // css mock: 类名=键名
    expect(dot).toBeTruthy()
    expect(dot!.getAttribute('title')).toContain('homeTodo.healthRed')
  })

  it('omits health dot without healthData (未接线/降级态)', () => {
    const { container } = render(<ProjectList {...base} projects={[proj]} />)
    expect(container.querySelector('span[title]')).toBeNull()
  })
})
