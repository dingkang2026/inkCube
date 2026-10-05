/** tests/useFeatureGuard.test.tsx — useFeatureGuard 入口准入用例（0.4lifecycle-design.md §7.3，v1.7）
 *  覆盖：就绪放行、缺必需依赖弹层（确认 → 锚点直达设置页 / 取消 → 中止）、
 *  仅缺可选依赖（VLM）提示条放行、数据不可得放行（第三层后端 ensure_ready 兜底）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useFeatureGuard, invalidateReadiness } from '../src/features/readiness/useReadiness'
import type { FeatureReadiness, FeatureKey } from '../src/types/error'

const { confirmMock, showToastMock, invokeMock } = vi.hoisted(() => ({
  confirmMock: vi.fn(),
  showToastMock: vi.fn(),
  invokeMock: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, _opts?: unknown) => key }),
}))
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => showToastMock,
}))

function features(over: Partial<Record<FeatureKey, FeatureReadiness>>) {
  return {
    features: {
      file_parsing: { ready: true, missing: [], optional_missing: [] },
      wiki_compile: { ready: true, missing: [], optional_missing: [] },
      report: { ready: true, missing: [], optional_missing: [] },
      task: { ready: true, missing: [], optional_missing: [] },
      knowledge_search: { ready: true, missing: [], optional_missing: [] },
      ...over,
    },
  }
}

beforeEach(() => {
  invalidateReadiness() // 模块级缓存跨测试共享，逐用例失效
  confirmMock.mockReset().mockResolvedValue(false)
  showToastMock.mockClear()
  invokeMock.mockReset()
  ;(window as any).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  invalidateReadiness()
  delete (window as any).electronAPI
  vi.restoreAllMocks()
})

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

async function runGuard(feature: FeatureKey) {
  const { result } = renderHook(() => useFeatureGuard())
  const onProceed = vi.fn()
  await act(async () => {
    await result.current(feature, onProceed)
  })
  await flush()
  return onProceed
}

describe('useFeatureGuard', () => {
  it('proceeds when feature is ready', async () => {
    invokeMock.mockResolvedValue(features({}))
    const onProceed = await runGuard('wiki_compile')
    expect(onProceed).toHaveBeenCalledTimes(1)
    expect(confirmMock).not.toHaveBeenCalled()
    expect(showToastMock).not.toHaveBeenCalled()
  })

  it('blocks with dialog when required dependency is missing; confirm navigates to settings', async () => {
    confirmMock.mockResolvedValue(true) // 用户点「去配置」
    invokeMock.mockResolvedValue(
      features({
        wiki_compile: {
          ready: false,
          missing: [{ kind: 'api_key' as const, id: 'llm', action: 'settings:external' as const }],
          optional_missing: [],
        },
      }),
    )
    const handler = vi.fn()
    window.addEventListener('app:navigate-settings', handler)
    try {
      const onProceed = await runGuard('wiki_compile')
      expect(onProceed).not.toHaveBeenCalled()
      expect(confirmMock).toHaveBeenCalledTimes(1)
      expect(handler).toHaveBeenCalledTimes(1)
      expect((handler.mock.calls[0][0] as CustomEvent).detail.section).toBe('externalModel')
    } finally {
      window.removeEventListener('app:navigate-settings', handler)
    }
  })

  it('stays blocked when user cancels the dialog', async () => {
    confirmMock.mockResolvedValue(false)
    invokeMock.mockResolvedValue(
      features({
        knowledge_search: {
          ready: false,
          missing: [{ kind: 'model' as const, id: 'embedding', action: 'settings:internal' as const }],
          optional_missing: [],
        },
      }),
    )
    const handler = vi.fn()
    window.addEventListener('app:navigate-settings', handler)
    try {
      const onProceed = await runGuard('knowledge_search')
      expect(onProceed).not.toHaveBeenCalled()
      expect(confirmMock).toHaveBeenCalledTimes(1)
      expect(handler).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('app:navigate-settings', handler)
    }
  })

  it('warns but proceeds when only optional dependency (VLM) is missing', async () => {
    invokeMock.mockResolvedValue(
      features({
        file_parsing: {
          ready: false,
          missing: [],
          optional_missing: [
            { kind: 'model' as const, id: 'vlm', action: 'settings:internal' as const },
          ],
        },
      }),
    )
    const onProceed = await runGuard('file_parsing')
    expect(showToastMock).toHaveBeenCalledWith('readiness.optionalVlm', 'warning')
    expect(confirmMock).not.toHaveBeenCalled()
    expect(onProceed).toHaveBeenCalledTimes(1)
  })

  it('proceeds when readiness data is unavailable (backend down, third layer guards)', async () => {
    invokeMock.mockRejectedValue(new Error('service down'))
    const onProceed = await runGuard('report')
    expect(onProceed).toHaveBeenCalledTimes(1)
    expect(confirmMock).not.toHaveBeenCalled()
  })
})
