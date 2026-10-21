/** tests/NavTabs.test.tsx — 铃铛 + 未读徽章（2.11 §6.1）：>99 显示 99+，
 *  数据取自 ActivityContext.status.notifications.unread，点击打开通知中心。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k as string }),
}))

const activityMocks = vi.hoisted(() => ({
  unread: { current: [] as unknown[] },
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({
    status: { notifications: { unread: activityMocks.unread.current } },
    summary: { running_tasks: 0 },
    activeProjectIds: new Set<string>(),
  }),
}))
const cssMock = vi.hoisted(() => ({ default: new Proxy({}, { get: (_t: unknown, k: unknown) => String(k) }) }))
vi.mock('../src/components/layout/NavTabs.module.css', () => cssMock)

import { render, fireEvent, cleanup } from '@testing-library/react'
import { NavTabs } from '../src/components/layout/NavTabs'
import type { NotificationItem } from '../src/features/activity/types'

const mk = (id: number): NotificationItem => ({
  id, entity_type: 'file', entity_id: `f${id}`, project_id: 'p1', kind: 'completed',
  entity_title: 'x', detail: null, read_at: null, created_at: '',
})

const base = { activeNav: 'home', onNavChange: vi.fn(), onOpenNotifications: vi.fn() }

beforeEach(() => {
  activityMocks.unread.current = []
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
})

describe('NavTabs notification bell (2.11 §6.1)', () => {
  it('无未读时不渲染徽章；点击铃铛打开通知中心', () => {
    const { getByTitle, container } = render(<NavTabs {...base} />)
    expect(container.textContent).not.toContain('99+')
    fireEvent.click(getByTitle('nav.notifications'))
    expect(base.onOpenNotifications).toHaveBeenCalledTimes(1)
  })

  it('未读数显示徽章；>99 显示 99+ 而非全量数字', () => {
    activityMocks.unread.current = Array.from({ length: 120 }, (_, i) => mk(i + 1))
    const { getByTitle, container } = render(<NavTabs {...base} />)
    expect(container.textContent).toContain('99+')
    expect(container.textContent).not.toContain('120')
    fireEvent.click(getByTitle('nav.notifications'))
    expect(base.onOpenNotifications).toHaveBeenCalledTimes(1)
  })
})
