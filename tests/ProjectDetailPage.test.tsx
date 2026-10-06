/** tests/ProjectDetailPage.test.tsx — #4 谓词心跳化 + 角标派生 + 边沿补拉回归（0.4 §6.3，顺序 4）
 *  轮询启停由 useActivity('wiki_compile') 项目级心跳派生（compileDialogOpen 兜住
 *  启动间隙）；挂载 / 项目切换 / 切到 wiki Tab 各拉一次快照；wikiCompileActive
 *  true→false 边沿补拉终态快照；Tab 角标从心跳 byProject 派生（不再依赖本地快照）。
 *  Tab 内容组件全部打桩：仅验证页面主体接线；机制本身由 useEdgeRefetch.test 覆盖。
 *  时序约定（同 tests/usePolling.test.ts）：fake timers + flush 排空 microtask。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const compileItems: Array<{ project_id?: string }> = []
const byProject = new Map<
  string,
  { files: number; imageDescribing: number; compiles: number; waitingReviews: number; reports: number }
>()

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) =>
    kind === 'wiki_compile'
      ? { kind, items: compileItems, isActive: compileItems.length > 0 }
      : {
          status: null,
          summary: {},
          byProject,
          activeProjectIds: new Set(),
          feed: [],
          applyStatus: () => {},
          markRead: async () => {},
        },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/features/project/ProjectDetailPage.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/features/knowledge/KnowledgePage', () => ({ KnowledgePage: () => null }))
vi.mock('../src/features/project/tabs/WikiTab', () => ({ WikiTab: () => null }))
vi.mock('../src/features/project/tabs/ReportsTab', () => ({ ReportsTab: () => null }))
vi.mock('../src/features/project/tabs/FormsTab', () => ({ FormsTab: () => null }))
vi.mock('../src/features/project/ProjectChatPanel', () => ({ ProjectChatPanel: () => null }))

import { render, screen, cleanup, act } from '@testing-library/react'
import { ProjectDetailPage } from '../src/features/project/ProjectDetailPage'
import type { Project } from '../src/features/knowledge/types'

const project: Project = { id: 'p1', name: 'P', file_count: 0, chunk_count: 0, created_at: '' }

const invokeMock = vi.fn()

const compileStatusCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'wiki:compileStatus').length

/** 排空 microtask（checkWikiCompileStatus 与轮询 tick 均为 async，fake timers 不自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  compileItems.length = 0
  byProject.clear()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async () => ({ task: null, ingest_summaries: [] }))
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('ProjectDetailPage wiki compile heartbeat wiring', () => {
  it('fetches a snapshot baseline on mount and stays idle without active compile', async () => {
    render(<ProjectDetailPage project={project} />)
    await flush()
    // 挂载即拉（0.4 §6.3 启动间隙）：不等首个轮询 tick
    expect(compileStatusCount()).toBe(1)

    // 心跳无活跃编译：轮询不启动，计数不增
    await flush()
    expect(compileStatusCount()).toBe(1)
  })

  it('starts polling on first tick when heartbeat has a compile of this project', async () => {
    const { rerender } = render(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(1)

    // 心跳出现本项目活跃编译（含 waiting_review）→ 轮询 enabled 翻真立即首跑
    compileItems.push({ project_id: 'p1' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(2)
  })

  it('ignores compiles of other projects', async () => {
    const { rerender } = render(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(1)

    compileItems.push({ project_id: 'other' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(1)
  })

  it('refetches exactly once on wikiCompileActive true→false edge (terminal snapshot)', async () => {
    const { rerender } = render(<ProjectDetailPage project={project} />)
    await flush()
    compileItems.push({ project_id: 'p1' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(2)

    // 心跳先行翻假（终态写入后 δ<5s 窗口，快照停在中间态）：边沿补拉恰一次
    compileItems.length = 0
    rerender(<ProjectDetailPage project={project} />)
    expect(compileStatusCount()).toBe(3)

    // 轮询已停，无额外拉取
    await flush()
    expect(compileStatusCount()).toBe(3)
  })

  it('derives tab badges from heartbeat byProject (waiting review / compiling)', async () => {
    byProject.set('p1', { files: 0, imageDescribing: 0, compiles: 0, waitingReviews: 1, reports: 0 })
    const { rerender, container } = render(<ProjectDetailPage project={project} />)
    await flush()
    // 待审阅角标（t mock 返回 key 本身）；compiles=0 → 无 pulse
    expect(screen.getByText('project.wiki.reviewBadge')).toBeTruthy()
    expect(container.querySelector('.tabPulse')).toBeNull()

    // 切到编译中：pulse 出现、待审阅角标消失
    byProject.set('p1', { files: 0, imageDescribing: 0, compiles: 1, waitingReviews: 0, reports: 0 })
    rerender(<ProjectDetailPage project={project} />)
    expect(screen.queryByText('project.wiki.reviewBadge')).toBeNull()
    expect(container.querySelector('.tabPulse')).toBeTruthy()
  })
})
