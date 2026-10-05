import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import type { GpuStatus } from '../../types'
import { usePolling } from '../../hooks/usePolling'
import { useActivity } from '../activity/ActivityContext'
import styles from './GPUSettings.module.css'

const POLL_INTERVAL = 3000

export function GPUSettings() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<GpuStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [downloading, setDownloading] = useState(false)
  // enabled 谓词从全局心跳派生（0.4lifecycle-design.md §6.2）：GPU 后端下载中时轮询明细，
  // 成功/失败终态均自停（修复旧 pollUntilGpu 失败永转）；
  // 启动间隙由触发动作后的一次立即拉取接上（见 handleDownloadGpu）
  const { items: modelItems } = useActivity('model_download')
  const gpuActive = modelItems.some((i) => i.entity_id === 'gpu-backend')

  const fetchStatus = useCallback(async () => {
    // 抛错由 usePolling 计入连续失败并退避（0.4 §5.8）；初始加载侧 catch 保底
    const res = await window.electronAPI.invoke('models:gpu-status')
    setStatus(res as GpuStatus)
  }, [])

  useEffect(() => {
    fetchStatus().catch(() => null).then(() => setLoading(false))
  }, [fetchStatus])

  usePolling({
    name: 'gpu-status',
    enabled: gpuActive,
    interval: POLL_INTERVAL,
    task: fetchStatus,
  })

  // 终态边沿（全局 active 消失）时复位按钮并补拉一次：
  // marker 写入与 _downloads 清位之间的窗口可能被轮询间隔跨过，
  // 停轮前最后一次拉取可能仍拿到 cpu，这里补拉确保 mode 终态不遭漏
  useEffect(() => {
    if (!gpuActive) {
      setDownloading(false)
      void fetchStatus().catch(() => null)
    }
  }, [gpuActive, fetchStatus])

  const handleDownloadGpu = async () => {
    await window.electronAPI.invoke('models:gpu-download')
    setDownloading(true)
    // 后台线程已同步置 downloading，立即拉一次接上启动间隙
    await fetchStatus().catch(() => null)
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
