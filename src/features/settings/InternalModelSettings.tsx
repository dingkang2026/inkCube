import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { ModelStatusInfo } from '../../types'
import styles from './InternalModelSettings.module.css'

const CATEGORY_ORDER = ['embedding', 'document_parsing', 'ocr'] as const
const POLL_INTERVAL = 1000

interface LibreOfficeStatus {
  available: boolean
  tool: 'ms-office' | 'libreoffice' | null
  path: string | null
}

export function InternalModelSettings() {
  const { t } = useTranslation()
  const [models, setModels] = useState<Record<string, ModelStatusInfo>>({})
  const [loading, setLoading] = useState(true)
  const [downloadingAll, setDownloadingAll] = useState(false)
  const [libreoffice, setLibreOffice] = useState<LibreOfficeStatus | null>(null)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = (await window.electronAPI.invoke('models:status')) as Record<string, ModelStatusInfo>
      setModels(res)
      setLoading(false)

      const anyDownloading = Object.values(res).some((m) => m.downloading)
      if (!anyDownloading && pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
        setDownloadingAll(false)
      }
    } catch {
      setLoading(false)
    }
  }, [])

  const fetchLibreOfficeStatus = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('models:libreoffice-status')
      setLibreOffice(res as LibreOfficeStatus)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    fetchLibreOfficeStatus()
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [fetchStatus, fetchLibreOfficeStatus])

  const startPolling = () => {
    if (pollingRef.current) return
    pollingRef.current = setInterval(fetchStatus, POLL_INTERVAL)
  }

  const handleDownload = async (name: string) => {
    await window.electronAPI.invoke('models:download', name)
    startPolling()
  }

  const handleDownloadAll = async () => {
    setDownloadingAll(true)
    await window.electronAPI.invoke('models:download-all')
    startPolling()
  }

  const grouped = CATEGORY_ORDER.map((cat) => ({
    category: cat,
    label: t(`settings.internalModel.category.${cat}`),
    items: Object.values(models).filter((m) => m.category === cat),
  }))

  const allDownloaded =
    Object.values(models).length > 0 &&
    Object.values(models).every((m) => m.downloaded) &&
    !Object.values(models).some((m) => m.downloading)

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div>
          <h3 className={styles.title}>{t('settings.internalModel.title')}</h3>
          <p className={styles.description}>{t('settings.internalModel.description')}</p>
        </div>
        <div className={styles.headerActions}>
          <button className="btn btn-secondary btn-sm" onClick={fetchStatus}>
            {t('settings.internalModel.refresh')}
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleDownloadAll}
            disabled={downloadingAll || allDownloaded}
          >
            {downloadingAll ? t('settings.internalModel.downloading') : t('settings.internalModel.downloadAll')}
          </button>
        </div>
      </div>

      {loading && Object.keys(models).length === 0 ? (
        <div className={styles.loading}>{t('settings.internalModel.verifying')}</div>
      ) : (
        <div className={styles.groups}>
          {/* LibreOffice status card */}
          {libreoffice && (
            <div className={styles.group}>
              <div className={styles.groupLabel}>{t('settings.internalModel.libreOffice.title')}</div>
              <div className={styles.modelCard}>
                <div className={styles.modelInfo}>
                  <span className={styles.modelName}>{t('settings.internalModel.libreOffice.name')}</span>
                  <span className={styles.modelMeta}>
                    {libreoffice.tool === 'ms-office'
                      ? t('settings.internalModel.libreOffice.msOffice')
                      : libreoffice.tool === 'libreoffice'
                      ? t('settings.internalModel.libreOffice.libreOffice')
                      : t('settings.internalModel.libreOffice.notInstalled')}
                  </span>
                </div>
                <div className={styles.modelStatus}>
                  <span className={`${styles.statusBadge} ${!libreoffice.available ? styles.missing : ''}`}>
                    {libreoffice.available
                      ? t('settings.internalModel.libreOffice.installed')
                      : t('settings.internalModel.libreOffice.installPrompt')}
                  </span>
                </div>
              </div>
            </div>
          )}

          {grouped.map(({ category, label, items }) =>
            items.length > 0 ? (
              <div key={category} className={styles.group}>
                <div className={styles.groupLabel}>{label}</div>
                {items.map((model) => (
                  <ModelCard
                    key={model.name}
                    model={model}
                    onDownload={() => handleDownload(model.name)}
                    t={t}
                  />
                ))}
              </div>
            ) : null,
          )}
        </div>
      )}

      <div className={styles.footer}>{t('settings.internalModel.totalSize')}</div>
    </div>
  )
}

function ModelCard({
  model,
  onDownload,
  t,
}: {
  model: ModelStatusInfo
  onDownload: () => void
  t: (key: string) => string
}) {
  const isBundled = model.install_type === 'bundled'
  const isDownloading = model.downloading
  const isDownloaded = model.downloaded
  const progress = model.progress || 0
  const hasError = !!model.error

  return (
    <div className={styles.modelCard}>
      <div className={styles.modelInfo}>
        <span className={styles.modelName}>
          {t(`settings.internalModel.modelNames.${model.name}`)}
        </span>
        <span className={styles.modelMeta}>
          {model.size} &middot; {model.framework}
          {isBundled && <> &middot; {t('settings.internalModel.bundled')}</>}
        </span>
      </div>
      <div className={styles.modelStatus}>
        {isBundled ? (
          <span className={`${styles.statusBadge} ${!isDownloaded ? styles.missing : ''}`}>
            {isDownloaded ? t('settings.internalModel.bundledInstalled') : t('settings.internalModel.bundledMissing')}
          </span>
        ) : hasError ? (
          <div className={styles.errorRow}>
            <span className={styles.errorText}>{model.error}</span>
            <button className="btn btn-secondary btn-sm" onClick={onDownload}>
              {t('settings.internalModel.download')}
            </button>
          </div>
        ) : isDownloading ? (
          <div className={styles.progressWrap}>
            <div className={styles.progressBar}>
              <div className={styles.progressFill} style={{ width: `${progress}%` }} />
            </div>
            <span className={styles.progressText}>{progress}%</span>
          </div>
        ) : isDownloaded ? (
          <span className={styles.statusBadge}>{t('settings.internalModel.downloaded')}</span>
        ) : (
          <button className="btn btn-secondary btn-sm" onClick={onDownload}>
            {t('settings.internalModel.download')}
          </button>
        )}
      </div>
    </div>
  )
}
