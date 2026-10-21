/** tests/InternalModelSettings.test.tsx — 模型设置轮询终态补拉回归（0.4 §5.7）
 *  背景 bug：明细轮询由活动条目驱动（modelsActive 自停），最后一次快照可能停在
 *  下载完成前（toast 走通知推送先于 status 上屏）→ 完成态卡 0% 进度条。
 *  修复：modelsActive true→false 边沿补拉一次，终态立即渲染。
 *  时序约定（同 tests/usePolling.test.ts）：fake timers + flush 排空 microtask；
 *  不用 waitFor——RTL 检测不到 vitest fake timers（未开 globals），其内部
 *  setInterval 永不推进，用例时序会漂移。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// 可变活动条目（闭包惰性求值：工厂执行时不读取，渲染时才取）
const activityState = { items: [] as Array<{ entity_id: string }> }

const { handleErrorMock } = vi.hoisted(() => ({ handleErrorMock: vi.fn() }))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => activityState,
}))

vi.mock('../src/hooks/useErrorHandler', () => ({
  useErrorHandler: () => handleErrorMock,
}))

// m9 新增删除流引入 useConfirm；本文件用例不涉删除，恒真确认即可
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/settings/InternalModelSettings.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { InternalModelSettings } from '../src/features/settings/InternalModelSettings'
import { MODELS_IPC } from '../src/types'

const downloadingModel = {
  name: 'rapidocr', category: 'ocr', framework: 'onnx', size: '~32MB',
  size_bytes: 32_000_000, downloaded: false, downloading: true,
  progress: 0, error: null, install_type: 'download',
}
const doneModel = {
  ...downloadingModel,
  downloaded: true, downloading: false, progress: 100,
}

let statusSnapshot: Record<string, typeof downloadingModel> = {}
const invokeMock = vi.fn()

const statusInvokeCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === MODELS_IPC.status).length

/** 排空 microtask（fetchStatus/轮询 tick 为 async，fake timers 不自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  statusSnapshot = { rapidocr: downloadingModel }
  activityState.items = [{ entity_id: 'rapidocr' }]
  invokeMock.mockReset()
  handleErrorMock.mockClear()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === MODELS_IPC.status) return statusSnapshot
    if (channel === MODELS_IPC.libreofficeStatus)
      return { available: true, tool: 'ms-office', path: null }
    return null
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('InternalModelSettings terminal refetch', () => {
  it('renders 0% progress while downloading', async () => {
    render(<InternalModelSettings />)
    // 初始 mount：useEffect 立即拉取 + usePolling 首跑，恰各一次
    await flush()
    expect(statusInvokeCount()).toBe(2)
    expect(screen.getByText('0%')).toBeTruthy()
  })

  it('refetches once on modelsActive true→false edge and renders downloaded badge', async () => {
    const { rerender } = render(<InternalModelSettings />)
    await flush()
    expect(screen.getByText('0%')).toBeTruthy()

    // 下载完成：活动条目消失（轮询自停）+ 快照翻转终态。
    // 边沿补拉在 effect 提交内同步发起 → rerender 返回即应恰好 +1
    const before = statusInvokeCount()
    statusSnapshot = { rapidocr: doneModel }
    activityState.items = []
    rerender(<InternalModelSettings />)
    const afterEdge = statusInvokeCount()
    expect(afterEdge).toBe(before + 1)

    // 补拉结果上屏：终态徽章替换 0% 进度条
    await flush()
    expect(statusInvokeCount()).toBe(afterEdge) // 轮询已自停，无额外拉取
    expect(screen.queryByText('0%')).toBeNull()
    expect(screen.getByText('settings.internalModel.downloaded')).toBeTruthy()
  })
})

describe('InternalModelSettings download failure handling', () => {
  it('resets downloadingAll and surfaces error when download-all fails', async () => {
    // 回归背景：invoke reject 无 catch → downloadingAll 永久卡 true，
    // 按钮永久禁用且无恢复轮询（无活动时轮询自停）、无任何错误反馈
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === MODELS_IPC.status) return statusSnapshot
      if (channel === MODELS_IPC.downloadAll) throw new Error('backend unreachable')
      if (channel === MODELS_IPC.libreofficeStatus)
        return { available: true, tool: 'ms-office', path: null }
      return null
    })
    render(<InternalModelSettings />)
    await flush()

    const btn = screen.getByText('settings.internalModel.downloadAll') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    await act(async () => {
      fireEvent.click(btn)
    })
    await flush()

    expect(handleErrorMock).toHaveBeenCalledTimes(1)
    expect(handleErrorMock).toHaveBeenCalledWith(expect.any(Error))
    expect((screen.getByText('settings.internalModel.downloadAll') as HTMLButtonElement).disabled).toBe(false)
  })

  it('surfaces error when single model download fails', async () => {
    // 未下载模型 → 渲染下载按钮；点击后 invoke reject 应被 catch 并反馈
    statusSnapshot = { rapidocr: { ...downloadingModel, downloading: false, downloaded: false, progress: 0 } }
    activityState.items = []
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === MODELS_IPC.status) return statusSnapshot
      if (channel === MODELS_IPC.download) throw new Error('backend unreachable')
      if (channel === MODELS_IPC.libreofficeStatus)
        return { available: true, tool: 'ms-office', path: null }
      return null
    })
    render(<InternalModelSettings />)
    await flush()

    const btn = screen.getByText('settings.internalModel.download') as HTMLButtonElement
    await act(async () => {
      fireEvent.click(btn)
    })
    await flush()

    expect(handleErrorMock).toHaveBeenCalledTimes(1)
    expect(handleErrorMock).toHaveBeenCalledWith(expect.any(Error))
  })
})
