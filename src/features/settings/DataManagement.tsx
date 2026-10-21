/** src/features/settings/DataManagement.tsx — Data management panel.
 *
 * Shows disk usage by category (后端 9 类，cleanable 标记哪些可整体清理；
 * uv_cache/fetched 目录不存在时后端不返回键，此处不渲染零值行)
 * and allows selective cleanup; retention policy inputs ride the SettingsPage
 * auto-save chain (form/onUpdate，启动期由 maintenance 执行清盘).
 * Integrated into SettingsPage under the "dataManagement" section.
 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive, BookOpen, Bot, Boxes, ClipboardList, FileText, Globe, Package,
  Settings2, type LucideIcon,
} from 'lucide-react'
import type { LLMSettings } from '../../types'
import s from './DataManagement.module.css'
import { useConfirm } from '../../components/ConfirmDialog'
import { useToast } from '../knowledge/Toast'
import { useErrorHandler } from '../../hooks/useErrorHandler'

interface CategoryUsage {
  size: number
  count: number
  file_count?: number
  image_count?: number
  /** false = 不可整体清理（reports/extras），只展示占用，清理走各自功能页 */
  cleanable?: boolean
}

interface DataUsage {
  data_dir: string
  categories: Record<string, CategoryUsage>
  total_size: number
}

interface DataManagementProps {
  form: LLMSettings
  onUpdate: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
  /** settings 表被清空（单类清理或全清成功）后回灌：不调则旧 form 经
   *  800ms 自动保存把旧配置全量写回，清理静默失效（B2 复活竞态） */
  onSettingsReset: () => void
}

