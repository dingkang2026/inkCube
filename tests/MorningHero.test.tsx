/**
 * tests/MorningHero.test.tsx — 首页晨会英雄区单测（0.6 §3.1① V2-E1b）。
 *
 * 覆盖：问候时段三分支（morning/afternoon/evening）；日期渲染（zh-CN 长日期）；
 * 晨报句规则拼装插值（tracking/intel/todo）；KPI 四条行内链（home.stat.* 标签 +
 * 数字）与 onJump 锚点分发（wall/todo/activity）；逾期 >0 语义红（kpiNumWarn）；
 * 全零渲染 0（Ruling 4 空数据保留精神——英雄区恒渲染）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) =>
      (o ? `${k}:${JSON.stringify(o)}` : k) as string,
    i18n: { language: 'zh-CN' },
  }),
}))
const cssMock = vi.hoisted(() => ({ default: new Proxy({}, { get: (_t: unknown, k: unknown) => String(k) }) }))
vi.mock('../src/features/home/MorningHero.module.css', () => cssMock)

import { render, fireEvent, cleanup } from '@testing-library/react'
import { MorningHero } from '../src/features/home/MorningHero'

const base = {
  trackingCount: 2,
  unreadIntel: 3,
  projectCount: 5,
  overdue: 1,
  dueToday: 4,
  activeCount: 6,
  onJump: vi.fn(),
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('MorningHero（0.6 §3.1① 晨会英雄区）', () => {
  it('上午 → greetingMorning + zh-CN 长日期渲染', () => {
    vi.setSystemTime(new Date('2026-10-24T09:30:00'))
    const { container } = render(<MorningHero {...base} />)
    expect(container.textContent).toContain('home.hero.greetingMorning')
    expect(container.textContent).toContain('10月24日星期六')
  })

  it('下午/晚上 → greetingAfternoon / greetingEvening 分支', () => {
    vi.setSystemTime(new Date('2026-10-24T15:00:00'))
    const { container, unmount } = render(<MorningHero {...base} />)
    expect(container.textContent).toContain('home.hero.greetingAfternoon')
    unmount()
    vi.setSystemTime(new Date('2026-10-24T20:30:00'))
    const r2 = render(<MorningHero {...base} />)
    expect(r2.container.textContent).toContain('home.hero.greetingEvening')
  })

  it('晨报句规则拼装：tracking/intel/todo 三计数插值', () => {
    vi.setSystemTime(new Date('2026-10-24T09:30:00'))
    const { container } = render(<MorningHero {...base} />)
    // todo = dueToday（今日到期口径）
    expect(container.textContent).toContain(
      'home.hero.briefing:{"tracking":2,"intel":3,"todo":4}',
    )
  })

  it('KPI 四条行内链：stat 标签 + 数字，onJump 分发 wall/todo/todo/activity', () => {
    vi.setSystemTime(new Date('2026-10-24T09:30:00'))
    const onJump = vi.fn()
    const { container, getByText } = render(<MorningHero {...base} onJump={onJump} />)
    const nums = Array.from(container.querySelectorAll('.kpiNum')).map((n) => n.textContent)
    expect(nums).toEqual(['5', '1', '4', '6'])
    expect(container.textContent).toContain('home.stat.projects')
    expect(container.textContent).toContain('home.stat.overdue')
    expect(container.textContent).toContain('home.stat.dueToday')
    expect(container.textContent).toContain('home.stat.active')
    fireEvent.click(getByText('home.stat.projects').closest('button')!)
    expect(onJump).toHaveBeenNthCalledWith(1, 'wall')
    fireEvent.click(getByText('home.stat.overdue').closest('button')!)
    expect(onJump).toHaveBeenNthCalledWith(2, 'todo')
    fireEvent.click(getByText('home.stat.active').closest('button')!)
    expect(onJump).toHaveBeenNthCalledWith(3, 'activity')
  })

  it('逾期 >0 → kpiNumWarn 语义红；全零照常渲染 0', () => {
    vi.setSystemTime(new Date('2026-10-24T09:30:00'))
    const { container } = render(<MorningHero {...base} />)
    const warn = container.querySelector('.kpiNumWarn') as HTMLElement
    expect(warn).toBeTruthy()
    expect(warn.textContent).toBe('1')
    const r2 = render(
      <MorningHero {...base} trackingCount={0} unreadIntel={0} projectCount={0} overdue={0} dueToday={0} activeCount={0} />,
    )
    const nums = Array.from(r2.container.querySelectorAll('.kpiNum')).map((n) => n.textContent)
    expect(nums).toEqual(['0', '0', '0', '0'])
  })
})
