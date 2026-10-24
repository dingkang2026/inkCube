/** tests/WikiLedgerTimeline.test.tsx — 台账页时间轴阅读形态（V2 E3 §3.5）
 *  条目日期分组渲染 / 空+失败回落（onFallback）/ 加载更多；invoke 打桩同 WikiTab 测试惯例。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { WikiLedgerTimeline } from '../src/features/project/tabs/WikiLedgerTimeline'

const invokeMock = vi.fn()
beforeEach(() => {
  vi.clearAllMocks()
  ;(window as any).electronAPI = { invoke: invokeMock }
})
afterEach(() => cleanup())

const entries = [
  { id: 'e1', page_key: 'policy_ledger', entry_date: '2026-10', entry_type: 'milestone', content: '设备全部进场' },
  { id: 'e2', page_key: 'policy_ledger', entry_date: '2026-09', entry_type: 'risk', content: '列入经营异常名录' },
]

describe('WikiLedgerTimeline (V2 E3)', () => {
  it('renders date-grouped entries with typed badges', async () => {
    invokeMock.mockResolvedValue({ entries, has_more: false })
    const onFallback = vi.fn()
    const { container } = render(
      <WikiLedgerTimeline projectId="p1" pageKey="policy_ledger" onFallback={onFallback} />,
    )
    await waitFor(() => expect(container.querySelector('.timelineGroup')).not.toBeNull())
    const text = container.textContent!
    expect(text).toContain('2026-10')
    expect(text).toContain('设备全部进场')
    expect(text).toContain('列入经营异常名录')
    // 未知回显枚举兜底不覆盖已知键:已知类型走 i18n 键
    expect(text).toContain('project.wiki.timeline.entryType.risk')
    // 按页过滤参数直达（T1 page_key）
    expect(invokeMock).toHaveBeenCalledWith('wiki:listLedgerEntries', {
      projectId: 'p1', pageKey: 'policy_ledger', limit: 20,
    })
    expect(onFallback).not.toHaveBeenCalled()
  })

  it('falls back to raw text when no entries', async () => {
    invokeMock.mockResolvedValue({ entries: [], has_more: false })
    const onFallback = vi.fn()
    render(<WikiLedgerTimeline projectId="p1" pageKey="policy_ledger" onFallback={onFallback} />)
    await waitFor(() => expect(onFallback).toHaveBeenCalledTimes(1))
  })

  it('falls back when the channel errors', async () => {
    invokeMock.mockRejectedValue(new Error('boom'))
    const onFallback = vi.fn()
    render(<WikiLedgerTimeline projectId="p1" pageKey="policy_ledger" onFallback={onFallback} />)
    await waitFor(() => expect(onFallback).toHaveBeenCalledTimes(1))
  })

  it('offers load-more and refetches with increased limit', async () => {
    invokeMock.mockResolvedValue({ entries, has_more: true })
    const { container, getByText } = render(
      <WikiLedgerTimeline projectId="p1" pageKey="policy_ledger" onFallback={vi.fn()} />,
    )
    await waitFor(() => expect(container.querySelector('.timelineGroup')).not.toBeNull())
    fireEvent.click(getByText('common.loadMore'))
    await waitFor(() =>
      expect(invokeMock).toHaveBeenLastCalledWith('wiki:listLedgerEntries', {
        projectId: 'p1', pageKey: 'policy_ledger', limit: 40,
      }))
  })

  it('keeps shown entries and does not fall back when load-more fails (CodeReview Major #1 regression)', async () => {
    invokeMock.mockResolvedValueOnce({ entries, has_more: true }).mockRejectedValueOnce(new Error('boom'))
    const onFallback = vi.fn()
    const { container, getByText } = render(
      <WikiLedgerTimeline projectId="p1" pageKey="policy_ledger" onFallback={onFallback} />,
    )
    await waitFor(() => expect(container.querySelector('.timelineGroup')).not.toBeNull())
    fireEvent.click(getByText('common.loadMore'))
    // 失败内联提示出现，已展示条目保留，不触发回落（误回落会静默替换时间轴）
    await waitFor(() => expect(container.querySelector('.timelineLoadFailed')).not.toBeNull())
    expect(container.textContent!).toContain('设备全部进场')
    expect(onFallback).not.toHaveBeenCalled()
  })
})
