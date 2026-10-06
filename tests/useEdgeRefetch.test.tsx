/** tests/useEdgeRefetch.test.tsx — 终态边沿补拉 hook 语义回归（0.4 §6.3）
 *  覆盖 useEdgeRefetch 的四条边界：初次挂载不触发 / true→false 恰一次 /
 *  false→true 不触发（且第二轮边沿可再触发）/ refetch 闭包更新不误触。
 *  四个落地调用点（InternalModelSettings / KnowledgePage / ImageGallery /
 *  ReportPreview）的接线回归见各自组件测试。 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import { useEdgeRefetch } from '../src/hooks/useEdgeRefetch'

afterEach(cleanup)

describe('useEdgeRefetch', () => {
  it('does not fire on initial mount or while enabled stays false', () => {
    const refetch = vi.fn()
    const { rerender } = renderHook(({ enabled }) => useEdgeRefetch(enabled, refetch), {
      initialProps: { enabled: false },
    })
    rerender({ enabled: false })
    rerender({ enabled: false })
    expect(refetch).not.toHaveBeenCalled()
  })

  it('fires exactly once on true→false edge', () => {
    const refetch = vi.fn()
    const { rerender } = renderHook(({ enabled }) => useEdgeRefetch(enabled, refetch), {
      initialProps: { enabled: true },
    })
    rerender({ enabled: false })
    expect(refetch).toHaveBeenCalledTimes(1)
    // 停在 false 后的 rerender 不再触发
    rerender({ enabled: false })
    rerender({ enabled: false })
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('does not fire on false→true, fires again on the next true→false', () => {
    const refetch = vi.fn()
    const { rerender } = renderHook(({ enabled }) => useEdgeRefetch(enabled, refetch), {
      initialProps: { enabled: false },
    })
    rerender({ enabled: true })
    expect(refetch).not.toHaveBeenCalled()
    rerender({ enabled: false })
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('calls the latest refetch closure; its identity change alone does not trigger', () => {
    const initial = vi.fn()
    const latest = vi.fn()
    const { rerender } = renderHook(({ enabled, fn }) => useEdgeRefetch(enabled, fn), {
      initialProps: { enabled: true, fn: initial },
    })
    // enabled 不变，仅 refetch 引用更新 → 不触发
    rerender({ enabled: true, fn: latest })
    expect(initial).not.toHaveBeenCalled()
    expect(latest).not.toHaveBeenCalled()
    // 边沿触发时执行的是最新闭包
    rerender({ enabled: false, fn: latest })
    expect(initial).not.toHaveBeenCalled()
    expect(latest).toHaveBeenCalledTimes(1)
  })
})
