/** tests/KnowledgePage.test.tsx — #3 终态边沿补拉回归（0.4 §6.3）
 *  背景 bug：文件解析轮询由全局心跳 hasActiveFiles 驱动自停，心跳先行翻 false 时
 *  轮询内 curCount===0 的全量刷新分支可能不执行 → 列表/最近片段滞留解析中。
 *  修复：hasActiveFiles true→false 边沿补拉 loadFiles + loadRecentChunks +
 *  onProjectDataChanged。
 *  子组件打桩隔离：仅验证页面主体的接线；机制本身由 useEdgeRefetch.test 覆盖。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const activityState = { items: [] as Array<{ project_id?: string; count?: number }> }

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => activityState,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/features/readiness/useReadiness', () => ({
  useFeatureGuard: () => async (_feature: string, fn: () => void) => { fn() },
}))

vi.mock('../src/features/knowledge/KnowledgePage.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

let fileUploaderProps: any = null
vi.mock('../src/features/knowledge/FileUploader', () => ({
  FileUploader: (props: unknown) => {
    fileUploaderProps = props
    return null
  },
}))
vi.mock('../src/features/knowledge/ImageGallery', () => ({ ImageGallery: () => null }))
vi.mock('../src/features/knowledge/SearchBar', () => ({ SearchBar: () => null }))
vi.mock('../src/features/knowledge/SearchResultList', () => ({ SearchResultList: () => null }))
vi.mock('../src/features/knowledge/SkeletonLoader', () => ({
  SkeletonFileList: () => null,
  SkeletonSearchResults: () => null,
}))

import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { KnowledgePage } from '../src/features/knowledge/KnowledgePage'
import type { Project } from '../src/features/knowledge/types'

const project: Project = { id: 'p1', name: 'P', file_count: 0, chunk_count: 0, created_at: '' }

const invokeMock = vi.fn()
const onProjectDataChanged = vi.fn()

const countBy = (channel: string) => invokeMock.mock.calls.filter((c) => c[0] === channel).length

const lastGetFilesPayload = () => {
  const calls = invokeMock.mock.calls.filter((c) => c[0] === 'knowledge:getFiles')
  return calls[calls.length - 1][1] as Record<string, unknown>
}

/** 排空 microtask（loadFiles/轮询 tick 均为 async，fake timers 不自动推进） */
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
  fileUploaderProps = null
  invokeMock.mockReset()
  onProjectDataChanged.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'knowledge:getFiles') return { files: [], total: 0 }
    if (channel === 'knowledge:recentChunks') return { results: [] }
    if (channel === 'knowledge:getActiveFiles') return { files: [{ id: 'f1' }] }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('KnowledgePage terminal refetch', () => {
  it('refetches files/chunks and notifies parent on hasActiveFiles true→false edge', async () => {
    const { rerender } = render(
      <KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />,
    )
    await flush()
    expect(countBy('knowledge:getFiles')).toBe(1)
    expect(countBy('knowledge:recentChunks')).toBe(1)
    expect(onProjectDataChanged).not.toHaveBeenCalled()

    // 解析开始：全局心跳出现活动条目 → 轮询启动首跑 getActiveFiles
    activityState.items = [{ project_id: 'p1', count: 1 }]
    rerender(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()
    expect(countBy('knowledge:getActiveFiles')).toBe(1)
    expect(countBy('knowledge:getFiles')).toBe(1)

    // 心跳先行翻 false（轮询自停）：边沿补拉明细与关联数据，恰好一次
    activityState.items = []
    rerender(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    expect(countBy('knowledge:getFiles')).toBe(2)
    expect(countBy('knowledge:recentChunks')).toBe(2)
    expect(onProjectDataChanged).toHaveBeenCalledTimes(1)

    // 轮询已停，无额外拉取
    await flush()
    expect(countBy('knowledge:getFiles')).toBe(2)
    expect(onProjectDataChanged).toHaveBeenCalledTimes(1)
  })

  it('renders welcome state without project and never polls', async () => {
    render(<KnowledgePage selectedProject={null} onProjectDataChanged={onProjectDataChanged} />)
    await flush()
    expect(screen.getByText('knowledge.welcomeTitle')).toBeTruthy()
    expect(countBy('knowledge:getFiles')).toBe(0)
    expect(onProjectDataChanged).not.toHaveBeenCalled()
  })
})

describe('KnowledgePage server-side status filter', () => {
  /** 回归背景：状态筛选原为纯客户端过滤只作用当前页，跨页失效；
   *  下沉服务端后切换筛选必须以 status 参数回第 1 页拉取。 */

  it('passes status + offset=0 when switching filter, drops status on all', async () => {
    const user = (
      <KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />
    )
    render(user)
    await flush()
    // 初始加载：默认 all → 不传 status
    expect(lastGetFilesPayload().status).toBeUndefined()

    // 切「处理中」→ 立即以 status=processing 回第 1 页
    await act(async () => {
      screen.getByText('knowledge.filterProcessing').click()
    })
    await flush()
    expect(lastGetFilesPayload()).toMatchObject({ status: 'processing', offset: 0 })

    // 切「失败」→ 字面状态
    await act(async () => {
      screen.getByText('knowledge.filterError').click()
    })
    await flush()
    expect(lastGetFilesPayload()).toMatchObject({ status: 'error', offset: 0 })

    // 切回「全部」→ 不传 status
    await act(async () => {
      screen.getByText('knowledge.filterAll').click()
    })
    await flush()
    expect(lastGetFilesPayload().status).toBeUndefined()
  })
})

describe('KnowledgePage pagination preservation', () => {
  /** 回归背景：选中文件的 effect 把 loadFiles 硬编码第 1 页，点击行选中后
   *  列表跳回首页；终态刷新（轮询/边沿补拉）同样硬编码第 1 页。 */

  const withPagedData = () => {
    invokeMock.mockImplementation(async (channel: string, payload?: Record<string, unknown>) => {
      if (channel === 'knowledge:getFiles') {
        return { files: [{ id: 'fx', filename: 'x.pdf' }], total: 100 }
      }
      if (channel === 'knowledge:recentChunks') return { results: [] }
      if (channel === 'knowledge:getActiveFiles') return { files: [{ id: 'f1' }] }
      return {}
    })
  }

  it('keeps the current page when selecting a file', async () => {
    withPagedData()
    render(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()
    expect(lastGetFilesPayload().offset).toBe(0)

    // 翻到第 2 页
    await act(async () => {
      fileUploaderProps.onPageChange?.(2)
    })
    await flush()
    expect(lastGetFilesPayload().offset).toBe(20)

    // 点击行选中文件：列表不得跳回第 1 页，仅 recent-chunks 跟随作用域
    await act(async () => {
      fileUploaderProps.onSelectFile?.('fx')
    })
    await flush()
    expect(lastGetFilesPayload().offset).toBe(20)
    const recentCalls = invokeMock.mock.calls.filter((c) => c[0] === 'knowledge:recentChunks')
    expect((recentCalls[recentCalls.length - 1][1] as Record<string, unknown>).fileId).toBe('fx')
  })

  it('refetches the CURRENT page on the terminal edge, not page 1', async () => {
    withPagedData()
    const { rerender } = render(
      <KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />,
    )
    await flush()

    // 翻到第 2 页后进入解析 → 心跳翻 false 触发边沿补拉
    await act(async () => {
      fileUploaderProps.onPageChange?.(2)
    })
    await flush()
    activityState.items = [{ project_id: 'p1', count: 1 }]
    rerender(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()
    activityState.items = []
    rerender(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()

    // 边沿补拉应保持第 2 页（offset 20），而非跳回首页
    expect(lastGetFilesPayload().offset).toBe(20)
  })

  it('falls back to the last page when the kept page overflows', async () => {
    // 第 1 页 total=100（5 页）；翻到第 5 页时总数已缩到 60（3 页）→ offset 80 越界空页
    // → 自愈回退末页 3（offset 40）
    invokeMock.mockImplementation(async (channel: string, payload?: Record<string, unknown>) => {
      if (channel === 'knowledge:getFiles') {
        const offset = payload?.offset ?? 0
        if (offset === 0) return { files: [{ id: 'a' }], total: 100 }
        if (offset === 80) return { files: [], total: 60 }
        return { files: [{ id: 'b' }], total: 60 }
      }
      if (channel === 'knowledge:recentChunks') return { results: [] }
      if (channel === 'knowledge:getActiveFiles') return { files: [{ id: 'f1' }] }
      return {}
    })
    render(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()

    await act(async () => {
      fileUploaderProps.onPageChange?.(5)
    })
    await flush()

    expect(lastGetFilesPayload().offset).toBe(40) // ceil(60/20)=3 → offset 40
  })
})

describe('KnowledgePage status filter counts', () => {
  /** 回归背景：筛选 Tab 无计数，活动文件散布列表时无法一眼看到分布；
   *  status_counts 由后端返回（搜索口径、不含状态筛选），「全部」=求和，
   *  「处理中」=pending+parsing（与后端筛选同口径）。 */

  it('renders per-tab counts from status_counts', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:getFiles') {
        return {
          files: [],
          total: 7,
          status_counts: { done: 2, pending: 1, parsing: 1, error: 3 },
        }
      }
      if (channel === 'knowledge:recentChunks') return { results: [] }
      return {}
    })
    render(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()
    expect(screen.getByText('knowledge.filterAll (7)')).toBeTruthy()
    expect(screen.getByText('knowledge.filterProcessing (2)')).toBeTruthy()
    expect(screen.getByText('knowledge.filterDone (2)')).toBeTruthy()
    expect(screen.getByText('knowledge.filterError (3)')).toBeTruthy()
  })

  it('omits the count suffix when a status has no files', async () => {
    render(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()
    // 默认 mock 无 status_counts → 全 0 → 无后缀（既有测试依赖精确匹配）
    expect(screen.getByText('knowledge.filterError')).toBeTruthy()
  })

  it('refreshes counts and list when a file finishes mid-batch (partial completion)', async () => {
    /** 回归背景：轮询部分完成分支（活动 2→1）原先只刷 recentChunks，
     *  Tab 计数与置顶重排要等全部完成才刷新。 */
    let activeIds = ['f1', 'f2']
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:getFiles') {
        return {
          files: [{ id: 'fx', filename: 'x.pdf' }],
          total: 100,
          status_counts: { pending: activeIds.length },
        }
      }
      if (channel === 'knowledge:recentChunks') return { results: [] }
      if (channel === 'knowledge:getActiveFiles') return { files: activeIds.map((id) => ({ id })) }
      return {}
    })
    const { rerender } = render(
      <KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />,
    )
    await flush()
    expect(screen.getByText('knowledge.filterProcessing (2)')).toBeTruthy()

    // 解析开始：轮询启动首跑（活动 2 个，prev=-1 → 不刷列表）
    activityState.items = [{ project_id: 'p1', count: 2 }]
    rerender(<KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />)
    await flush()
    expect(countBy('knowledge:getFiles')).toBe(1)

    // 第二 tick：f1 完成（活动 2→1，部分完成分支）→ 立即重拉列表刷新计数
    activeIds = ['f2']
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    await flush()
    expect(countBy('knowledge:getFiles')).toBe(2)
    expect(lastGetFilesPayload().offset).toBe(0) // 保持当前页
    expect(screen.getByText('knowledge.filterProcessing (1)')).toBeTruthy()
  })
})

describe('KnowledgePage file panel drag resize', () => {
  /** 文件列表面板右缘拖拽调宽：默认 320px，向右拖增宽，夹在 260~960px；
   *  与 chat/preview 面板同一拖拽交互模式。 */

  it('starts at 320px and resizes via the right-edge handle with clamping', async () => {
    const { container } = render(
      <KnowledgePage selectedProject={project} onProjectDataChanged={onProjectDataChanged} />,
    )
    await flush()
    const panel = container.querySelector('[class*="inputPanel"]') as HTMLElement
    expect(panel).toBeTruthy()
    expect(panel.style.width).toBe('320px')

    const handle = container.querySelector('[class*="panelDragHandle"]') as HTMLElement
    expect(handle).toBeTruthy()

    // 向右拖 100px → 420px
    fireEvent.mouseDown(handle, { clientX: 100 })
    fireEvent.mouseMove(document, { clientX: 200 })
    expect(panel.style.width).toBe('420px')
    fireEvent.mouseUp(document)

    // 左拖超下限 → 夹在 260px
    fireEvent.mouseDown(handle, { clientX: 500 })
    fireEvent.mouseMove(document, { clientX: -1000 })
    expect(panel.style.width).toBe('260px')
    fireEvent.mouseUp(document)

    // 右拖超上限 → 夹在 960px
    fireEvent.mouseDown(handle, { clientX: 0 })
    fireEvent.mouseMove(document, { clientX: 5000 })
    expect(panel.style.width).toBe('960px')
    fireEvent.mouseUp(document)
  })
})
