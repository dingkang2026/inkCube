/** src/features/knowledge/ImageGallery.tsx */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, Package, Image as ImageIcon, ZoomIn, ZoomOut, RotateCcw, ChevronLeft, ChevronRight, Pause, Play, Trash2, X, AlertTriangle } from 'lucide-react'
import type { ImageRecord, KnowledgeIpcChannels } from './types'
import { IMAGE_CATEGORIES, DEFAULT_KNOWLEDGE_IPC_CHANNELS } from './types'
import { SkeletonImageGrid } from './SkeletonLoader'
import { useToast } from './Toast'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import { useActivity } from '../activity/ActivityContext'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import s from './ImageGallery.module.css'
import { useConfirm } from '../../components/ConfirmDialog'
import { translateStoredError } from '../../i18n/errorCodes'

interface ImageGalleryProps {
  projectId: string
  fileId?: string
  /** 图片描述队列停止态（0.4 §队列暂停，UI 呈现为停止/继续）：
   *  停止 = 不再开始新描述 + 清空内存队列积压；DB pending 不动，恢复后 Scanner 补投递断点续传 */
  describePaused?: boolean
  onToggleDescribePause?: () => void
  ipcChannels?: Partial<KnowledgeIpcChannels>
}

interface ImageItem extends ImageRecord {
  filename?: string
}

/** Convert a local file path to an inkcube:// protocol URL. */
function localFileUrl(filePath: string): string {
  // Normalize Windows backslashes to forward slashes
  const normalized = filePath.replace(/\\/g, '/')
  // Use query param to avoid drive letter (D:) being parsed as hostname
  return `inkcube://app?file=${encodeURIComponent(normalized)}`
}

