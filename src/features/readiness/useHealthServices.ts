/** src/features/readiness/useHealthServices.ts — 服务健康段共享订阅（0.3 §4.4 导航状态点）。
 *
 * 挂载拉一次 system:getHealth + `app:health-changed`（设置保存/模型下载完成边沿）
 * / window focus 重拉，零新增轮询（0.4 §8.1）。消费方：SystemAlerts（服务异常红条，
 * 原地状态机提取为此 hook，行为不变）、SettingsPage（导航条目必配状态点）。
 */
import { useCallback, useEffect, useState } from 'react'
import type { HealthStatus, ServiceHealth } from '../../types/error'

export function useHealthServices() {
  // services 为 null = 首拉未完成（消费方按「未知」处理，不渲染异常态避免闪红）
  const [services, setServices] = useState<Record<string, ServiceHealth> | null>(null)
  // overall 异常/拉取失败（红条渲染条件；状态点只看 services 段）
  const [down, setDown] = useState(false)

  const refetch = useCallback(async () => {
    try {
      const r = (await window.electronAPI.invoke('system:getHealth')) as HealthStatus
      setDown(r?.status !== 'ok')
      setServices(r?.services ?? {})
    } catch {
      // Health check failed — treat as down（沿用原 HomeServiceBanner 语义）
      setDown(true)
      setServices({})
    }
  }, [])

  useEffect(() => { void refetch() }, [refetch])

  useEffect(() => {
    const refetchOnEdge = () => { void refetch() }
    window.addEventListener('app:health-changed', refetchOnEdge)
    window.addEventListener('focus', refetchOnEdge)
    return () => {
      window.removeEventListener('app:health-changed', refetchOnEdge)
      window.removeEventListener('focus', refetchOnEdge)
    }
  }, [refetch])

  return { services, down, refetch }
}
