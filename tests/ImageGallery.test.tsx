/** tests/ImageGallery.test.tsx — #4 终态边沿补拉回归（0.4 §6.3）
 *  背景 bug：图片描述轮询由全局心跳 image_describing 驱动自停，最后一批完成图
 *  可能落在末次 refreshImages 之后（默认 statusFilter='done'，新完成图不在列表）
 *  → 图库缺失刚完成的图。修复：hasActiveImages true→false 边沿补拉一次。
 *  另：状态行/类别行「全部」标签计数回归（= 各计数之和，而非当前筛选的 total）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const activityState = { items: [] as Array<{ project_id?: string; image_describing?: number }> }

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => activityState,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/hooks/useErrorHandler', () => ({
  useErrorHandler: () => vi.fn(),
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('../src/features/knowledge/ImageGallery.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/features/knowledge/SkeletonLoader', () => ({
  SkeletonImageGrid: () => null,
}))

import { render, cleanup, act, screen } from '@testing-library/react'
import { ImageGallery } from '../src/features/knowledge/ImageGallery'

const invokeMock = vi.fn()

const imagesInvokeCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'knowledge:getProjectImages').length

/** 排空 microtask（loadImages/refreshImages 与轮询 tick 均为 async，fake timers 不自动推进） */
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
    if (channel === 'knowledge:getProjectImages') return { images: [], total: 0 }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('ImageGallery terminal refetch', () => {
  it('refetches once on hasActiveImages true→false edge', async () => {
    const { rerender } = render(<ImageGallery projectId="p1" />)
    await flush()
    // mount 首次加载
    expect(imagesInvokeCount()).toBe(1)

    // 描述开始：心跳出现 image_describing → 轮询启动首跑
    activityState.items = [{ project_id: 'p1', image_describing: 1 }]
    rerender(<ImageGallery projectId="p1" />)
    await flush()
    expect(imagesInvokeCount()).toBe(2)

    // 心跳先行归零（轮询自停）：边沿补拉恰好一次
    activityState.items = []
    rerender(<ImageGallery projectId="p1" />)
    expect(imagesInvokeCount()).toBe(3)

    // 轮询已停，无额外拉取
    await flush()
    expect(imagesInvokeCount()).toBe(3)
  })

  it('does not refetch when no images were describing', async () => {
    const { rerender } = render(<ImageGallery projectId="p1" />)
    await flush()
    rerender(<ImageGallery projectId="p1" />)
    await flush()
    expect(imagesInvokeCount()).toBe(1)
  })
})

describe('ImageGallery all-tab counts', () => {
  it('shows the all-tab count as the sum of status/category counts, not the filtered total', async () => {
    // total 是当前筛选（默认 done）下的数；status_counts/category_counts
    // 不含对应维度过滤，是真实分布 → 「全部」应显示求和而非 total
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:getProjectImages') {
        return {
          images: [],
          total: 2,
          status_counts: { done: 2, archived: 8 },
          category_counts: { chart: 4, photo: 6 },
        }
      }
      return {}
    })
    render(<ImageGallery projectId="p1" />)
    await flush()
    expect(screen.getByText('knowledge.imgStatus_done (2)')).toBeTruthy()
    expect(screen.getByText('knowledge.imgStatus_archived (8)')).toBeTruthy()
    expect(screen.getByText('knowledge.statusFilter_all (10)')).toBeTruthy()
    expect(screen.getByText('knowledge.category_all (10)')).toBeTruthy()
  })

  it('renders the seal category tab (new auto-archived category)', async () => {
    render(<ImageGallery projectId="p1" />)
    await flush()
    expect(screen.getByText('knowledge.category_seal')).toBeTruthy()
  })
})
