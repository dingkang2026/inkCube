/** tests/SettingsNav.test.tsx — 设置页导航分组渲染（0.3 §4.4 重组 2026-10-19）：
 *  组标题小字分隔、条目平铺组内；空 label 组不渲染标题行；点击条目回调 section key。 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('../src/features/settings/SettingsNav.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { SettingsNav } from '../src/features/settings/SettingsNav'

afterEach(() => cleanup())

const groups = [
  {
    label: '',
    items: [{ key: 'general', label: 'General' }],
  },
  {
    label: 'Models',
    items: [
      { key: 'externalModel', label: 'External Model' },
      { key: 'modelRouting', label: 'Model Routing' },
    ],
  },
]

describe('SettingsNav groups (0.3 §4.4)', () => {
  it('renders group labels and items in order', () => {
    render(<SettingsNav active="general" onChange={vi.fn()} groups={groups} />)

    expect(screen.getByText('Models')).toBeTruthy()
    expect(screen.getByText('General')).toBeTruthy()
    expect(screen.getByText('External Model')).toBeTruthy()
    expect(screen.getByText('Model Routing')).toBeTruthy()
  })

  it('empty-label group renders no heading row', () => {
    const { container } = render(<SettingsNav active="general" onChange={vi.fn()} groups={groups} />)

    // 首组无标题：仅「Models」一个组标题
    const headings = container.querySelectorAll('[class*="groupLabel"]')
    expect(headings).toHaveLength(1)
    expect(headings[0].textContent).toBe('Models')
  })

  it('clicking an item invokes onChange with the section key and marks active', () => {
    const onChange = vi.fn()
    const { container, rerender } = render(
      <SettingsNav active="general" onChange={onChange} groups={groups} />)

    fireEvent.click(screen.getByText('Model Routing'))
    expect(onChange).toHaveBeenCalledWith('modelRouting')

    rerender(<SettingsNav active="modelRouting" onChange={onChange} groups={groups} />)
    const activeBtn = [...container.querySelectorAll('button')].find(
      (b) => b.textContent === 'Model Routing' && b.className.includes('active'),
    )
    expect(activeBtn).toBeTruthy()
  })

  it('renders status dots only on flagged items with translated title (0.3 §4.4 服务状态点)', () => {
    const groupsWithStatus = [
      {
        label: 'Basics',
        items: [
          { key: 'externalModel', label: 'External Model', missing: true as const, missingTitle: 'Required setup incomplete' },
          { key: 'internalModel', label: 'Internal Model' },
          { key: 'general', label: 'General' },
        ],
      },
    ]
    const { container } = render(
      <SettingsNav active="externalModel" onChange={vi.fn()} groups={groupsWithStatus} />,
    )

    const dots = container.querySelectorAll('[class*="statusDot"]')
    expect(dots).toHaveLength(1)
    expect(dots[0].getAttribute('title')).toBe('Required setup incomplete')
  })

  it('renders no status dot when items carry no status (默认干净导航)', () => {
    const { container } = render(<SettingsNav active="general" onChange={vi.fn()} groups={groups} />)
    expect(container.querySelectorAll('[class*="statusDot"]')).toHaveLength(0)
  })
})
