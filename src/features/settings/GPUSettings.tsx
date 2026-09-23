import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import type { GpuStatus } from '../../types'
import styles from './GPUSettings.module.css'

const POLL_INTERVAL = 3000

export function GPUSettings() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<GpuStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [downloading, setDownloading] = useState(false)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('models:gpu-status')
      setStatus(res as GpuStatus)
      return res as GpuStatus
    } catch {
      return null
    }
  }, [])

  useEffect(() => {
    fetchStatus().then(() => setLoading(false))
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [fetchStatus])

  const startPolling = useCallback(() => {
    if (pollingRef.current) return
    pollingRef.current = setInterval(async () => {
      const s = await fetchStatus()
      if (s?.mode === 'gpu') {
        setDownloading(false)
        if (pollingRef.current) clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }, POLL_INTERVAL)
  }, [fetchStatus])

  const handleDownloadGpu = async () => {
    await window.electronAPI.invoke('models:gpu-download')
    setDownloading(true)
    startPolling()
  }

  if (loading) {
    return <div className={styles.loading}>{t('settings.internalModel.verifying')}</div>
  }

  const isGpu = status?.mode === 'gpu'

  return (
    <div className={styles.container}>
      <h3 className={styles.title}>{t('settings.gpu.title')}</h3>
      <p className={styles.description}>{t('settings.gpu.description')}</p>

      <div className={styles.modeCard}>
        <div className={styles.modeHeader}>
          <span className={styles.modeLabel}>{t('settings.gpu.currentMode')}</span>
          <span className={`${styles.modeBadge} ${isGpu ? styles.gpu : styles.cpu}`}>
            {isGpu ? t('settings.gpu.gpuMode') : t('settings.gpu.cpuMode')}
          </span>
        </div>
        <p className={styles.modeDesc}>
          {isGpu ? t('settings.gpu.gpuDescription') : t('settings.gpu.cpuDescription')}
        </p>
      </div>

      {!isGpu && (
        <div className={styles.downloadSection}>
          <div className={styles.downloadInfo}>
            <span className={styles.downloadSize}>{t('settings.gpu.gpuSize')}</span>
            <span className={styles.downloadReq}>{t('settings.gpu.requirement')}</span>
          </div>
          {downloading ? (
            <span className={styles.downloadingBadge}>{t('settings.gpu.downloading')}</span>
          ) : (
            <button className="btn btn-primary btn-sm" onClick={handleDownloadGpu}>
              {t('settings.gpu.downloadGpu')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