export function ImageGallery({ projectId, fileId, describePaused, onToggleDescribePause, ipcChannels }: ImageGalleryProps) {
  const ipc = { ...DEFAULT_KNOWLEDGE_IPC_CHANNELS, ...ipcChannels }
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()
  const handleError = useErrorHandler()
  const [images, setImages] = useState<ImageItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('done')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [sizeFilter, setSizeFilter] = useState('all')
  const [lightboxImg, setLightboxImg] = useState<ImageItem | null>(null)
  const [categoryMenuId, setCategoryMenuId] = useState<string | null>(null)
  const categoryMenuRef = useRef<HTMLDivElement>(null)
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({})
  const [currentPage, setCurrentPage] = useState(1)
  const [lightboxIndex, setLightboxIndex] = useState(-1)
  // 拉取失败标记（静默 catch 排查 2026-10-24）：空列表时给错误+重试，不得伪装
  // 「暂无图片/无匹配」；有内容（筛选重拉/轮询失败）保留现有内容静默
  const [loadFailed, setLoadFailed] = useState(false)
  const [lightboxZoom, setLightboxZoom] = useState(1)

  const LIMIT = 40

  // 轮询/边沿补拉保持用户当前页（页面遍历 review M3-2 2026-10-24）：固定 offset:0
  // 会把翻到第 N 页的视图替换回第一页内容，页码与内容错位
  const currentPageRef = useRef(currentPage)
  currentPageRef.current = currentPage

  const loadImages = async (projId: string, off: number) => {
    setLoading(true)
    try {
      const result: any = await window.electronAPI.invoke(ipc.getProjectImages, {
        projectId: projId,
        limit: LIMIT,
        offset: off,
        fileId: fileId || undefined,
        status: statusFilter || undefined,
        category: categoryFilter !== 'all' ? categoryFilter : undefined,
        search: searchQuery.trim() || undefined,
        size: sizeFilter !== 'all' ? sizeFilter : undefined,
      })
      setImages(result.images || [])
      setTotal(result.total || 0)
      if (result.status_counts) setStatusCounts(result.status_counts)
      if (result.category_counts) setCategoryCounts(result.category_counts)
      setLoadFailed(false)
    } catch (err) {
      console.error('Failed to load images:', err)
      // 失败不覆盖现有内容；空列表置位 → 渲染层错误+重试
      setLoadFailed(true)
    } finally {
      setLoading(false)
    }
  }

  // Silent refresh for polling (no loading spinner)；抛错由 usePolling 计入退避（0.4 §5.8）
  const refreshImages = useCallback(async (projId: string) => {
    const result: any = await window.electronAPI.invoke(ipc.getProjectImages, {
      projectId: projId,
      limit: LIMIT,
      offset: (currentPageRef.current - 1) * LIMIT,
      fileId: fileId || undefined,
      status: statusFilter || undefined,
      category: categoryFilter !== 'all' ? categoryFilter : undefined,
      search: searchQuery.trim() || undefined,
      size: sizeFilter !== 'all' ? sizeFilter : undefined,
    })
    setImages(result.images || [])
    setTotal(result.total || 0)
    if (result.status_counts) setStatusCounts(result.status_counts)
    if (result.category_counts) setCategoryCounts(result.category_counts)
    // 静默刷新成功同样复位失败态（重试按钮走本函数，loadImages 不参与）
    setLoadFailed(false)
  }, [fileId, statusFilter, categoryFilter, sizeFilter, searchQuery, ipc])

  useEffect(() => {
    if (projectId) {
      setCurrentPage(1)
      loadImages(projectId, 0)
    }
  }, [projectId, fileId])

  // Reload when filters change (reset to page 1), skip initial mount
  const isFirstFilterRender = useRef(true)
  useEffect(() => {
    if (isFirstFilterRender.current) {
      isFirstFilterRender.current = false
      return
    }
    if (projectId) {
      setCurrentPage(1)
      loadImages(projectId, 0)
    }
  }, [statusFilter, categoryFilter, sizeFilter, searchQuery])

  // Polling: refresh every 4s while images are being described（#6，v1.7 迁移 usePolling；谓词从全局心跳
  // file kind 的 image_describing 派生，0.4 §6.2——本地 images 受过滤/分页影响 pending 项可能不在列表，漏判熄火）
  const { items: activeFileItems } = useActivity('file')
  const hasActiveImages = activeFileItems.some(a =>
    a.project_id === projectId && (a.image_describing ?? 0) > 0
  )
  usePolling({
    name: 'image-gallery',
    enabled: hasActiveImages,
    interval: 4000,
    task: () => refreshImages(projectId),
  })
  // 终态边沿补拉（0.4 §6.3）：轮询随 image_describing 归零自停，最后一批完成图
  // 可能落在末次刷新之后（默认 statusFilter='done'，新完成图不在列表）→ 边沿补拉
  useEdgeRefetch(hasActiveImages, () => {
    if (projectId) void refreshImages(projectId).catch(() => {})
  })

  const handlePageChange = (page: number) => {
    if (projectId && page >= 1 && page <= totalPages && page !== currentPage) {
      setCurrentPage(page)
      const newOffset = (page - 1) * LIMIT
      loadImages(projectId, newOffset)
      // Scroll to top of gallery
      document.querySelector(`.${s.container}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  const handleZoom = (delta: number) => {
    setLightboxZoom(prev => Math.min(4, Math.max(0.25, prev + delta)))
  }

  // Lightbox keyboard navigation
  useEffect(() => {
    if (!lightboxImg) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeLightbox()
      else if (e.key === 'ArrowLeft') handleLightboxNav(-1)
      else if (e.key === 'ArrowRight') handleLightboxNav(1)
      else if (e.key === '+' || e.key === '=') handleZoom(0.25)
      else if (e.key === '-') handleZoom(-0.25)
      else if (e.key === '0') setLightboxZoom(1)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [lightboxImg, lightboxIndex])

  // Close category menu on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (categoryMenuRef.current && !categoryMenuRef.current.contains(e.target as Node)) {
        setCategoryMenuId(null)
      }
    }
    if (categoryMenuId) {
      document.addEventListener('mousedown', handleClick)
      return () => document.removeEventListener('mousedown', handleClick)
    }
  }, [categoryMenuId])

  // Images are already filtered server-side
  const filteredImages = images

  // Derive counts from backend status_counts
  const totalPages = Math.max(1, Math.ceil(total / LIMIT))
  // 「全部」标签计数 = 各状态/各类别计数之和（status_counts/category_counts 不含对应维度
  // 过滤，求和即该维度下全部；total 随当前筛选变化，误作「全部」会显示成当前筛选项的数）
  const allStatusCount = Object.values(statusCounts).reduce((a, b) => a + b, 0)
  const allCategoryCount = Object.values(categoryCounts).reduce((a, b) => a + b, 0)

  const handleImageClick = (img: ImageItem) => {
    const idx = filteredImages.findIndex(fi => fi.id === img.id)
    setLightboxIndex(idx >= 0 ? idx : 0)
    setLightboxImg(img)
    setLightboxZoom(1)
  }

  const closeLightbox = () => {
    setLightboxImg(null)
    setLightboxIndex(-1)
    setLightboxZoom(1)
  }

  const handleLightboxNav = (dir: number) => {
    if (filteredImages.length === 0) return
    const newIndex = (lightboxIndex + dir + filteredImages.length) % filteredImages.length
    setLightboxIndex(newIndex)
    setLightboxImg(filteredImages[newIndex])
    setLightboxZoom(1)
  }

  const handleRetry = useCallback(async (img: ImageItem, force = false) => {
    try {
      await window.electronAPI.invoke(ipc.retryImage, {
        projectId,
        imageId: img.id,
        force,
      })
      setImages(prev => prev.map(i =>
        i.id === img.id ? { ...i, status: 'pending' as const, error_message: null } : i
      ))
      showToast(force ? t('knowledge.retryForceSubmitted') : t('knowledge.retrySubmitted'), 'success')
    } catch (err: any) {
      handleError(err)
    }
  }, [projectId, t, showToast, handleError])

  const handleChangeCategory = useCallback(async (img: ImageItem, category: string) => {
    try {
      await window.electronAPI.invoke(ipc.updateImageCategory, {
        projectId,
        imageId: img.id,
        category,
      })
      setImages(prev => prev.map(i =>
        i.id === img.id ? { ...i, category } : i
      ))
      setCategoryMenuId(null)
    } catch (err) {
      handleError(err)
    }
  }, [projectId, handleError])

  const handleArchive = useCallback(async (img: ImageItem) => {
    if (!(await confirmDialog({ title: t('knowledge.archiveImage'), message: t('knowledge.confirmArchive'), danger: false }))) return
    try {
      await window.electronAPI.invoke(ipc.archiveImage, {
        projectId,
        imageId: img.id,
      })
      setImages(prev => prev.map(i =>
        i.id === img.id ? { ...i, status: 'archived' as const, vlm_description: null, error_message: 'INKCUBE_CODE:MANUALLY_ARCHIVED' } : i
      ))
    } catch (err) {
      handleError(err)
    }
  }, [projectId, t, confirmDialog, handleError])

  // 删除：向量（ChromaDB）+ 磁盘文件 + DB 记录由后端一并清除（2.2 §10.1）；
  // confirm 不传 danger → 默认红色确认钮（破坏性操作）
  const handleDelete = useCallback(async (img: ImageItem) => {
    if (!(await confirmDialog({ title: t('knowledge.deleteImage'), message: t('knowledge.confirmDeleteImage') }))) return
    try {
      await window.electronAPI.invoke(ipc.deleteImage, {
        projectId,
        imageId: img.id,
      })
      setImages(prev => prev.filter(i => i.id !== img.id))
      setTotal(prev => Math.max(0, prev - 1))
      if (lightboxImg?.id === img.id) {
        setLightboxImg(null)
        setLightboxIndex(-1)
        setLightboxZoom(1)
      }
      showToast(t('knowledge.deleteSuccess'), 'success')
    } catch (err) {
      handleError(err)
    }
  }, [projectId, ipc, t, confirmDialog, handleError, lightboxImg])

  if (loading && images.length === 0) {
    return (
      <div className={s.container}>
        <div className={s.searchBar}>
          <span className={s.total}>...</span>
        </div>
        <SkeletonImageGrid count={8} />
      </div>
    )
  }

  // Empty state (no images at all) — still show filter tabs so user can navigate
  const showEmptyState = !loading && images.length === 0 && !statusFilter && !categoryFilter && !searchQuery && !loadFailed

  return (
    <div className={s.container}>
      {/* Filter bar: status tabs + search */}
      <div className={s.searchBar}>
        <div className={s.totalWrap}>
          <span className={s.total}>{t('knowledge.imageTotal', { count: total })}</span>
          {/* 描述队列停止开关（0.4 §队列暂停，UI 语义停止/继续）：仅当父组件接入 useQueuePause 时显示 */}
          {onToggleDescribePause && (
            <button
              className={`${s.pauseBtn} ${describePaused ? s.pauseBtnActive : ''}`}
              onClick={onToggleDescribePause}
              title={describePaused ? t('knowledge.resumeDescribe') : t('knowledge.stopDescribe')}
            >
              {describePaused ? <Play size={13} /> : <Pause size={13} />}
            </button>
          )}
        </div>
        {/* Status filter tabs — each status独立 */}
        <div className={s.statusTabs}>
          {[
            { value: 'pending', label: t('knowledge.imgStatus_pending'), count: statusCounts['pending'] || 0 },
            { value: 'processing', label: t('knowledge.imgStatus_processing'), count: statusCounts['processing'] || 0 },
            { value: 'done', label: t('knowledge.imgStatus_done'), count: statusCounts['done'] || 0 },
            { value: 'error', label: t('knowledge.imgStatus_error'), count: statusCounts['error'] || 0 },
            { value: 'archived', label: t('knowledge.imgStatus_archived'), count: statusCounts['archived'] || 0 },
            { value: 'all', label: t('knowledge.statusFilter_all'), count: allStatusCount },
          ].map(opt => (
            <button
              key={opt.value}
              className={`${s.statusTab} ${statusFilter === opt.value ? s.statusTabActive : ''}`}
              onClick={() => setStatusFilter(opt.value)}
            >
              {opt.label}{opt.count > 0 ? ` (${opt.count})` : ''}
            </button>
          ))}
        </div>
        <div className={s.searchWrap}>
          <span className={s.searchIcon}><Search size={14} /></span>
          <input
            type="text"
            className={s.searchInput}
            placeholder={t('knowledge.searchImagePlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <button className={s.searchClear} onClick={() => setSearchQuery('')}>×</button>
          )}
        </div>
      </div>

      {/* Category filter tabs + size filter — second row */}
      <div className={s.filterRow}>
        <div className={s.categoryTabs}>
          <button
            className={`${s.categoryTab} ${categoryFilter === 'all' ? s.categoryTabActive : ''}`}
            onClick={() => setCategoryFilter('all')}
          >
            {t('knowledge.category_all', '全部')} ({allCategoryCount})
          </button>
          {IMAGE_CATEGORIES.map(cat => {
            const cnt = categoryCounts[cat] || 0
            return (
              <button
                key={cat}
                className={`${s.categoryTab} ${categoryFilter === cat ? s.categoryTabActive : ''}`}
                onClick={() => setCategoryFilter(cat)}
              >
                {t(`knowledge.category_${cat}`)}{cnt > 0 ? ` (${cnt})` : ''}
              </button>
            )
          })}
        </div>
        <select
          className={s.sizeFilter}
          value={sizeFilter}
          onChange={(e) => setSizeFilter(e.target.value)}
        >
          <option value="all">{t('knowledge.size_all', '全部尺寸')}</option>
          <option value="small">{t('knowledge.size_small', '小图')} (&lt;200px)</option>
          <option value="medium">{t('knowledge.size_medium', '中图')} (200-800px)</option>
          <option value="large">{t('knowledge.size_large', '大图')} (≥800px)</option>
        </select>
      </div>

      {/* 首拉失败态（静默 catch 排查 2026-10-24）：错误+重试，区别于空态/无匹配 */}
      {loadFailed && filteredImages.length === 0 && !loading && (
        <div className={s.empty} data-testid="image-gallery-error">
          <div className={s.emptyIcon}><AlertTriangle size={28} /></div>
          <p>{t('knowledge.galleryLoadFailed')}</p>
          <button
            type="button"
            className={s.categoryTab}
            onClick={() => { if (projectId) void refreshImages(projectId).catch(() => { /* 重试失败保持错误态，下一轮轮询自愈 */ }) }}
          >
            {t('knowledge.retry')}
          </button>
        </div>
      )}

      {/* Empty state: no images at all */}
      {showEmptyState && (
        <div className={s.empty}>
          <div className={s.emptyIcon}><ImageIcon size={28} /></div>
          <p>{t('knowledge.noImages')}</p>
        </div>
      )}

      {/* No results for current filter */}
      {!showEmptyState && filteredImages.length === 0 && !loading && !loadFailed && (
        <div className={s.noMatch}>
          <p>{searchQuery ? t('knowledge.noImageMatch') : t('knowledge.noImages')}</p>
        </div>
      )}

      {/* Thumbnail grid */}
      {filteredImages.length > 0 && (
        <div className={s.grid}>
        {filteredImages.map((img) => (
          <div key={img.id} className={`${s.thumb} ${img.status === 'error' ? s.thumbError : img.status === 'archived' ? s.thumbArchived : ''}`}>
            <div
              className={s.thumbImage}
              onClick={() => handleImageClick(img)}
            >
              <img
                src={localFileUrl(img.local_path)}
                alt={img.caption || img.vlm_description || ''}
                loading="lazy"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none'
                }}
              />
              {/* Status badge (text label) */}
              {img.status !== 'done' && (
                <span className={`${s.statusBadge} ${s[`status_${img.status}`] || ''}`}>
                  {t(`knowledge.imgStatus_${img.status}`)}
                </span>
              )}
              {/* Error tooltip on hover（D1：落库协议串还原为当前语言） */}
              {img.status === 'error' && img.error_message && (
                <div className={s.errorTooltip}>
                  {translateStoredError(img.error_message, t)}
                </div>
              )}
              {/* Hover overlay with actions */}
              <div className={s.thumbOverlay}>
                <span className={s.thumbIcon}><Search size={14} /></span>
                <div className={s.thumbActions}>
                  {/* Retry: always force — user explicitly requested */}
                  {(img.status === 'error' || img.status === 'archived') && (
                    <button
                      className={s.thumbActionBtn}
                      onClick={(e) => { e.stopPropagation(); handleRetry(img, true) }}
                      title={t('knowledge.retry')}
                    ><RotateCcw size={13} /></button>
                  )}
                  {/* Archive button: visible for non-archived images */}
                  {img.status !== 'archived' && (
                    <button
                      className={s.thumbActionBtn}
                      onClick={(e) => { e.stopPropagation(); handleArchive(img) }}
                      title={t('knowledge.archiveImage')}
                    ><Package size={13} /></button>
                  )}
                  {/* Delete: all statuses，确认后向量/磁盘/记录一并清除 */}
                  <button
                    className={s.thumbActionBtn}
                    onClick={(e) => { e.stopPropagation(); handleDelete(img) }}
                    title={t('knowledge.deleteImage')}
                  ><Trash2 size={13} /></button>
                </div>
              </div>
            </div>
            {/* Row 1: Status + filename + page */}
            <div className={s.thumbStatusRow}>
              <span className={`${s.statusInline} ${s[`statusInline_${img.status}`] || ''}`}>
                {t(`knowledge.imgStatus_${img.status}`)}
              </span>
              <span className={s.thumbFilename} title={img.filename}>
                {img.filename || '—'}
              </span>
              {img.page_no != null && (
                <span className={s.thumbPage}>p.{img.page_no}</span>
              )}
            </div>
            {/* Row 2: Category badge */}
            <div className={s.thumbCategoryRow}>
              <button
                className={`${s.categoryBadge} ${s[`cat_${img.category || 'uncategorized'}`] || ''}`}
                onClick={(e) => { e.stopPropagation(); setCategoryMenuId(categoryMenuId === img.id ? null : img.id) }}
                title={t('knowledge.changeCategory')}
              >
                {t(`knowledge.category_${img.category || 'uncategorized'}`)}
              </button>
              {categoryMenuId === img.id && (
                <div className={s.categoryMenu} ref={categoryMenuRef}>
                  {IMAGE_CATEGORIES.map(cat => (
                    <button
                      key={cat}
                      className={`${s.categoryMenuItem} ${(img.category || 'uncategorized') === cat ? s.categoryMenuActive : ''}`}
                      onClick={(e) => { e.stopPropagation(); handleChangeCategory(img, cat) }}
                    >
                      {t(`knowledge.category_${cat}`)}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* Description preview */}
            {img.vlm_description && (
              <div className={s.thumbDesc} title={img.vlm_description}>
                {img.vlm_description}
              </div>
            )}
          </div>
        ))}
      </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className={s.pagination}>
          <button className={s.pageBtn} disabled={currentPage <= 1} onClick={() => handlePageChange(currentPage - 1)}>
            <ChevronLeft size={14} />
          </button>
          {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
            let page: number
            if (totalPages <= 7) {
              page = i + 1
            } else if (currentPage <= 4) {
              page = i + 1
            } else if (currentPage >= totalPages - 3) {
              page = totalPages - 6 + i
            } else {
              page = currentPage - 3 + i
            }
            return (
              <button
                key={page}
                className={`${s.pageBtn} ${page === currentPage ? s.pageBtnActive : ''}`}
                onClick={() => handlePageChange(page)}
              >
                {page}
              </button>
            )
          })}
          <button className={s.pageBtn} disabled={currentPage >= totalPages} onClick={() => handlePageChange(currentPage + 1)}>
            <ChevronRight size={14} />
          </button>
          <span className={s.pageInfo}>{currentPage} / {totalPages}</span>
        </div>
      )}

      {/* Lightbox */}
      {lightboxImg && (
        <div className={s.lightbox} onClick={closeLightbox}>
          <div className={s.lightboxContent} onClick={(e) => e.stopPropagation()}>
            <button className={s.lightboxClose} onClick={closeLightbox}><X size={16} /></button>
            {/* Nav arrows */}
            {filteredImages.length > 1 && (
              <>
                <button className={s.lightboxNav} onClick={() => handleLightboxNav(-1)}>
                  <ChevronLeft size={20} />
                </button>
                <button className={s.lightboxNavRight} onClick={() => handleLightboxNav(1)}>
                  <ChevronRight size={20} />
                </button>
              </>
            )}
            {/* Image with zoom */}
            <div className={s.lightboxImageWrap}>
              <img
                src={localFileUrl(lightboxImg.local_path)}
                alt=""
                style={{ transform: `scale(${lightboxZoom})`, transformOrigin: 'center center' }}
              />
            </div>
            {/* Zoom controls */}
            <div className={s.lightboxZoomBar}>
              <button className={s.zoomBtn} onClick={() => handleZoom(-0.25)} title="Zoom Out"><ZoomOut size={16} /></button>
              <span className={s.zoomLevel}>{Math.round(lightboxZoom * 100)}%</span>
              <button className={s.zoomBtn} onClick={() => handleZoom(0.25)} title="Zoom In"><ZoomIn size={16} /></button>
              <button className={s.zoomBtn} onClick={() => setLightboxZoom(1)} title="Reset"><RotateCcw size={14} /></button>
            </div>
            {/* Metadata panel */}
            <div className={s.lightboxMeta}>
              <div className={s.lightboxMetaRow}>
                <span className={s.lightboxMetaLabel}>{t('knowledge.filename', 'Filename')}</span>
                <span>{lightboxImg.filename || '—'}</span>
              </div>
              {(lightboxImg.width_px || lightboxImg.height_px) && (
                <div className={s.lightboxMetaRow}>
                  <span className={s.lightboxMetaLabel}>{t('knowledge.dimensions', 'Dimensions')}</span>
                  <span>{lightboxImg.width_px || '?'} × {lightboxImg.height_px || '?'} px</span>
                </div>
              )}
              {lightboxImg.page_no != null && (
                <div className={s.lightboxMetaRow}>
                  <span className={s.lightboxMetaLabel}>{t('knowledge.page', 'Page')}</span>
                  <span>{lightboxImg.page_no}</span>
                </div>
              )}
              <div className={s.lightboxMetaRow}>
                <span className={s.lightboxMetaLabel}>{t('knowledge.category', 'Category')}</span>
                <span>{t(`knowledge.category_${lightboxImg.category || 'uncategorized'}`)}</span>
              </div>
              <div className={s.lightboxMetaRow}>
                <span className={s.lightboxMetaLabel}>{t('knowledge.status', 'Status')}</span>
                <span>{t(`knowledge.imgStatus_${lightboxImg.status}`)}</span>
              </div>
              {lightboxImg.vlm_description && (
                <div className={s.lightboxMetaRow}>
                  <span className={s.lightboxMetaLabel}>{t('knowledge.description', 'Description')}</span>
                  <span className={s.lightboxMetaDesc}>{lightboxImg.vlm_description}</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
