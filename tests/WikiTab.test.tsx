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

// WikiTab/WikiHomePage 均消费 useToast；无 Provider 会 throw，统一 mock 捕获调用
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

// 捕获 props 以观测页面列表（force 重编译失败不清空的断言依据）与预览导航（蒙板用例入口）
let homePageProps: { pages?: unknown[]; onNavigateToPage?: (key: string) => void } = {}
vi.mock('../src/features/project/tabs/WikiHomePage', () => ({
  WikiHomePage: (props: { pages?: unknown[]; onNavigateToPage?: (key: string) => void }) => {
    homePageProps = props
    return null
  },
}))
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
  toastMock.mockReset()
  homePageProps = {}
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

describe('WikiTab retry-failed entry (2.6 §13.3)', () => {
  const withError = [
    { file_id: 'f1', filename: 'a.pdf' },
    { file_id: 'f2', filename: 'b.pdf', error: 'LLM timeout' },
  ]

  const renderTabWithSummaries = (
    status: string,
    ingestSummaries: unknown[],
    onCompileDetailChange?: (open: boolean) => void,
  ) =>
    render(
      <WikiTab
        projectId="p1"
        projectType="general"
        compileData={{ task: makeTask(status), ingest_summaries: ingestSummaries, is_resumed: false }}
        onCompileDetailChange={onCompileDetailChange}
      />,
    )

  it('offers a non-force retry entry when completed with per-file errors', async () => {
    const { getByText } = renderTabWithSummaries('completed', withError)
    await flush()

    fireEvent.click(getByText('重试失败 (1)'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:compile', {
      projectId: 'p1',
      mode: 'incremental',
      force: false,
    })
  })

  it('failed-count badge re-opens the collapsed detail panel', async () => {
    const onDetail = vi.fn()
    const { getByText, queryByTestId } = renderTabWithSummaries('completed', withError, onDetail)
    await flush()

    expect(queryByTestId('compile-panel')).toBeNull()
    fireEvent.click(getByText('1 个文件失败'))

    expect(queryByTestId('compile-panel')).toBeTruthy()
    expect(onDetail).toHaveBeenLastCalledWith(true)
  })

  it('hides the retry entry when completed without errors', async () => {
    const { queryByText } = renderTabWithSummaries('completed', [])
    await flush()

    expect(queryByText(/重试失败/)).toBeNull()
  })
})

describe('WikiTab force recompile failure (P2-1)', () => {
  const pages = [
    { id: 'w1', page_key: 'alpha', title: 'Alpha', page_type: 'entity', status: 'compiled', updated_at: '' },
  ]

  it('keeps the page list when the force compile trigger fails', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listPages') return { pages, pending_compile_count: 0 }
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:compile') throw new Error('409 busy')
      return {}
    })
    const { getAllByText } = renderTab('failed')
    await flush()
    expect(homePageProps.pages).toHaveLength(1)

    // 状态条「重新编译」→ 确认弹窗 → 危险按钮（两处同名，弹窗打开后取末个）
    fireEvent.click(getAllByText('重新编译')[0])
    const btns = getAllByText('重新编译')
    fireEvent.click(btns[btns.length - 1])
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:compile', {
      projectId: 'p1', mode: 'incremental', force: true,
    })
    // 失败时本地页面列表不得清空（清空已移至触发成功之后）
    expect(homePageProps.pages).toHaveLength(1)
    expect(toastMock).toHaveBeenLastCalledWith('编译触发失败: 409 busy', 'error')
  })
})

describe('WikiTab preview backdrop (知识问答面板同款)', () => {
  const page = { id: 'w1', page_key: 'alpha', title: 'Alpha', page_type: 'entity', status: 'compiled', updated_at: '' }
  const pageDetail = {
    page: { ...page, content: 'hello', refs: [], sources: [], tags: [], last_compiled_at: null },
    sources: [],
    change_log: [],
  }

  it('closes the preview panel from the backdrop click', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listPages') return { pages: [page], pending_compile_count: 0 }
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:getPage') return pageDetail
      return {}
    })
    const { container } = renderTab('completed')
    await flush()

    // 预览未开时无蒙板
    expect(container.querySelector('[class*="previewBackdrop"]')).toBeNull()

    // 首页点击页面 → 预览面板与蒙板一同出现
    act(() => { homePageProps.onNavigateToPage?.('alpha') })
    await flush()
    expect(container.querySelector('[class*="previewPanel"]')).toBeTruthy()
    const backdrop = container.querySelector('[class*="previewBackdrop"]')
    expect(backdrop).toBeTruthy()

    // 点蒙板 → 预览面板与蒙板一并消失（模板管理 drawer / Chat 面板同款语义）
    fireEvent.click(backdrop as Element)
    expect(container.querySelector('[class*="previewPanel"]')).toBeNull()
    expect(container.querySelector('[class*="previewBackdrop"]')).toBeNull()
  })
})
