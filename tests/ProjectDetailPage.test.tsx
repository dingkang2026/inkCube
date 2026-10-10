/** tests/ProjectDetailPage.test.tsx — #4 谓词心跳化 + 角标派生 + 边沿补拉回归（0.4 §6.3，顺序 4）
 *  轮询启停由 useActivity('wiki_compile') 项目级心跳派生（compileDetailOpen 兜住
 *  启动间隙）；挂载 / 项目切换 / 切到 wiki Tab 各拉一次快照；wikiCompileActive
 *  true→false 边沿补拉终态快照；Tab 角标从心跳 byProject 派生（不再依赖本地快照）。
 *  Tab 内容组件全部打桩：仅验证页面主体接线；机制本身由 useEdgeRefetch.test 覆盖。
 *  时序约定（同 tests/usePolling.test.ts）：fake timers + flush 排空 microtask。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const compileItems: Array<{ project_id?: string }> = []
const byProject = new Map<
  string,
  { files: number; imageDescribing: number; compiles: number; reports: number }
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
vi.mock('../src/features/project/affairs/AffairsTab', () => ({ AffairsTab: () => null }))
vi.mock('../src/features/project/ProjectChatPanel', () => ({ ProjectChatPanel: () => null }))
vi.mock('../src/features/project/PulseBar', () => ({
  PulseBar: (props: { reviews?: { contradiction: number }; pendingCompile?: number }) => (
    <div data-testid="pulsebar" data-contradiction={props.reviews?.contradiction}
      data-pending={props.pendingCompile} />
  ),
}))

import { render, cleanup, act, fireEvent } from '@testing-library/react'
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

    // 心跳出现本项目活跃编译 → 轮询 enabled 翻真立即首跑
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

  it('derives tab pulse from heartbeat byProject compiles', async () => {
    byProject.set('p1', { files: 0, imageDescribing: 0, compiles: 1, reports: 0 })
    const { rerender, container } = render(<ProjectDetailPage project={project} />)
    await flush()
    // 编译中：pulse 出现
    expect(container.querySelector('.tabPulse')).toBeTruthy()

    // 无活跃编译：pulse 消失
    byProject.set('p1', { files: 0, imageDescribing: 0, compiles: 0, reports: 0 })
    rerender(<ProjectDetailPage project={project} />)
    expect(container.querySelector('.tabPulse')).toBeNull()
  })

  it('defaults to the wiki tab (knowledge-first, 3.0 §8 phase 1)', async () => {
    const { container } = render(<ProjectDetailPage project={project} />)
    await flush()
    const tabs = Array.from(container.querySelectorAll('button'))
    const active = tabs.find((b) => b.className.includes('tabActive'))
    expect(active?.textContent).toContain('project.tab.wiki')
  })

  it('refetches once per manual switch back to wiki (prev-tab guard)', async () => {
    const { getByText } = render(<ProjectDetailPage project={project} />)
    await flush()
    // 挂载恰 1 次（守卫防默认 wiki 的挂载双拉）
    expect(compileStatusCount()).toBe(1)
    // 用户切走再切回：恰 +1（锁守卫「切到仍拉取」半边；删 effect 则停在 1）
    fireEvent.click(getByText('project.tab.materials'))
    fireEvent.click(getByText('project.tab.wiki'))
    await flush()
    expect(compileStatusCount()).toBe(2)
  })

  it('mounts PulseBar and refetches pulse data on compile terminal edge', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'wiki:listReviews') return { reviews: [{ type: 'contradiction' }, { type: 'contradiction' }, { type: 'data-gap' }] }
      if (ch === 'wiki:listPages') return { pages: [], pending_compile_count: 5 }
      return { task: null, ingest_summaries: [] }
    })
    const { rerender, container } = render(<ProjectDetailPage project={project} />)
    await flush()
    // 挂载注入脉搏数据：矛盾分型计数 + 后端 pending_compile_count 直读（P11 裁决）
    const bar = container.querySelector('[data-testid="pulsebar"]')!
    expect(bar.getAttribute('data-contradiction')).toBe('2')
    expect(bar.getAttribute('data-pending')).toBe('5')

    // 活跃期推进两个轮询周期：listReviews 不随 #4 轮询增长（零轮询回归锁，若被轮询化则必然 >1）
    compileItems.push({ project_id: 'p1' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    await act(async () => { vi.advanceTimersByTime(11000) })
    expect(invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length).toBe(1)

    // 编译终态边沿（true→false）补拉：恰第二次，无轮询增量
    compileItems.length = 0
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    const reviewCalls = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    expect(reviewCalls).toBe(2)
  })
})
