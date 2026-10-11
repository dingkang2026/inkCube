/** src/features/knowledge/ImageList.tsx */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { X, RefreshCw, Package } from 'lucide-react'
import type { ImageRecord, KnowledgeIpcChannels } from './types'
import { IMAGE_CATEGORIES, DEFAULT_KNOWLEDGE_IPC_CHANNELS } from './types'
import { useToast } from './Toast'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import s from './ImageList.module.css'
import { useConfirm } from '../../components/ConfirmDialog'
import { useActivity } from '../activity/ActivityContext'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'

interface ImageListProps {
  projectId: string
  fileId: string
  onClose: () => void
  ipcChannels?: Partial<KnowledgeIpcChannels>
}

export function ImageList({ projectId, fileId, onClose, ipcChannels }: ImageListProps) {
  const ipc = { ...DEFAULT_KNOWLEDGE_IPC_CHANNELS, ...ipcChannels }
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()
  const handleError = useErrorHandler()
  const [images, setImages] = useState<ImageRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('active')

  const loadImages = async () => {
    setLoading(true)
    try {
      const result: any = await window.electronAPI.invoke(ipc.getImages, { projectId, fileId })
      setImages(result.images || [])
    } catch (err) {
      console.error('Failed to load images:', err)
    } finally {
      setLoading(false)
    }
  }

  // 静默刷新（轮询用）：不触发 loading 转圈，仅换数据
  const refreshImages = async () => {
    try {
      const result: any = await window.electronAPI.invoke(ipc.getImages, { projectId, fileId })
      setImages(result.images || [])
    } catch (err) {
      console.error('Failed to refresh images:', err)
    }
  }

  useEffect(() => { loadImages() }, [fileId])

  // 描述进行中每 4s 静默重拉（0.4 §6.2：谓词从全局心跳 image_describing 派生，
  // 本列表此前仅打开时拉一次——描述中打开会永久冻结在旧状态）
  const { items: activeFileItems } = useActivity('file')
  const hasActiveImages = activeFileItems.some(
    a => a.project_id === projectId && (a.image_describing ?? 0) > 0
  )
  usePolling({
    name: 'image-list',
    enabled: hasActiveImages,
    interval: 4000,
    task: () => refreshImages(),
  })
  // 终态边沿补拉（0.4 §6.3）：轮询随 image_describing 归零自停，最后一批
  // 完成图可能落在末次刷新之后 → 边沿补拉
  useEdgeRefetch(hasActiveImages, () => { void loadImages() })

  const handleRetry = async (imageId: string, force = false) => {
    try {
      await window.electronAPI.invoke(ipc.retryImage, { projectId, imageId, force })
      loadImages()
      showToast(force ? t('knowledge.retryForceSubmitted') : t('knowledge.retrySubmitted'), 'success')
    } catch (err: any) {
      handleError(err)
    }
  }

  const handleChangeCategory = async (imageId: string, category: string) => {
    try {
      await window.electronAPI.invoke(ipc.updateImageCategory, { projectId, imageId, category })
      setImages(prev => prev.map(i => i.id === imageId ? { ...i, category } : i))
    } catch (err) {
      handleError(err)
    }
  }

  const handleArchive = async (imageId: string) => {
    if (!(await confirmDialog({ title: t('knowledge.archiveImage'), message: t('knowledge.confirmArchive'), danger: false }))) return
    try {
      await window.electronAPI.invoke(ipc.archiveImage, { projectId, imageId })
      setImages(prev => prev.map(i =>
        i.id === imageId ? { ...i, status: 'archived' as const, vlm_description: null, error_message: '手动归档' } : i
      ))
    } catch (err) {
      handleError(err)
    }
  }

  const handleRetryFailed = async () => {
    try {
      await window.electronAPI.invoke(ipc.retryFailedImages, { projectId, fileId })
      loadImages()
      showToast(t('knowledge.retrySubmitted'), 'success')
    } catch (err: any) {
      handleError(err)
    }
  }

  // Filter images by status
  const archivedCount = images.filter(img => img.status === 'archived').length
  const filteredImages = images.filter(img => {
    if (statusFilter === 'active' && img.status === 'archived') return false
    if (statusFilter === 'archived' && img.status !== 'archived') return false
    if (statusFilter === 'error' && img.status !== 'error') return false
    return true
  })

  return (
    <div className={s.container}>
      <div className={s.header}>
        <h4 className={s.title}>{t('knowledge.imageList')}</h4>
        <button className={s.closeBtn} onClick={onClose}><X size={16} /></button>
      </div>

      {/* Status filter tabs */}
      <div className={s.filterBar}>
        {[
          { value: 'active', label: t('knowledge.statusFilter_active') },
          { value: 'archived', label: `${t('knowledge.imgStatus_archived')}${archivedCount > 0 ? ` (${archivedCount})` : ''}` },
          { value: 'error', label: t('knowledge.imgStatus_error') },
          { value: 'all', label: t('knowledge.statusFilter_all') },
        ].map(opt => (
          <button
            key={opt.value}
            className={`${s.filterTab} ${statusFilter === opt.value ? s.filterTabActive : ''}`}
            onClick={() => setStatusFilter(opt.value)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className={s.loading}>{t('knowledge.loadingImages')}</div>
      ) : filteredImages.length === 0 ? (
        <div className={s.empty}>{t('knowledge.noImages')}</div>
      ) : (
        <>
          {filteredImages.map(img => (
            <div key={img.id} className={s.imageItem}>
              <div className={s.imageInfo}>
                <span className={s.pageNo}>P{img.page_no ?? '?'}</span>
                <span className={`${s.statusBadge} ${s[`status_${img.status}`] || ''}`}>
                  {t(`knowledge.imgStatus_${img.status}`)}
                </span>
                <select
                  className={s.categorySelect}
                  value={img.category || 'uncategorized'}
                  onChange={(e) => handleChangeCategory(img.id, e.target.value)}
                  title={t('knowledge.changeCategory')}
                >
                  {IMAGE_CATEGORIES.map(cat => (
                    <option key={cat} value={cat}>{t(`knowledge.category_${cat}`)}</option>
                  ))}
                </select>
                {img.vlm_description && (
                  <p className={s.description}>{img.vlm_description.slice(0, 120)}...</p>
                )}
                {img.error_message && (
                  <p className={s.error}>{img.error_message}</p>
                )}
              </div>
              <div className={s.imageActions}>
                {/* Retry: always force — user explicitly requested */}
                {(img.status === 'error' || img.status === 'archived') && (
                  <button
                    className={s.actionBtn}
                    onClick={() => handleRetry(img.id, true)}
                    title={t('knowledge.retry')}
                  >
                    <RefreshCw size={13} />
                  </button>
                )}
                {img.status !== 'archived' && (
                  <button className={s.actionBtn} onClick={() => handleArchive(img.id)} title={t('knowledge.archiveImage')}>
                    <Package size={13} />
                  </button>
                )}
              </div>
            </div>
          ))}
          {(filteredImages.some(i => i.status === 'error') || filteredImages.some(i => i.status === 'archived')) && (
            <button className={s.retryAllBtn} onClick={handleRetryFailed}>
              <RefreshCw size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />{t('knowledge.retryAllFailed')}
            </button>
          )}
        </>
      )}
    </div>
  )
}
