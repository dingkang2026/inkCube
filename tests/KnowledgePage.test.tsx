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

vi.mock('../src/features/knowledge/FileUploader', () => ({ FileUploader: () => null }))
vi.mock('../src/features/knowledge/ImageGallery', () => ({ ImageGallery: () => null }))
vi.mock('../src/features/knowledge/SearchBar', () => ({ SearchBar: () => null }))
vi.mock('../src/features/knowledge/SearchResultList', () => ({ SearchResultList: () => null }))
vi.mock('../src/features/knowledge/SkeletonLoader', () => ({
  SkeletonFileList: () => null,
  SkeletonSearchResults: () => null,
}))

import { render, screen, cleanup, act } from '@testing-library/react'
import { KnowledgePage } from '../src/features/knowledge/KnowledgePage'
import type { Project } from '../src/features/knowledge/types'

const project: Project = { id: 'p1', name: 'P', file_count: 0, chunk_count: 0, created_at: '' }

const invokeMock = vi.fn()
const onProjectDataChanged = vi.fn()

const countBy = (channel: string) => invokeMock.mock.calls.filter((c) => c[0] === channel).length

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