// 分类图标用 lucide；浅色底功能色标由 colorClass 提供（module.css 内分类色，全站唯一使用处）。
// 顺序：可整体清理的在前（主要操作对象），reports/extras 逐条清理类殿后；
// uv_cache/fetched 目录不存在时后端不返回该键，此处不渲染零值行
const CATEGORIES: { key: string; icon: LucideIcon; colorClass: string }[] = [
  { key: 'tasks', icon: Archive, colorClass: 'tasks' },
  { key: 'knowledge', icon: BookOpen, colorClass: 'knowledge' },
  { key: 'models', icon: Bot, colorClass: 'models' },
  { key: 'uv_cache', icon: Boxes, colorClass: 'uv_cache' },
  { key: 'fetched', icon: Globe, colorClass: 'fetched' },
  { key: 'logs', icon: FileText, colorClass: 'logs' },
  { key: 'settings', icon: Settings2, colorClass: 'settings' },
  { key: 'reports', icon: ClipboardList, colorClass: 'reports' },
  { key: 'extras', icon: Package, colorClass: 'extras' },
]

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  const val = bytes / Math.pow(1024, i)
  return `${val.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

export function DataManagement({ form, onUpdate, onSettingsReset }: DataManagementProps) {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()
  const handleError = useErrorHandler()
  const [usage, setUsage] = useState<DataUsage | null>(null)
  const [loading, setLoading] = useState(true)
  const [cleaning, setCleaning] = useState<string | null>(null)

  const fetchUsage = useCallback(async () => {
    try {
      const result = await window.electronAPI.invoke('system:getDataUsage') as DataUsage
      setUsage(result)
    } catch (err) {
      handleError(err)
    } finally {
      setLoading(false)
    }
  }, [handleError])

  useEffect(() => {
    fetchUsage()
  }, [fetchUsage])

  const handleClean = async (category: string) => {
    const confirmMsg = t(`data.confirmClean_${category}`)
    if (!(await confirmDialog({ title: t('data.clean'), message: confirmMsg }))) return

    // Extra confirmation for high-risk categories
    if (category === 'knowledge' || category === 'models' || category === 'tasks') {
      const doubleConfirm = await confirmDialog({ title: t('data.clean'), message: t('data.doubleConfirm') })
      if (!doubleConfirm) return
    }

    setCleaning(category)
    try {
      const result = await window.electronAPI.invoke('system:cleanData', category) as { success?: boolean; partial?: boolean; code?: string; message?: string }
      if (!result.success) {
        // 下载中拒绝（409 门语义）：提示等待而非笼统失败
        showToast(
          result.code === 'MODEL_DOWNLOAD_IN_PROGRESS' ? t('data.cleanModelsDownloading') : t('data.cleanFailed'),
          'error',
          { detail: result.message },
        )
      } else {
        if (result.partial) {
          // Some files couldn't be deleted (e.g., locked on Windows)
          showToast(t('data.cleanPartial'), 'warning')
        }
        if (category === 'settings') onSettingsReset()
      }
      await fetchUsage()
    } catch (err) {
      handleError(err)
    } finally {
      setCleaning(null)
    }
  }

  const handleResetAll = async () => {
    if (!(await confirmDialog({ title: t('data.resetAll'), message: t('data.confirmResetAll') }))) return
    if (!(await confirmDialog({ title: t('data.resetAll'), message: t('data.doubleConfirmReset') }))) return

    setCleaning('all')
    try {
      // 真·全清：后端单事务清全表 + data_dir 磁盘，IPC 层联动清网页登录态
      const result = await window.electronAPI.invoke('system:resetAllData') as { success?: boolean; partial?: boolean; code?: string; message?: string }
      if (!result.success) {
        showToast(
          result.code === 'RESET_BLOCKED_ACTIVE_WORK'
            ? t('data.resetBlockedActive')
            : result.code === 'MODEL_DOWNLOAD_IN_PROGRESS'
              ? t('data.cleanModelsDownloading')
              : t('data.cleanFailed'),
          'error',
          { detail: result.message },
        )
      } else {
        if (result.partial) showToast(t('data.cleanPartial'), 'warning')
        // settings 表已清空：先回灌再提示，防旧 form 复活写回（B2）
        onSettingsReset()
        showToast(t('data.resetDone'), 'success')
      }
      await fetchUsage()
    } catch (err) {
      handleError(err)
    } finally {
      setCleaning(null)
    }
  }

  const handleOpenDir = async () => {
    try {
      const result = await window.electronAPI.invoke('system:openDataDir') as { success?: boolean; message?: string }
      // openPath 失败（路径无效/无权限）返回错误字符串（electron 侧转 success:false）
      if (result && result.success === false) {
        showToast(t('data.openDirFailed'), 'error', { detail: result.message })
      }
    } catch (err) {
      handleError(err)
    }
  }

  if (loading) {
    return <div className={s.loading}>{t('data.loading')}</div>
  }

  if (!usage) {
    return <div className={s.loading}>{t('data.loadFailed')}</div>
  }

  return (
    <div className={s.container}>
      {/* Data directory */}
      <div className={s.dataDir}>
        <span className={s.dataDirPath} title={usage.data_dir}>{usage.data_dir}</span>
        <button className={s.openDirBtn} onClick={handleOpenDir}>
          {t('data.openDir')}
        </button>
        <button className={s.refreshBtn} onClick={fetchUsage}>
          {t('data.refresh')}
        </button>
      </div>

      {/* Total size */}
      <div className={s.totalSize}>
        <span>{t('data.totalUsage')}</span>
        <span>{formatBytes(usage.total_size)}</span>
      </div>

      {/* Category list */}
      <div className={s.categoryList}>
        {CATEGORIES.map(({ key, icon: Icon, colorClass }) => {
          const cat = usage.categories[key]
          if (!cat) return null
          const desc = getCategoryDesc(t, key, cat)
          return (
            <div key={key} className={s.category}>
              <div className={`${s.categoryIcon} ${s[colorClass]}`}><Icon size={16} /></div>
              <div className={s.categoryInfo}>
                <div className={s.categoryName}>{t(`data.category_${key}`)}</div>
                <div className={s.categoryDesc}>{desc}</div>
              </div>
              <div className={s.categorySize}>{formatBytes(cat.size)}</div>
              {cat.cleanable === false ? (
                <span className={s.notCleanable}>{t('data.notCleanable')}</span>
              ) : (
                <button
                  className={s.cleanBtn}
                  onClick={() => handleClean(key)}
                  disabled={cleaning !== null || cat.size === 0}
                >
                  {cleaning === key ? t('data.cleaning') : t('data.clean')}
                </button>
              )}
            </div>
          )
        })}
      </div>

      {/* 保留策略（2026-10-20 存储审计）：天数改动走 SettingsPage 800ms 自动保存，
          下次启动由 maintenance 后台任务按新保留期执行清盘 */}
      <div className={s.retentionSection}>
        <div className={s.retentionTitle}>{t('data.retentionTitle')}</div>
        <div className={s.retentionDesc}>{t('data.retentionDesc')}</div>
        {(
          [
            ['archived_task_cleanup_days', 'data.retentionArchived', 30],
            ['llm_usage_retention_days', 'data.retentionUsage', 90],
            ['schedule_runs_retention_days', 'data.retentionRuns', 90],
          ] as const
        ).map(([key, labelKey, fallback]) => (
          <div key={key} className={s.retentionRow}>
            <label className={s.retentionLabel} htmlFor={`retention-${key}`}>
              {t(labelKey)}
            </label>
            <div className={s.retentionInputWrap}>
              <input
                id={`retention-${key}`}
                type="number"
                min={0}
                max={3650}
                className={s.retentionInput}
                value={form[key] ?? fallback}
                onChange={(e) => onUpdate(key, parseInt(e.target.value) || 0)}
              />
              <span className={s.retentionUnit}>{t('data.retentionDays')}</span>
            </div>
          </div>
        ))}
        <div className={s.retentionHint}>{t('data.retentionHint')}</div>
      </div>

      {/* Reset all */}
      <div className={s.divider} />
      <div className={s.resetSection}>
        <div className={s.resetTitle}>{t('data.resetAllTitle')}</div>
        <div className={s.resetDesc}>{t('data.resetAllDesc')}</div>
        <button
          className={s.resetBtn}
          onClick={handleResetAll}
          disabled={cleaning !== null}
        >
          {cleaning === 'all' ? t('data.resetting') : t('data.resetAll')}
        </button>
      </div>
    </div>
  )
}

function getCategoryDesc(t: (key: string, opts?: Record<string, unknown>) => string, key: string, cat: CategoryUsage): string {
  switch (key) {
    case 'tasks':
      // count = 归档任务数（后端口径：清理只动归档工作区，活跃任务永不触碰）
      return t('data.desc_tasks', { archived: cat.count })
    case 'knowledge':
      return t('data.desc_knowledge', {
        projects: cat.count,
        files: cat.file_count || 0,
        images: cat.image_count || 0,
      })
    case 'reports':
      return t('data.desc_reports', { count: cat.count })
    case 'extras':
      return t('data.desc_extras')
    case 'uv_cache':
      return t('data.desc_uv_cache')
    case 'fetched':
      return t('data.desc_fetched')
    case 'logs':
      return t('data.desc_logs')
    case 'settings':
      return t('data.desc_settings')
    case 'models':
      return t('data.desc_models')
    default:
      return ''
  }
}
