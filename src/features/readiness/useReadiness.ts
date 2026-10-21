/** src/features/readiness/useReadiness.ts — 功能就绪度前端缓存与入口 guard（0.4lifecycle-design.md §5.3 第二层）。
 *
 * 数据源复用 system:getHealth（不新增轮询）：模块级缓存 + TTL，
 * guard 触发时缓存过期则先刷新再判定。判定策略：
 * - 就绪 / 数据不可得（服务 down、未知 feature）→ 放行（第三层后端 ensure_ready 兜底）
 * - 缺必需依赖 → 引导弹层（「去配置 / 去下载」锚点直达设置页）+ 取消中止
 * - 仅缺可选依赖（VLM）→ 提示条 + 放行（缺失降级，不阻断）
 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useConfirm } from '../../components/ConfirmDialog'
import { useToast } from '../knowledge/Toast'
import type { FeatureKey, FeatureReadiness, HealthStatus } from '../../types/error'

const CACHE_TTL = 30_000

/** action 锚点编码（后端 readiness.py）→ 设置页 section */
export function settingsSectionOfAction(action: string): string {
  if (action === 'settings:external') return 'externalModel'
  if (action === 'settings:internal') return 'internalModel'
  if (action === 'settings:webSearch') return 'webSearch'
  return 'general'
}

/** 统一的设置页直达导航（带锚点 section；App.tsx 监听 app:navigate-settings） */
export function navigateToSettings(section?: string) {
  window.dispatchEvent(
    new CustomEvent('app:navigate-settings', section ? { detail: { section } } : undefined),
  )
}

// 模块级缓存：跨组件共享，功能入口检查读缓存（0.4 §7.2「不新增轮询」）
let cache: Record<string, FeatureReadiness> | null = null
let cacheAt = 0
let inflight: Promise<Record<string, FeatureReadiness> | null> | null = null

async function fetchFeatures(force = false): Promise<Record<string, FeatureReadiness> | null> {
  if (!force && cache && Date.now() - cacheAt < CACHE_TTL) return cache
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const res = (await window.electronAPI.invoke('system:getHealth')) as HealthStatus
      cache = res.features ?? null
      cacheAt = Date.now()
      return cache
    } catch {
      return null
    } finally {
      inflight = null
    }
  })()
  return inflight
}

/** 供外部主动失效（如设置保存成功后），下次 guard 判定前会重新拉取 */
export function invalidateReadiness() {
  cache = null
}

/** 就绪度数据 hook：挂载时读缓存/按需拉取；refresh 强制刷新 */
export function useReadiness() {
  const [features, setFeatures] = useState<Record<string, FeatureReadiness> | null>(cache)

  useEffect(() => {
    let cancelled = false
    void fetchFeatures().then((f) => {
      if (!cancelled && f) setFeatures(f)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const refresh = useCallback(async () => {
    const f = await fetchFeatures(true)
    if (f) setFeatures(f)
    return f
  }, [])

  return { features, refresh }
}

/**
 * 入口 guard hook（0.4 §7.3 第二层）：缺必需 → 引导弹层；仅缺可选 → 提示条放行。
 * 返回 Promise<void>，onProceed 只在确认放行时被调用一次。
 */
export function useFeatureGuard() {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()

  return useCallback(
    async (feature: FeatureKey, onProceed: () => void): Promise<void> => {
      let feats = cache
      if (!feats || Date.now() - cacheAt >= CACHE_TTL) {
        feats = await fetchFeatures()
      }
      const info = feats?.[feature]
      // 数据不可得（服务 down / 未知 feature）→ 放行，后端 ensure_ready 兜底
      if (!info) {
        onProceed()
        return
      }
      if (info.ready) {
        onProceed()
        return
      }
      if (info.missing.length > 0) {
        const miss = info.missing[0]
        const ok = await confirmDialog({
          title: t('readiness.dialogTitle'),
          message: t('readiness.dialogMessage', {
            feature: t(`readiness.feature.${feature}`),
            dep: t(`readiness.dep.${miss.id}`),
          }),
          confirmText: miss.kind === 'model' ? t('readiness.goDownload') : t('readiness.goConfigure'),
          cancelText: t('common.cancel'),
          danger: false,
        })
        if (ok) navigateToSettings(settingsSectionOfAction(miss.action))
        return
      }
      // 仅缺可选依赖（VLM）→ 提示条 + 放行（0.4 §7.3）
      if (info.optional_missing.length > 0) {
        showToast(t('readiness.optionalVlm'), 'warning')
      }
      onProceed()
    },
    [confirmDialog, showToast, t],
  )
}
