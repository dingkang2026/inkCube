import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, XCircle } from 'lucide-react'
import styles from './ExternalDepsSettings.module.css'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import { MODELS_IPC } from '../../types'

interface DepStatus {
  available: boolean
  path: string | null
  version?: string
}

interface LibreOfficeStatus {
  available: boolean
  tool: 'ms-office' | 'libreoffice' | null
  path: string | null
}

export function ExternalDepsSettings() {
  const { t } = useTranslation()
  const handleError = useErrorHandler()
  const [deps, setDeps] = useState<Record<string, DepStatus>>({})
  const [libreoffice, setLibreOffice] = useState<LibreOfficeStatus | null>(null)
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

  // LibreOffice 走模型管理器注册表精确检测（ms-office 优先、LibreOffice 兼底的
  // 文档解析降级链），与 git/ffmpeg 的 PATH 检测不同源；2026-10-19 自内部模型页迁入
  const fetchLibreOffice = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke(MODELS_IPC.libreofficeStatus)
      setLibreOffice(res as LibreOfficeStatus)
    } catch (err) {
      handleError(err)
    }
  }, [handleError])

  useEffect(() => {
    fetchDeps()
    fetchLibreOffice()
  }, [fetchDeps, fetchLibreOffice])

  // pandoc 已删：实际消费走 tool_resolver 捆绑版（随应用分发必有）；
  // libreoffice 单独行渲染（检测源不同），见下方列表
  const depNames: { key: string; label: string; description: string }[] = [
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
              <div className={styles.itemIcon}>{available ? <CheckCircle2 size={16} /> : <XCircle size={16} />}</div>
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

        {/* LibreOffice 行（模型管理器注册表检测：ms-office 优先，LibreOffice 兼底） */}
        <div className={`${styles.item} ${libreoffice?.available ? styles.ok : styles.missing}`}>
          <div className={styles.itemIcon}>{libreoffice?.available ? <CheckCircle2 size={16} /> : <XCircle size={16} />}</div>
          <div className={styles.itemInfo}>
            <span className={styles.itemName}>LibreOffice</span>
            <span className={styles.itemDesc}>{t('task.depDescLibreoffice')}</span>
            {libreoffice?.tool && (
              <span className={styles.itemVersion}>
                {libreoffice.tool === 'ms-office'
                  ? t('task.libreOffice.msOffice')
                  : t('task.libreOffice.libreOffice')}
              </span>
            )}
          </div>
          <span className={styles.itemStatus}>
            {libreoffice?.available ? t('task.depDetected') : t('task.depNotDetected')}
          </span>
        </div>
      </div>
    </div>
  )
}
