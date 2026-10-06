/** src/hooks/useEdgeRefetch.ts — 终态边沿补拉（0.4 §6.3）
 *  轮询由全局心跳的活动条目驱动（enabled true→false 自停），最后一次快照可能停在
 *  终态写入前；在 enabled 翻 false 的边沿补拉一次明细，确保终态立即上屏。
 *  语义：初次挂载不触发；false→true 不触发；true→false 恰触发一次。
 *  错误处理：不吞异常，由调用方回调内自行兜底（与 usePolling 抛错计入退避的约定分层）。
 *  refetch 以 ref 包裹：调用点回调每次渲染变化不触发重跑，effect 仅依赖 enabled。 */
import { useEffect, useRef } from 'react'

export function useEdgeRefetch(enabled: boolean, refetch: () => void) {
  const prevRef = useRef(enabled)
  const refetchRef = useRef(refetch)
  refetchRef.current = refetch

  useEffect(() => {
    if (prevRef.current && !enabled) {
      refetchRef.current()
    }
    prevRef.current = enabled
  }, [enabled])
}
