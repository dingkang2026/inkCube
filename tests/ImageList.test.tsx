/** tests/ImageList.test.tsx — 描述中冻结回归（0.4 §6.2/§6.3）
 *  背景 bug：文件详情图片列表仅在打开时拉一次（无轮询/补拉），描述进行中
 *  打开会永久冻结在旧状态（完成的图不出现、pending 不动）。
 *  修复：image_describing 心跳驱动 4s 静默轮询 + 终态边沿补拉。
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

const confirmState = { value: true }

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => confirmState.value,
}))

vi.mock('../src/features/knowledge/ImageList.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, cleanup, fireEvent, screen } from '@testing-library/react'
import { ImageList } from '../src/features/knowledge/ImageList'

const invokeMock = vi.fn()

const countBy = (channel: string) =>
  invokeMock.mock.calls.filter((c) => c[0] === channel).length

/** 排空 microtask（loadImages/refreshImages 与轮询 tick 均为 async，fake timers 不自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

import { act } from 'react'

beforeEach(() => {
  vi.useFakeTimers()
  activityState.items = []
  confirmState.value = true
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'knowledge:getImages') {
      return { images: [{ id: 'i1', status: 'pending' }] }
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

describe('ImageList refresh during describing', () => {
  it('polls while images are describing and refetches once on the terminal edge', async () => {
    const { rerender } = render(<ImageList projectId="p1" fileId="fx" onClose={() => {}} />)
    await flush()
    expect(countBy('knowledge:getImages')).toBe(1)

    // 描述开始：心跳出现 image_describing → 轮询启动首跑
    activityState.items = [{ project_id: 'p1', image_describing: 1 }]
    rerender(<ImageList projectId="p1" fileId="fx" onClose={() => {}} />)
    await flush()
    expect(countBy('knowledge:getImages')).toBe(2)

    // 心跳先行归零（轮询自停）：边沿补拉恰好一次
    activityState.items = []
    rerender(<ImageList projectId="p1" fileId="fx" onClose={() => {}} />)
    expect(countBy('knowledge:getImages')).toBe(3)

    // 轮询已停，无额外拉取
    await flush()
    expect(countBy('knowledge:getImages')).toBe(3)
  })

  it('does not poll when nothing is describing', async () => {
    const { rerender } = render(<ImageList projectId="p1" fileId="fx" onClose={() => {}} />)
    await flush()
    rerender(<ImageList projectId="p1" fileId="fx" onClose={() => {}} />)
    await flush()
    expect(countBy('knowledge:getImages')).toBe(1)
  })
})

describe('ImageList delete image', () => {
  it('confirm 后经 knowledge:deleteImage 删除并重拉列表', async () => {
    render(<ImageList projectId="p1" fileId="fx" onClose={() => {}} />)
    await flush()
    const before = countBy('knowledge:getImages')
    fireEvent.click(screen.getByTitle('knowledge.deleteImage'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('knowledge:deleteImage', { projectId: 'p1', imageId: 'i1' })
    expect(countBy('knowledge:getImages')).toBe(before + 1) // loadImages 重拉
  })

  it('取消确认不发起删除', async () => {
    confirmState.value = false
    render(<ImageList projectId="p1" fileId="fx" onClose={() => {}} />)
    await flush()
    fireEvent.click(screen.getByTitle('knowledge.deleteImage'))
    await flush()
    expect(invokeMock.mock.calls.filter(([c]) => c === 'knowledge:deleteImage').length).toBe(0)
  })
})
