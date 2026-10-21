/** tests/ScheduleSideSummary.test.tsx — 任务侧栏定时订阅摘要行（2.11 E3 T6）
 *  覆盖：无订阅渲染 hintline（可点击开管理台）；有订阅渲染摘要行
 *  「订阅 N · 新产出 M · 异常 K」；异常>0 红显 class；点击回调 onOpenAdmin。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ScheduleSideSummary } from '../src/features/schedule/ScheduleSideSummary'

const activityState = vi.hoisted(() => ({ isActive: false }))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, p?: Record<string, unknown>) =>
      p ? `${k} n=${p['n']} unread=${p['unread']} abnormal=${p['abnormal']}` : k,
  }),
}))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ isActive: activityState.isActive }),
}))

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockResolvedValue({ totals: { n: 0, total: 0, abnormal: 0 } })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
  activityState.isActive = false
})

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const renderRow = (onOpenAdmin = vi.fn()) => {
  render(<ScheduleSideSummary onOpenAdmin={onOpenAdmin} />)
  return onOpenAdmin
}

describe('ScheduleSideSummary', () => {
  it('no subscriptions: renders hintline, not summary row', async () => {
    invokeMock.mockResolvedValue({ totals: { n: 0, total: 0, abnormal: 0 } })
    renderRow()
    await waitFor(() => expect(screen.getByText('schedule.hintCreate')).toBeTruthy())
    expect(screen.queryByText(/schedule\.summaryLine/)).toBeNull()
  })

  it('with subscriptions: renders summary line with counts', async () => {
    invokeMock.mockResolvedValue({ totals: { n: 3, total: 2, abnormal: 1 } })
    renderRow()
    await waitFor(() =>
      expect(screen.getByText('schedule.summaryLine n=3 unread=2 abnormal=1')).toBeTruthy())
  })

  it('abnormal > 0: abnormal accent class applied', async () => {
    invokeMock.mockResolvedValue({ totals: { n: 3, total: 0, abnormal: 2 } })
    const { container } = render(<ScheduleSideSummary onOpenAdmin={() => {}} />)
    await waitFor(() => expect(screen.getByText(/schedule\.summaryLine/)).toBeTruthy())
    expect(container.querySelector('[class*="abnormal"]')).toBeTruthy()
  })

  it('click opens admin view via onOpenAdmin', async () => {
    invokeMock.mockResolvedValue({ totals: { n: 1, total: 0, abnormal: 0 } })
    const onOpenAdmin = renderRow()
    await waitFor(() => expect(screen.getByText(/schedule\.summaryLine/)).toBeTruthy())
    fireEvent.click(screen.getByText(/schedule\.summaryLine/))
    expect(onOpenAdmin).toHaveBeenCalledTimes(1)
  })
})
