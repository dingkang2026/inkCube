/** tests/ReportsTab.test.tsx — #7 谓词心跳化 + 终态边沿补拉回归（0.4 §6.3，顺序 4）
 *  轮询启停由 useActivity('report') 项目级心跳派生（本地列表是挂载快照，存在
 *  陈旧漏判——如停轮期间其他页面触发的生成）；hasGenerating true→false 边沿
 *  补拉 loadReports，终态（done/error）立即上屏。
 *  子视图打桩隔离：仅验证列表态接线；机制本身由 useEdgeRefetch.test 覆盖。
 *  时序约定（同 tests/usePolling.test.ts）：fake timers + flush 排空 microtask。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const activityState = { items: [] as Array<{ project_id?: string }> }

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => activityState,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('../src/features/project/tabs/ReportsTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/features/project/tabs/ReportWizard', () => ({
  ReportWizard: () => null,
}))
vi.mock('../src/features/project/tabs/ReportPreview', () => ({
  ReportPreview: () => null,
}))

import { render, screen, cleanup, act } from '@testing-library/react'
import { ReportsTab } from '../src/features/project/tabs/ReportsTab'

const invokeMock = vi.fn()

const listInvokeCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'reports:listReports').length

/** 排空 microtask（loadReports 与轮询 tick 均为 async，fake timers 不自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  activityState.items = []
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'reports:listReports') {
      return {
        reports: [
          {
            id: 'r1',
            title: 'R1',
            template_id: 'tpl',
            template_name: 'TPL',
            output_format: 'docx',
            status: 'generating',
            progress: 10,
            stage: 'none',
            created_at: '2026-01-01T00:00:00Z',
          },
        ],
      }
    }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('ReportsTab heartbeat-driven polling', () => {
  it('loads list once on mount and stays idle while heartbeat has no active report', async () => {
    render(<ReportsTab projectId="p1" projectType="general" />)
    await flush()
    expect(listInvokeCount()).toBe(1)
    expect(screen.getByText('R1')).toBeTruthy()

    // 无活跃条目：轮询不启动，计数不增
    await flush()
    expect(listInvokeCount()).toBe(1)
  })

  it('starts polling on first tick when heartbeat reports an active report of this project', async () => {
    const { rerender } = render(<ReportsTab projectId="p1" projectType="general" />)
    await flush()
    expect(listInvokeCount()).toBe(1)

    // 心跳出现本项目活跃报告 → 轮询 enabled 翻真立即首跑
    activityState.items = [{ project_id: 'p1' }]
    rerender(<ReportsTab projectId="p1" projectType="general" />)
    await flush()
    expect(listInvokeCount()).toBe(2)
  })

  it('ignores active reports of other projects', async () => {
    const { rerender } = render(<ReportsTab projectId="p1" projectType="general" />)
    await flush()
    expect(listInvokeCount()).toBe(1)

    // 其他项目的报告条目不驱动本页轮询
    activityState.items = [{ project_id: 'other' }]
    rerender(<ReportsTab projectId="p1" projectType="general" />)
    await flush()
    expect(listInvokeCount()).toBe(1)
  })

  it('refetches exactly once on hasGenerating true→false edge (terminal before snapshot sync)', async () => {
    const { rerender } = render(<ReportsTab projectId="p1" projectType="general" />)
    await flush()
    activityState.items = [{ project_id: 'p1' }]
    rerender(<ReportsTab projectId="p1" projectType="general" />)
    await flush()
    expect(listInvokeCount()).toBe(2)

    // 心跳先行翻假（终态已发生、快照同步前）：边沿补拉恰一次
    activityState.items = []
    rerender(<ReportsTab projectId="p1" projectType="general" />)
    expect(listInvokeCount()).toBe(3)

    // 轮询已停，无额外拉取
    await flush()
    expect(listInvokeCount()).toBe(3)
  })
})
