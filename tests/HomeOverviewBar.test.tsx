/** tests/HomeOverviewBar.test.tsx — 0.5 E1 T3:首页概览统计卡(0.5 §4.2 打样修订 2026-10-18:
 *  四张卡=标签+大数字,点击滚动锚点;空数据显示 0 而非隐藏,Ruling 4 2026-10-18) */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/home/HomeOverviewBar.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { HomeOverviewBar } from '../src/features/home/HomeOverviewBar'

afterEach(cleanup)

describe('HomeOverviewBar (0.5 E1 T3)', () => {
  it('renders four stat cards with labels and numbers', () => {
    const { container } = render(
      <HomeOverviewBar projectCount={3} overdue={2} dueToday={1} activeCount={4} onJump={vi.fn()} />,
    )
    const text = container.textContent!
    expect(text).toContain('home.stat.projects')
    expect(text).toContain('home.stat.overdue')
    expect(text).toContain('home.stat.dueToday')
    expect(text).toContain('home.stat.active')
    // 大数字与标签分离渲染,按卡序核对数值
    const nums = Array.from(container.querySelectorAll('.num')).map((n) => n.textContent)
    expect(nums).toEqual(['3', '2', '1', '4'])
  })

  it('clicks map to anchors: projects→wall, overdue/dueToday→todo, active→activity', () => {
    const onJump = vi.fn()
    const { container } = render(
      <HomeOverviewBar projectCount={3} overdue={2} dueToday={1} activeCount={4} onJump={onJump} />,
    )
    fireEvent.click(container.querySelector('[data-anchor="wall"]')!)
    fireEvent.click(container.querySelector('[data-anchor="todo"]')!)
    fireEvent.click(container.querySelector('[data-anchor="activity"]')!)
    expect(onJump).toHaveBeenNthCalledWith(1, 'wall')
    expect(onJump).toHaveBeenNthCalledWith(2, 'todo')
    expect(onJump).toHaveBeenNthCalledWith(3, 'activity')
  })

  it('zero values still render (Ruling 4 空数据保留)', () => {
    const { container } = render(
      <HomeOverviewBar projectCount={0} overdue={0} dueToday={0} activeCount={0} onJump={vi.fn()} />,
    )
    const text = container.textContent!
    expect(text).toContain('home.stat.projects')
    expect(text).toContain('home.stat.overdue')
    expect(text).toContain('home.stat.dueToday')
    expect(text).toContain('home.stat.active')
    const nums = Array.from(container.querySelectorAll('.num')).map((n) => n.textContent)
    expect(nums).toEqual(['0', '0', '0', '0'])
  })
})
