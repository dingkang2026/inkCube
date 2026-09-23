/** src/features/settings/DataManagement.tsx — Data management panel.
 *
 * Shows disk usage by category and allows selective cleanup.
 * Integrated into SettingsPage under the "dataManagement" section.
 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import s from './DataManagement.module.css'

interface CategoryUsage {
  size: number
  count: number
  message_count?: number
  file_count?: number
  image_count?: number
}

interface DataUsage {
  data_dir: string
  categories: Record<string, CategoryUsage>
  total_size: number
}

const CATEGORIES = [
  { key: 'logs', icon: '📋', colorClass: 'logs' },
  { key: 'conversations', icon: '💬', colorClass: 'conversations' },
  { key: 'knowledge', icon: '📚', colorClass: 'knowledge' },
  { key: 'settings', icon: '⚙️', colorClass: 'settings' },
  { key: 'models', icon: '🤖', colorClass: 'models' },
] as const

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  const val = bytes / Math.pow(1024, i)
  return `${val.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

export function DataManagement() {
  const { t } = useTranslation()
  const [usage, setUsage] = useState<DataUsage | null>(null)
  const [loading, setLoading] = useState(true)
  const [cleaning, setCleaning] = useState<string | null>(null)

  const fetchUsage = useCallback(async () => {
    try {
      const result = await window.electronAPI.invoke('system:getDataUsage') as DataUsage
      setUsage(result)
    } catch (err) {
      console.error('Failed to fetch data usage:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchUsage()
  }, [fetchUsage])

  const handleClean = async (category: string) => {
    const confirmMsg = t(`data.confirmClean_${category}`)
    if (!window.confirm(confirmMsg)) return

    // Extra confirmation for high-risk categories
    if (category === 'knowledge' || category === 'models') {
      const doubleConfirm = window.confirm(t('data.doubleConfirm'))
      if (!doubleConfirm) return
    }

    setCleaning(category)
    try {
      const result = await window.electronAPI.invoke('system:cleanData', category) as { success?: boolean; partial?: boolean; message?: string }
      if (!result.success) {
        // Log technical details to console, show user-friendly message
        console.warn(`Failed to clean ${category}:`, result.message)
        alert(t('data.cleanFailed'))
      } else if (result.partial) {
        // Some files couldn't be deleted (e.g., locked on Windows)
        console.warn(`Partial clean for ${category}:`, result.message)
        alert(t('data.cleanPartial'))
      }
      await fetchUsage()
    } catch (err) {
      console.error(`Failed to clean ${category}:`, err)
      alert(t('data.cleanFailed'))
    } finally {
      setCleaning(null)
    }
  }

  const handleResetAll = async () => {
    if (!window.confirm(t('data.confirmResetAll'))) return
    if (!window.confirm(t('data.doubleConfirmReset'))) return

    setCleaning('all')
    try {
      // Clean in order: conversations → knowledge → settings → models → logs
      for (const cat of ['conversations', 'knowledge', 'settings', 'models', 'logs']) {
        const result = await window.electronAPI.invoke('system:cleanData', cat) as { success?: boolean; message?: string }
        if (!result.success) {
          console.warn(`Failed to clean ${cat}:`, result.message)
          // Show user-friendly message with category name
          alert(`${t('data.cleanFailed')}: ${t(`data.category_${cat}`)}`)
          break
        }
      }
      await fetchUsage()
    } catch (err) {
      console.error('Failed to reset all:', err)
      alert(t('data.cleanFailed'))
    } finally {
      setCleaning(null)
    }
  }

  const handleOpenDir = async () => {
    try {
      await window.electronAPI.invoke('system:openDataDir')
    } catch (err) {
      console.error('Failed to open data dir:', err)
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
        {CATEGORIES.map(({ key, icon, colorClass }) => {
          const cat = usage.categories[key]
          if (!cat) return null
          const desc = getCategoryDesc(t, key, cat)
          return (
            <div key={key} className={s.category}>
              <div className={`${s.categoryIcon} ${s[colorClass]}`}>{icon}</div>
              <div className={s.categoryInfo}>
                <div className={s.categoryName}>{t(`data.category_${key}`)}</div>
                <div className={s.categoryDesc}>{desc}</div>
              </div>
              <div className={s.categorySize}>{formatBytes(cat.size)}</div>
              <button
                className={s.cleanBtn}
                onClick={() => handleClean(key)}
                disabled={cleaning !== null || cat.size === 0}
              >
                {cleaning === key ? t('data.cleaning') : t('data.clean')}
              </button>
            </div>
          )
        })}
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
    case 'logs':
      return t('data.desc_logs')
    case 'conversations':
      return t('data.desc_conversations', { count: cat.count, msgCount: cat.message_count || 0 })
    case 'knowledge':
      return t('data.desc_knowledge', {
        projects: cat.count,
        files: cat.file_count || 0,
        images: cat.image_count || 0,
      })
    case 'settings':
      return t('data.desc_settings')
    case 'models':
      return t('data.desc_models')
    default:
      return ''
  }
}
