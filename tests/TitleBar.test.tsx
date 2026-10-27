/** tests/TitleBar.test.tsx — 通知铃铛 + 未读角标（2.11 §6.1）：>99 显示 99+，
 *  数据取自 ActivityContext.status.notifications.unread，点击打开通知中心。
 *  2026-10-26 铃铛自 NavTabs 迁入 TitleBar 右上角（原 tests/NavTabs.test.tsx）。
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
vi.mock('../src/components/layout/TitleBar.module.css', () => cssMock)

import { render, fireEvent, cleanup } from '@testing-library/react'
import { TitleBar } from '../src/components/layout/TitleBar'
import type { NotificationItem } from '../src/features/activity/types'

const mk = (id: number): NotificationItem => ({
  id, entity_type: 'file', entity_id: `f${id}`, project_id: 'p1', kind: 'completed',
  entity_title: 'x', detail: null, read_at: null, created_at: '',
})

const base = {
  onOpenSettings: vi.fn(),
  onToggleSidebar: vi.fn(),
  onShowAbout: vi.fn(),
  onOpenNotifications: vi.fn(),
}

beforeEach(() => {
  activityMocks.unread.current = []
  // TitleBar useEffect：window:maximized/unmaximized 订阅 + Ctrl 快捷键监听
  ;(window as { electronAPI?: unknown }).electronAPI = {
    on: vi.fn(() => vi.fn()),
    invoke: vi.fn(async () => ({})),
    send: vi.fn(),
  }
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
  delete (window as { electronAPI?: unknown }).electronAPI
})

describe('TitleBar notification bell (2.11 §6.1)', () => {
  it('无未读时不渲染徽章；点击铃铛打开通知中心', () => {
    const { getByTitle, container } = render(<TitleBar {...base} />)
    expect(container.textContent).not.toContain('99+')
    fireEvent.click(getByTitle('nav.notifications'))
    expect(base.onOpenNotifications).toHaveBeenCalledTimes(1)
  })

  it('未读数显示徽章；>99 显示 99+ 而非全量数字', () => {
    activityMocks.unread.current = Array.from({ length: 120 }, (_, i) => mk(i + 1))
    const { getByTitle, container } = render(<TitleBar {...base} />)
    expect(container.textContent).toContain('99+')
    expect(container.textContent).not.toContain('120')
    fireEvent.click(getByTitle('nav.notifications'))
    expect(base.onOpenNotifications).toHaveBeenCalledTimes(1)
  })
})
