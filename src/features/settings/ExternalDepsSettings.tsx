import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import styles from './ExternalDepsSettings.module.css'
import { useErrorHandler } from '../../hooks/useErrorHandler'

interface DepStatus {
  available: boolean
  path: string | null
  version?: string
}

export function ExternalDepsSettings() {
  const { t } = useTranslation()
  const handleError = useErrorHandler()
  const [deps, setDeps] = useState<Record<string, DepStatus>>({})
  const [loading, setLoading] = useState(true)

  const fetchDeps = useCallback(async () => {
    setLoading(true)
    try {
      const res = await window.electronAPI.invoke('system:checkDeps') as { deps: Record<string, DepStatus> }
      setDeps(res.deps || {})
    } catch (err) {
      // 检测失败 ≠ 未安装：静默会把所有依赖误标为「未检测到」
      handleError(err)
    } finally {
      setLoading(false)
    }
  }, [handleError])

  useEffect(() => {
    fetchDeps()
  }, [fetchDeps])

  const depNames: { key: string; label: string; description: string }[] = [
    { key: 'pandoc', label: 'Pandoc', description: t('task.depDescPandoc') },
    { key: 'libreoffice', label: 'LibreOffice', description: t('task.depDescLibreoffice') },
    { key: 'git', label: 'Git', description: t('task.depDescGit') },
    { key: 'ffmpeg', label: 'FFmpeg', description: t('task.depDescFfmpeg') },
  ]

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h3 className={styles.title}>{t('task.externalDeps')}</h3>
        <p className={styles.description}>{t('task.externalDepsDesc')}</p>
        <button className={styles.refreshBtn} onClick={fetchDeps} disabled={loading}>
          {loading ? '...' : t('task.depRefresh')}
        </button>
      </div>

      <div className={styles.list}>
        {depNames.map(({ key, label, description }) => {
          const status = deps[key]
          const available = status?.available ?? false
          return (
            <div key={key} className={`${styles.item} ${available ? styles.ok : styles.missing}`}>
              <div className={styles.itemIcon}>{available ? '✅' : '❌'}</div>
              <div className={styles.itemInfo}>
                <span className={styles.itemName}>{label}</span>
                <span className={styles.itemDesc}>{description}</span>
                {available && status?.version && (
                  <span className={styles.itemVersion}>{status.version}</span>
                )}
                {available && status?.path && (
                  <span className={styles.itemPath}>{status.path}</span>
                )}
              </div>
              <span className={styles.itemStatus}>
                {available ? t('task.depDetected') : t('task.depNotDetected')}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
