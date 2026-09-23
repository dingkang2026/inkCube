import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import styles from './ExternalDepsSettings.module.css'

interface DepStatus {
  available: boolean
  path: string | null
  version?: string
}

export function ExternalDepsSettings() {
  const { t } = useTranslation()
  const [deps, setDeps] = useState<Record<string, DepStatus>>({})
  const [loading, setLoading] = useState(true)

  const fetchDeps = useCallback(async () => {
    setLoading(true)
    try {
      const res = await window.electronAPI.invoke('system:checkDeps') as { deps: Record<string, DepStatus> }
      setDeps(res.deps || {})
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchDeps()
  }, [fetchDeps])

  const depNames: { key: string; label: string; description: string }[] = [
    { key: 'pandoc', label: 'Pandoc', description: '文档格式转换 (docx ↔ md ↔ html)' },
    { key: 'libreoffice', label: 'LibreOffice', description: 'Office 文档转换 (pptx → pdf)' },
    { key: 'git', label: 'Git', description: '版本控制 & Skill 安装' },
    { key: 'ffmpeg', label: 'FFmpeg', description: '音视频处理' },
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
