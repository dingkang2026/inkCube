/** tests/QuickActions.test.tsx — 0.5 E1 T4:首页快捷动作(新建任务/新建项目,动作化替代导航卡) */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k as string }),
}))
vi.mock('../src/features/home/QuickActions.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { QuickActions } from '../src/features/home/QuickActions'

afterEach(cleanup)

describe('QuickActions (0.5 E1 T4)', () => {
  it('renders two action cards with i18n copy', () => {
    const { container } = render(
      <QuickActions onCreateTask={vi.fn()} onCreateProject={vi.fn()} />,
    )
    const text = container.textContent!
    expect(text).toContain('home.quick.newTask')
    expect(text).toContain('home.quick.newTaskDesc')
    expect(text).toContain('home.quick.newProject')
    expect(text).toContain('home.quick.newProjectDesc')
  })

  it('clicks trigger respective callbacks', () => {
    const onCreateTask = vi.fn()
    const onCreateProject = vi.fn()
    const { getByText } = render(
      <QuickActions onCreateTask={onCreateTask} onCreateProject={onCreateProject} />,
    )
    fireEvent.click(getByText('home.quick.newTask'))
    expect(onCreateTask).toHaveBeenCalledTimes(1)
    fireEvent.click(getByText('home.quick.newProject'))
    expect(onCreateProject).toHaveBeenCalledTimes(1)
  })
})
