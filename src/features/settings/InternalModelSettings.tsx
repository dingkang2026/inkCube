import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import type { ModelStatusInfo } from '../../types'
import { MODELS_IPC } from '../../types'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { useActivity } from '../activity/ActivityContext'
import { useErrorHandler } from '../../hooks/useErrorHandler'
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
  const handleError = useErrorHandler()
  const [models, setModels] = useState<Record<string, ModelStatusInfo>>({})
  const [loading, setLoading] = useState(true)
  const [downloadingAll, setDownloadingAll] = useState(false)
  const [libreoffice, setLibreOffice] = useState<LibreOfficeStatus | null>(null)
  // enabled 谓词从全局心跳派生（0.4lifecycle-design.md §6.2）：存在下载中模型时轮询明细，
  // 全部终态后自停；启动间隙由触发动作后的一次立即拉取接上（见 handleDownload）
  const { items: modelItems } = useActivity('model_download')
  const modelsActive = modelItems.length > 0

  const fetchStatus = useCallback(async () => {
    // 抛错由 usePolling 计入连续失败并退避（0.4 §5.8）；初始加载侧 catch 兜底
    const res = (await window.electronAPI.invoke(MODELS_IPC.status)) as Record<string, ModelStatusInfo>
    setModels(res)
    setLoading(false)

    if (!Object.values(res).some((m) => m.downloading)) {
      setDownloadingAll(false)
    }
  }, [])

  const fetchLibreOfficeStatus = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke(MODELS_IPC.libreofficeStatus)
      setLibreOffice(res as LibreOfficeStatus)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    fetchStatus().catch((err) => {
      setLoading(false)
      handleError(err)
    })
    fetchLibreOfficeStatus()
  }, [fetchStatus, fetchLibreOfficeStatus, handleError])

  usePolling({
    name: 'internal-model-status',
    enabled: modelsActive,
    interval: POLL_INTERVAL,
    task: fetchStatus,
  })

  // 终态补拉（0.4 §6.3）：轮询随活动消失自停，最后一次快照可能停在完成前（toast 走
  // 通知推送先于 status 上屏）→ modelsActive true→false 边沿补拉一次，立即渲染终态
  useEdgeRefetch(modelsActive, () => { void fetchStatus().catch(() => {}) })

  const handleDownload = async (name: string) => {
    try {
      // ok:False（如重复下载）为 200 响应体不抛错，显式检查避免「点了没反应」
      const res = (await window.electronAPI.invoke(MODELS_IPC.download, name)) as { ok?: boolean; message?: string; code?: string }
      if (res && res.ok === false) {
        const err = new Error(res.message || 'download rejected')
        ;(err as Error & { errorCode?: string }).errorCode = res.code
        handleError(err)
        return
      }
    } catch (err) {
      handleError(err)
      return
    }
    // 后台线程已同步置 downloading，立即拉一次接上启动间隙（全局心跳 ≤5/15s 后接管谓词）
    await fetchStatus().catch(() => {})
  }

  const handleDownloadAll = async () => {
    setDownloadingAll(true)
    try {
      const res = (await window.electronAPI.invoke(MODELS_IPC.downloadAll)) as { ok?: boolean; message?: string; code?: string }
      if (res && res.ok === false) {
        const err = new Error(res.message || 'download rejected')
        ;(err as Error & { errorCode?: string }).errorCode = res.code
        handleError(err)
        setDownloadingAll(false)
        return
      }
      await fetchStatus().catch(() => {})
    } catch (err) {
      handleError(err)
      // 失败路径复位按钮（无活动时轮询自停，不复位则永久禁用）；
      // 成功路径由 fetchStatus 依 downloading 终态复位
      setDownloadingAll(false)
    }
  }

  // 类别分组：已知顺序优先；后端新增的未知类别追加到尾部不静默丢弃（0.4 §8.1 #6），
  // 标签缺 i18n key 时回退显示类别 id
  const values = Object.values(models)
  const knownSet = new Set<string>(CATEGORY_ORDER)
  const grouped = [
    ...CATEGORY_ORDER.map((cat) => ({
      category: cat,
      label: t(`settings.internalModel.category.${cat}`),
      items: values.filter((m) => m.category === cat),
    })),
    ...[...new Set(values.map((m) => m.category).filter((c) => !knownSet.has(c)))].map((cat) => ({
      category: cat,
      label: t(`settings.internalModel.category.${cat}`, { defaultValue: cat }),
      items: values.filter((m) => m.category === cat),
    })),
  ]

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
          <button className="btn btn-secondary btn-sm" onClick={() => void fetchStatus().catch(() => {})}>
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
