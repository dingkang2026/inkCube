/** src/features/knowledge/ImageGallery.tsx */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, Package, Image as ImageIcon } from 'lucide-react'
import type { ImageRecord } from './types'
import { IMAGE_CATEGORIES } from './types'
import { SkeletonImageGrid } from './SkeletonLoader'
import { useToast } from './Toast'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import s from './ImageGallery.module.css'

interface ImageGalleryProps {
  projectId: string
  fileId?: string
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

export function ImageGallery({ projectId, fileId }: ImageGalleryProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const handleError = useErrorHandler()
  const [images, setImages] = useState<ImageItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(false)
  const [offset, setOffset] = useState(0)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('active')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [lightboxImg, setLightboxImg] = useState<ImageItem | null>(null)
  const [categoryMenuId, setCategoryMenuId] = useState<string | null>(null)
  const categoryMenuRef = useRef<HTMLDivElement>(null)

  const LIMIT = 40

  const loadImages = async (projId: string, off: number, append = false) => {
    setLoading(true)
    try {
      const result: any = await window.electronAPI.invoke('knowledge:getProjectImages', {
        projectId: projId,
        limit: LIMIT,
        offset: off,
        fileId: fileId || undefined,
      })
      const imgs: ImageItem[] = result.images || []
      if (append) {
        setImages(prev => [...prev, ...imgs])
      } else {
        setImages(imgs)
      }
      setTotal(result.total || 0)
      setHasMore(result.has_more || false)
      setOffset(off + imgs.length)
    } catch (err) {
      console.error('Failed to load images:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (projectId) {
      loadImages(projectId, 0)
    }
  }, [projectId, fileId])

  const handleLoadMore = () => {
    if (projectId && hasMore && !loading) {
      loadImages(projectId, offset, true)
    }
  }

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

  // Filter images by status, category, and search query
  const filteredImages = images.filter(img => {
    // Status filter: 'active' excludes archived
    if (statusFilter === 'active' && img.status === 'archived') return false
    if (statusFilter === 'archived' && img.status !== 'archived') return false
    if (statusFilter === 'error' && img.status !== 'error') return false
    // Category filter
    if (categoryFilter !== 'all' && (img.category || 'uncategorized') !== categoryFilter) return false
    // Text search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      return (
        (img.vlm_description && img.vlm_description.toLowerCase().includes(q)) ||
        (img.caption && img.caption.toLowerCase().includes(q)) ||
        (img.filename && img.filename.toLowerCase().includes(q))
      )
    }
    return true
  })

  // Count archived images for the tab badge
  const archivedCount = images.filter(img => img.status === 'archived').length

  const handleImageClick = (img: ImageItem) => {
    setLightboxImg(img)
  }

  const closeLightbox = () => setLightboxImg(null)

  const handleRetry = useCallback(async (img: ImageItem, force = false) => {
    try {
      await window.electronAPI.invoke('knowledge:retryImage', {
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
      await window.electronAPI.invoke('knowledge:updateImageCategory', {
        projectId,
        imageId: img.id,
        category,
      })
      setImages(prev => prev.map(i =>
        i.id === img.id ? { ...i, category } : i
      ))
      setCategoryMenuId(null)
    } catch (err) {
      console.error('Update category failed:', err)
    }
  }, [projectId])

  const handleArchive = useCallback(async (img: ImageItem) => {
    if (!confirm(t('knowledge.confirmArchive'))) return
    try {
      await window.electronAPI.invoke('knowledge:archiveImage', {
        projectId,
        imageId: img.id,
      })
      setImages(prev => prev.map(i =>
        i.id === img.id ? { ...i, status: 'archived' as const, vlm_description: null, error_message: '手动归档' } : i
      ))
    } catch (err) {
      console.error('Archive failed:', err)
    }
  }, [projectId, t])

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

  if (!loading && images.length === 0) {
    return (
      <div className={s.empty}>
        <div className={s.emptyIcon}><ImageIcon size={28} /></div>
        <p>{t('knowledge.noImages')}</p>
      </div>
    )
  }

  return (
    <div className={s.container}>
      {/* Filter bar: status tabs + category + search */}
      <div className={s.searchBar}>
        <span className={s.total}>{t('knowledge.imageTotal', { count: total })}</span>
        {/* Status filter tabs */}
        <div className={s.statusTabs}>
          {[
            { value: 'active', label: t('knowledge.statusFilter_active') },
            { value: 'archived', label: `${t('knowledge.imgStatus_archived')}${archivedCount > 0 ? ` (${archivedCount})` : ''}` },
            { value: 'error', label: t('knowledge.imgStatus_error') },
            { value: 'all', label: t('knowledge.statusFilter_all') },
          ].map(opt => (
            <button
              key={opt.value}
              className={`${s.statusTab} ${statusFilter === opt.value ? s.statusTabActive : ''}`}
              onClick={() => setStatusFilter(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <select
          className={s.categoryFilter}
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
        >
          <option value="all">{t('knowledge.category')} — {t('knowledge.category_all', '全部')}</option>
          {IMAGE_CATEGORIES.map(cat => (
            <option key={cat} value={cat}>{t(`knowledge.category_${cat}`)}</option>
          ))}
        </select>
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

      {/* Thumbnail grid */}
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
              {/* Error tooltip on hover */}
              {img.status === 'error' && img.error_message && (
                <div className={s.errorTooltip}>
                  {img.error_message}
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
                    >↻</button>
                  )}
                  {/* Archive button: visible for non-archived images */}
                  {img.status !== 'archived' && (
                    <button
                      className={s.thumbActionBtn}
                      onClick={(e) => { e.stopPropagation(); handleArchive(img) }}
                      title={t('knowledge.archiveImage')}
                    ><Package size={13} /></button>
                  )}
                </div>
              </div>
            </div>
            <div className={s.thumbInfo}>
              <span className={s.thumbFilename} title={img.filename}>
                {img.filename || '—'}
              </span>
              {img.page_no != null && (
                <span className={s.thumbPage}>p.{img.page_no}</span>
              )}
            </div>
            {/* Category badge */}
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

      {filteredImages.length === 0 && searchQuery && (
        <div className={s.noMatch}>
          <p>{t('knowledge.noImageMatch')}</p>
        </div>
      )}

      {/* Load more */}
      {hasMore && (
        <button className={s.loadMoreBtn} onClick={handleLoadMore} disabled={loading}>
          {loading ? t('knowledge.loading') : t('knowledge.loadMore')}
        </button>
      )}

      {/* Lightbox */}
      {lightboxImg && (
        <div className={s.lightbox} onClick={closeLightbox}>
          <div className={s.lightboxContent} onClick={(e) => e.stopPropagation()}>
            <img src={localFileUrl(lightboxImg.local_path)} alt="" />
            <button className={s.lightboxClose} onClick={closeLightbox}>×</button>
            {lightboxImg.vlm_description && (
              <div className={s.lightboxDesc}>{lightboxImg.vlm_description}</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
