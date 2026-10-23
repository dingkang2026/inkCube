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

// 台账解析心跳（3.0 四期 T7）：WikiTab 消费 useActivity('ledger_parse') 派生「解析中」提示；
// 本文件不测该派生（时间轴 UI 由 WikiHomePage.test 覆盖），空 items 即可
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) => ({ kind, items: [] }),
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
// 捕获 props 以观测预览面板渲染的详情内容（review H-2 用例断言 title）
const readerState = vi.hoisted(() => ({ props: null as { pageDetail?: { page?: { title?: string } } } | null }))
vi.mock('../src/features/project/tabs/WikiPageReader', () => ({
  WikiPageReader: (props: { pageDetail?: { page?: { title?: string } } }) => {
    readerState.props = props
    return <div data-testid="page-reader" />
  },
}))
vi.mock('../src/features/project/tabs/WikiPageEditor', () => ({ WikiPageEditor: () => null }))
vi.mock('../src/features/project/tabs/WikiChangeLog', () => ({ WikiChangeLog: () => null }))
vi.mock('../src/features/project/tabs/WikiSearchView', () => ({ WikiSearchView: () => null }))
vi.mock('../src/features/project/tabs/WikiGraphView', () => ({ WikiGraphView: () => null }))
// 捕获 onFindingsChanged 并渲染触发按钮,驱动「治理动作后计数回流」用例(0.4 §3.1 操作后刷新)
let lintViewProps: { onFindingsChanged?: () => void } = {}
vi.mock('../src/features/project/tabs/WikiLintView', () => ({
  WikiLintView: (props: { onFindingsChanged?: () => void }) => {
    lintViewProps = props
    return <button data-testid="lint-view" onClick={() => props.onFindingsChanged?.()} />
  },
}))
vi.mock('../src/features/project/tabs/CompilePanel', () => ({
  CompilePanel: () => <div data-testid="compile-panel" />,
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { StrictMode } from 'react'
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
  lintViewProps = {}
  readerState.props = null
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

describe('WikiTab focusSignal compile (3.0 二期 Task 13,裁决 6)', () => {
  it('triggers compile when idle and compile signal arrives', async () => {
    render(
      <WikiTab projectId="p1" projectType="general" focusSignal={{ view: 'compile', ts: 1 }} />,
    )
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('wiki:compile', {
      projectId: 'p1', mode: 'incremental', force: false,
    })
  })

  it('does not re-trigger compile while a compile is already active (M2)', async () => {
    render(
      <WikiTab
        projectId="p1"
        projectType="general"
        compileData={{ task: makeTask('ingesting'), ingest_summaries: [], is_resumed: false }}
        focusSignal={{ view: 'compile', ts: 1 }}
      />,
    )
    await flush()
    // 编译中信号到达仅停留,不得重复触发(409 + 状态条闪断,原顶栏按钮 disabled 同语义)
    expect(invokeMock).not.toHaveBeenCalledWith('wiki:compile', expect.anything())
  })

  it('StrictMode mount double-invocation triggers compile exactly once (review 轻微②)', async () => {
    // dev 下 StrictMode 挂载双跑（Toast action 从其他页跳入 → WikiTab 新挂载）：
    // consume-once effect 跑两次，闭包 state 守卫两次都读到 false，
    // compilingRef 拦第二次，不得发出两次 wiki:compile
    // （第二次 409 → 误导性「编译触发失败」Toast + 状态条闪断）
    render(
      <StrictMode>
        <WikiTab projectId="p1" projectType="general" focusSignal={{ view: 'compile', ts: 1 }} />
      </StrictMode>,
    )
    await flush()
    const compileCalls = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:compile')
    expect(compileCalls).toHaveLength(1)
  })
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

describe('WikiTab focusSignal (3.0 一期:脉搏带直达治理)', () => {
  it('switches to lint view when focusSignal arrives', async () => {
    const { rerender, queryByTestId } = render(
      <WikiTab projectId="p1" projectType="general" focusSignal={null} />
    )
    await flush()
    expect(queryByTestId('lint-view')).toBeNull()

    rerender(
      <WikiTab projectId="p1" projectType="general"
        focusSignal={{ view: 'lint', ts: Date.now() }} />
    )
    await flush()
    expect(queryByTestId('lint-view')).toBeTruthy()
  })

  it('consumes focusSignal once (null-out after handled, re-issue works)', async () => {
    const onFocusHandled = vi.fn()
    const { rerender, queryByTestId } = render(
      <WikiTab projectId="p1" projectType="general" focusSignal={null}
        onFocusHandled={onFocusHandled} />
    )
    await flush()
    rerender(
      <WikiTab projectId="p1" projectType="general" focusSignal={{ view: 'lint', ts: 1 }}
        onFocusHandled={onFocusHandled} />
    )
    await flush()
    expect(queryByTestId('lint-view')).toBeTruthy()
    expect(onFocusHandled).toHaveBeenCalledTimes(1)

    // 父级置空后：停留 lint 且不重复回调（防 Tab 往返重挂载被残留信号反复拉回 lint）
    rerender(
      <WikiTab projectId="p1" projectType="general" focusSignal={null}
        onFocusHandled={onFocusHandled} />
    )
    await flush()
    expect(queryByTestId('lint-view')).toBeTruthy()
    expect(onFocusHandled).toHaveBeenCalledTimes(1)

    // 再次下发（新 ts）：仍能触发
    rerender(
      <WikiTab projectId="p1" projectType="general" focusSignal={{ view: 'lint', ts: 2 }}
        onFocusHandled={onFocusHandled} />
    )
    await flush()
    expect(onFocusHandled).toHaveBeenCalledTimes(2)
  })
})

describe('WikiTab lint findings changed (0.4 §3.1 操作后刷新,3.0 一期最终 review)', () => {
  it('re-pulls reviews and notifies parent when lint findings change', async () => {
    const onReviewsChanged = vi.fn()
    const { rerender, queryByTestId } = render(
      <WikiTab projectId="p1" projectType="general" focusSignal={null}
        onReviewsChanged={onReviewsChanged} />
    )
    await flush()

    // 直达信号进 lint 视图,桩按钮模拟一次处置动作完成
    rerender(
      <WikiTab projectId="p1" projectType="general"
        focusSignal={{ view: 'lint', ts: Date.now() }}
        onReviewsChanged={onReviewsChanged} />
    )
    await flush()
    expect(queryByTestId('lint-view')).toBeTruthy()

    const before = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    fireEvent.click(queryByTestId('lint-view')!)
    await flush()

    const after = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    expect(after).toBe(before + 1) // WikiTab 自身重拉发现项
    expect(onReviewsChanged).toHaveBeenCalledTimes(1) // 上报父级刷新脉搏带
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

describe('WikiTab same-type project switch (review H-2)', () => {
  it('clears preview state on project switch; late stale detail cannot repopulate', async () => {
    let p1PageCalls = 0
    let resolveLate: (v: unknown) => void = () => {}
    invokeMock.mockImplementation(async (channel: string, payload?: { projectId?: string }) => {
      if (channel === 'wiki:listPages') {
        return { pages: [{ page_key: 'overview', title: '概览' }], pending_compile_count: 0 }
      }
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:getPage' && payload?.projectId === 'p1') {
        p1PageCalls += 1
        if (p1PageCalls === 1) {
          return { page: { page_key: 'overview', title: 'A 的页' }, sources: [], change_log: [] }
        }
        // 切换瞬间在途的第二次 p1 请求：挂起，晚点回
        return new Promise((resolve) => { resolveLate = resolve })
      }
      return { page: { page_key: 'overview', title: 'B 的页' }, sources: [], change_log: [] }
    })
    const { rerender, getByTestId, queryByTestId } = render(
      <WikiTab projectId="p1" projectType="general" />,
    )
    await flush()
    act(() => { homePageProps.onNavigateToPage?.('overview') })
    await flush()
    expect(getByTestId('page-reader')).toBeTruthy()
    expect(readerState.props?.pageDetail?.page?.title).toBe('A 的页')

    // 同类型项目切换：父层 key=projectType 不变 → 实例存活，清理 effect 必须清预览态
    // （否则预览面板显示 A 的页——标题一致无违和感，编辑保存会整页覆盖 B 项目同名页）
    rerender(<WikiTab projectId="p2" projectType="general" />)
    await flush()
    expect(queryByTestId('page-reader')).toBeNull()

    // 切换瞬间在途的 p1 detail 晚到 → latestPidRef 守卫丢弃，孤儿 state 不得复活预览
    await act(async () => {
      resolveLate({ page: { page_key: 'overview', title: 'A 的页' }, sources: [], change_log: [] })
    })
    await flush()
    expect(queryByTestId('page-reader')).toBeNull()

    // p2 点同一页 → 面板渲染 B 的详情（标题一致也不再拿旧项目内容充数）
    act(() => { homePageProps.onNavigateToPage?.('overview') })
    await flush()
    expect(getByTestId('page-reader')).toBeTruthy()
    expect(readerState.props?.pageDetail?.page?.title).toBe('B 的页')
  })
})
