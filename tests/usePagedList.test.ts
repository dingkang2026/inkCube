/** tests/usePagedList.test.ts — 列表分页统一 hook 的核心语义回归。
 *
 * 覆盖四项约定（0.1dev-guide「列表分页统一约定」流式列表形态）：
 * 首页拉取 / 加载更多（limit 递增重拉）/ has_more 终止空转 / reload 按已加载量重拉，
 * 另附筛选参数变化重拉、enabled 门控与错误路径。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { usePagedList } from '../src/hooks/usePagedList'

// 稳定引用 mock：返回新 vi.fn() 会因 useCallback 依赖失效触发无限重渲染
const invokeMock = vi.hoisted(() => vi.fn())

vi.stubGlobal('electronAPI', { invoke: invokeMock })

beforeEach(() => {
  invokeMock.mockReset()
})

afterEach(cleanup)

/** 构造后端分页响应（itemsKey='reports' 形态） */
function page(values: number[], hasMore: boolean, total: number) {
  return { reports: values, has_more: hasMore, total }
}

function range(n: number) {
  return Array.from({ length: n }, (_, i) => i)
}

describe('usePagedList', () => {
  it('首拉：发 limit=50 并透传筛选参数，填充 items/hasMore/total', async () => {
    invokeMock.mockResolvedValue(page([1], true, 3))
    const { result } = renderHook(() =>
      usePagedList<number>({
        channel: 'reports:listReports',
        itemsKey: 'reports',
        params: { projectId: 'p1' },
      }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(invokeMock).toHaveBeenCalledWith('reports:listReports', {
      projectId: 'p1',
      limit: 50,
    })
    expect(result.current.items).toEqual([1])
    expect(result.current.hasMore).toBe(true)
    expect(result.current.total).toBe(3)
  })

  it('loadMore：limit 递增至 100 全量重拉，items 替换', async () => {
    invokeMock
      .mockResolvedValueOnce(page([1, 2], true, 5))
      .mockResolvedValueOnce(page([1, 2, 3], true, 5))
    const { result } = renderHook(() =>
      usePagedList<number>({ channel: 'c', itemsKey: 'reports' }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => {
      await result.current.loadMore()
    })
    expect(invokeMock).toHaveBeenLastCalledWith('c', { limit: 100 })
    expect(result.current.items).toEqual([1, 2, 3])
    expect(result.current.loadingMore).toBe(false)
  })

  it('has_more=false 后 loadMore 空转：不再发请求', async () => {
    invokeMock.mockResolvedValue(page([1], false, 1))
    const { result } = renderHook(() =>
      usePagedList<number>({ channel: 'c', itemsKey: 'reports' }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    const calls = invokeMock.mock.calls.length
    await act(async () => {
      await result.current.loadMore()
    })
    expect(invokeMock.mock.calls.length).toBe(calls)
  })

  it('reload：按已加载量重拉（limitsRef 模式），不退回第一页', async () => {
    invokeMock
      .mockResolvedValueOnce(page(range(50), true, 120))
      .mockResolvedValueOnce(page(range(100), true, 120))
    const { result } = renderHook(() =>
      usePagedList<number>({ channel: 'c', itemsKey: 'reports' }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => {
      await result.current.loadMore()
    })
    expect(result.current.items).toHaveLength(100)
    invokeMock.mockResolvedValueOnce(page(range(100), false, 120))
    await act(async () => {
      await result.current.reload()
    })
    expect(invokeMock).toHaveBeenLastCalledWith('c', { limit: 100 })
    expect(result.current.items).toHaveLength(100)
    expect(result.current.hasMore).toBe(false)
  })

  it('reload 在零已加载时退化为单页量（不触发后端 limit=0 全量语义）', async () => {
    invokeMock.mockResolvedValue(page([], false, 0))
    const { result } = renderHook(() =>
      usePagedList<number>({ channel: 'c', itemsKey: 'reports' }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    await act(async () => {
      await result.current.reload()
    })
    expect(invokeMock).toHaveBeenLastCalledWith('c', { limit: 50 })
  })

  it('筛选参数变化：limit 重置回单页量重拉', async () => {
    invokeMock.mockResolvedValue(page([1], false, 1))
    const { result, rerender } = renderHook(
      ({ q }: { q?: string }) =>
        usePagedList<number>({ channel: 'c', itemsKey: 'reports', params: { q } }),
      { initialProps: { q: undefined as string | undefined } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    rerender({ q: 'abc' })
    await waitFor(() =>
      expect(invokeMock).toHaveBeenLastCalledWith('c', { q: 'abc', limit: 50 }),
    )
  })

  it('enabled=false 不首拉；转 true 后拉取', async () => {
    invokeMock.mockResolvedValue(page([1], false, 1))
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        usePagedList<number>({ channel: 'c', itemsKey: 'reports', enabled }),
      { initialProps: { enabled: false } },
    )
    expect(invokeMock).not.toHaveBeenCalled()
    rerender({ enabled: true })
    await waitFor(() => expect(result.current.items).toEqual([1]))
  })

  it('请求失败：error 记录、不抛出 unhandled rejection', async () => {
    invokeMock.mockRejectedValue(new Error('backend down'))
    const { result } = renderHook(() =>
      usePagedList<number>({ channel: 'c', itemsKey: 'reports' }),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('backend down')
    expect(result.current.items).toEqual([])
  })
})
