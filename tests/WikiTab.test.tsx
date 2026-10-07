/** tests/WikiTab.test.tsx — 状态条「查看详情」入口回归（2.6 §10 修订）
 *  编译中 / completed（含无错误）/ failed / stopped 四种状态条均带查看详情，
 *  收起的明细面板经此重新展开；面板默认收起（不自动弹出）；
 *  展开后 onCompileDetailChange 上报 true（父级 #4 轮询据此提频至 2s，0.4 §8.1）。
 *  Tab 内容组件全部打桩，仅验证状态条与明细面板接线。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? key,
  }),
}))

vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/features/readiness/useReadiness', () => ({
  useFeatureGuard: () => async (_key: string, fn: () => void) => {
    fn()
  },
}))

vi.mock('../src/features/project/tabs/WikiHomePage', () => ({ WikiHomePage: () => null }))
vi.mock('../src/features/project/tabs/WikiPageReader', () => ({ WikiPageReader: () => null }))
vi.mock('../src/features/project/tabs/WikiPageEditor', () => ({ WikiPageEditor: () => null }))
vi.mock('../src/features/project/tabs/WikiChangeLog', () => ({ WikiChangeLog: () => null }))
vi.mock('../src/features/project/tabs/WikiSearchView', () => ({ WikiSearchView: () => null }))
vi.mock('../src/features/project/tabs/WikiGraphView', () => ({ WikiGraphView: () => null }))
vi.mock('../src/features/project/tabs/WikiLintView', () => ({ WikiLintView: () => null }))
vi.mock('../src/features/project/tabs/CompilePanel', () => ({
  CompilePanel: () => <div data-testid="compile-panel" />,
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { WikiTab } from '../src/features/project/tabs/WikiTab'

const invokeMock = vi.fn()

/** 排空 microtask（loadPages 等异步拉取，避免 loading 态吞掉状态条） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const makeTask = (status: string) => ({
  id: 't1',
  status,
  stage: null,
  pages_total: 3,
  pages_completed: 1,
  pages_failed: status === 'failed' ? 3 : 0,
  error_message: status === 'failed' ? 'boom' : null,
  created_at: '',
  completed_at: null,
})

const renderTab = (status: string, onCompileDetailChange?: (open: boolean) => void) =>
  render(
    <WikiTab
      projectId="p1"
      projectType="general"
      compileData={{ task: makeTask(status), ingest_summaries: [], is_resumed: false }}
      onCompileDetailChange={onCompileDetailChange}
    />,
  )

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'wiki:listPages') return { pages: [], pending_compile_count: 0 }
    if (channel === 'wiki:listReviews') return { reviews: [] }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('WikiTab compile detail entry', () => {
  it('keeps the detail panel collapsed by default (no auto-pop)', async () => {
    const { queryByTestId } = renderTab('ingesting')
    await flush()
    expect(queryByTestId('compile-panel')).toBeNull()
  })

  it.each(['ingesting', 'completed', 'failed', 'stopped'])(
    're-opens the collapsed panel from the status bar in %s state',
    async (status) => {
      const onDetail = vi.fn()
      const { getByText, queryByTestId } = renderTab(status, onDetail)
      await flush()

      expect(queryByTestId('compile-panel')).toBeNull()
      fireEvent.click(getByText('查看详情'))

      expect(queryByTestId('compile-panel')).toBeTruthy()
      expect(onDetail).toHaveBeenLastCalledWith(true)
    },
  )
})
