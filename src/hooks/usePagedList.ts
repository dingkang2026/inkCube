/** src/hooks/usePagedList.ts — 列表接口统一「加载更多」分页 hook（0.1dev-guide「列表分页统一约定」）
 *
 * 对齐 useTasks 的 limitsRef 范式（流式列表形态：看板/时间轴/侧栏/面板）：
 *  - 「加载更多」= limit 递增、offset 恒 0 的全量重拉——不做客户端拼接，
 *    新增行天然进入头部，轮询/重拉数据一致（本地 SQLite 全量重拉廉价）；
 *  - reload() 按当前已加载量重拉（limitsRef 模式）：轮询/边沿刷新不退回第一页；
 *  - has_more 由后端 limit+1 探测，hasMore=false 后 loadMore 空转；
 *  - total（单表 COUNT）端点未返回时为 null（has_more-only 形态兼容）。
 *
 * 页码形态（管理页：服务端筛选下沉 + 页码控件）不使用本 hook，
 * 对齐 KnowledgePage 文件列表范式自管 limit/offset。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

/** 「加载更多」统一页长（与 useTasks PAGE_SIZE 同源，后端 limit 默认值一致） */
export const PAGED_LIST_PAGE_SIZE = 50

export interface UsePagedListOptions {
  /** IPC 通道名（如 'reports:listReports'） */
  channel: string
  /** 响应中列表数组的键名（各通道不同：reports/tasks/reviews…） */
  itemsKey: string
  /** 除 limit 外的固定筛选参数（q/status/projectId…）；内联对象安全（内部按 JSON 序列化比对） */
  params?: Record<string, unknown>
  /** 单页大小，默认 50 */
  pageSize?: number
  /** false 不首拉（挂载条件未就绪，如等待 projectId）；转 true 时拉首页。默认 true */
  enabled?: boolean
}

export interface PagedList<T> {
  items: T[]
  hasMore: boolean
  /** 服务端 total；端点未返回（has_more-only 形态）为 null */
  total: number | null
  /** 首拉/筛选变化重拉进行中（reload/loadMore 不置位，避免轮询闪烁） */
  loading: boolean
  /** 加载更多进行中 */
  loadingMore: boolean
  error: string | null
  /** 加载下一页（hasMore=false 或已在加载时空转） */
  loadMore: () => Promise<void>
  /** 按当前已加载量静默重拉（轮询/边沿刷新入口；不退回第一页） */
  reload: () => Promise<void>
}

export function usePagedList<T = unknown>({
  channel,
  itemsKey,
  params,
  pageSize = PAGED_LIST_PAGE_SIZE,
  enabled = true,
}: UsePagedListOptions): PagedList<T> {
  const [items, setItems] = useState<T[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [total, setTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 已加载量 ref（useTasks limitsRef 模式）：reload/外部回调读到最新值不退回第一页
  const limitRef = useRef(pageSize)
  // 竞态守卫：迟到响应（loadMore 与 reload 并发）不回写
  const seqRef = useRef(0)
  // items.length 镜像：reload 在轮询回调中调用，state 闭包过期不可靠
  const countRef = useRef(0)
  // params 引用稳定化：调用方传内联对象不会触发重拉（JSON 序列化比对）
  const paramsRef = useRef(params)
  paramsRef.current = params
  const paramsKey = JSON.stringify(params ?? {})

  const fetchPage = useCallback(
    async (limit: number) => {
      const seq = ++seqRef.current
      try {
        const res = (await window.electronAPI.invoke(channel, {
          ...(paramsRef.current ?? {}),
          limit,
        })) as Record<string, unknown> | null
        if (seqRef.current !== seq) return
        const raw = res?.[itemsKey]
        const list = (Array.isArray(raw) ? raw : []) as T[]
        setItems(list)
        countRef.current = list.length
        limitRef.current = limit
        setHasMore(!!res?.has_more)
        setTotal(typeof res?.total === 'number' ? res.total : null)
        setError(null)
      } catch (err) {
        if (seqRef.current !== seq) return
        setError(err instanceof Error ? err.message : String(err))
      }
    },
    [channel, itemsKey],
  )

  // 首拉 + 筛选变化重拉（limit 重置回单页量）；paramsKey 代替 params 依赖
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    setLoading(true)
    fetchPage(pageSize).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [channel, itemsKey, paramsKey, pageSize, enabled, fetchPage])

  const loadMore = useCallback(async () => {
    if (!hasMore || loadingMore || loading) return
    setLoadingMore(true)
    try {
      await fetchPage(limitRef.current + pageSize)
    } finally {
      setLoadingMore(false)
    }
  }, [hasMore, loadingMore, loading, pageSize, fetchPage])

  const reload = useCallback(async () => {
    // 按已加载量重拉（不退回第一页）；下限为单页量——0 会命中后端 limit=0 全量语义
    await fetchPage(Math.max(countRef.current, pageSize))
  }, [pageSize, fetchPage])

  return { items, hasMore, total, loading, loadingMore, error, loadMore, reload }
}
