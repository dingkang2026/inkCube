/** src/features/knowledge/FileUploader.tsx */
import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2, RefreshCw, MoreVertical } from 'lucide-react'
import type { FileRecord, KnowledgeIpcChannels } from './types'
import { DEFAULT_KNOWLEDGE_IPC_CHANNELS } from './types'
import s from './FileUploader.module.css'

interface FileUploaderProps {
  projectId: string
  files: FileRecord[]
  onRefresh: () => void
  hasMore?: boolean
  onLoadMore?: () => void
  loadingMore?: boolean
  selectedFileId?: string | null
  onSelectFile?: (fileId: string | null) => void
  onDeleteFile?: (fileIds: string[]) => void
  ipcChannels?: Partial<KnowledgeIpcChannels>
}

const FILE_ICONS: Record<string, string> = {
  '.pdf': '📄',
  '.docx': '📝',
  '.doc': '📝',
  '.pptx': '📊',
  '.ppt': '📊',
  '.xlsx': '📈',
  '.xls': '📈',
  '.html': '🌐',
  '.png': '🖼️',
  '.jpg': '🖼️',
  '.jpeg': '🖼️',
}

export function FileUploader({ projectId, files, onRefresh, hasMore, onLoadMore, loadingMore, selectedFileId, onSelectFile, onDeleteFile, ipcChannels }: FileUploaderProps) {
  const ipc = { ...DEFAULT_KNOWLEDGE_IPC_CHANNELS, ...ipcChannels }
  const { t } = useTranslation()
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [confirmingFileId, setConfirmingFileId] = useState<string | null>(null)
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [menuOpenFileId, setMenuOpenFileId] = useState<string | null>(null)

  useEffect(() => {
    if (!menuOpenFileId) return
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest(`.${s.menuBtnWrap}`)) {
        setMenuOpenFileId(null)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [menuOpenFileId])

  const handleDelete = async (fileId: string) => {
    if (confirmingFileId === fileId) {
      // Second click — confirmed
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current)
      setConfirmingFileId(null)
    } else {
      // First click — show confirmation
      setConfirmingFileId(fileId)
      confirmTimerRef.current = setTimeout(() => setConfirmingFileId(null), 3000)
      return
    }
    try {
      await window.electronAPI.invoke(ipc.deleteFile, { projectId, fileId })
      onRefresh()
      onDeleteFile?.([fileId])
    } catch (err) {
      console.error('Delete failed:', err)
    }
  }

  const cancelDelete = () => {
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current)
    setConfirmingFileId(null)
  }

  const handleRetry = async (fileId: string) => {
    try {
      await window.electronAPI.invoke(ipc.retryFile, { projectId, fileId })
      onRefresh()
    } catch (err) {
      console.error('Retry failed:', err)
    }
  }

  const handleRetryAll = async () => {
    try {
      await window.electronAPI.invoke(ipc.retryFailedFiles, projectId)
      onRefresh()
    } catch (err) {
      console.error('Retry all failed:', err)
    }
  }

  // Multi-select handlers
  const toggleSelect = (fileId: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(fileId)) next.delete(fileId)
      else next.add(fileId)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === files.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(files.map(f => f.id)))
    }
  }

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    if (!confirm(t('knowledge.confirmBatchDelete', { count: selectedIds.size }))) return
    try {
      await window.electronAPI.invoke(ipc.batchDeleteFiles, {
        projectId,
        fileIds: Array.from(selectedIds),
      })
      const deleted = Array.from(selectedIds)
      setSelectedIds(new Set())
      onRefresh()
      onDeleteFile?.(deleted)
    } catch (err) {
      console.error('Batch delete failed:', err)
    }
  }

  const handleBatchReprocess = async () => {
    if (selectedIds.size === 0) return
    try {
      await window.electronAPI.invoke(ipc.batchReprocessFiles, {
        projectId,
        fileIds: Array.from(selectedIds),
      })
      setSelectedIds(new Set())
      onRefresh()
    } catch (err) {
      console.error('Batch reprocess failed:', err)
    }
  }

  const hasSelection = selectedIds.size > 0

  return (
    <div className={s.container}>
      {/* File list */}
      {files.length > 0 && (
        <div className={s.fileList}>
          <div className={s.fileListHeader}>
            <label className={s.selectAllLabel}>
              <input
                type="checkbox"
                checked={files.length > 0 && selectedIds.size === files.length}
                onChange={toggleSelectAll}
              />
              <span>{t('knowledge.uploadedFiles')} ({files.length})</span>
            </label>
            <button className={s.refreshBtn} onClick={onRefresh} title={t('knowledge.refresh')}>
              <RefreshCw size={14} />
            </button>
          </div>
          {files.map((file) => {
            const statusClass = getStatusDotClass(file.status)
            const isParsing = file.status === 'parsing'
            return (
            <div
              key={file.id}
              className={`${s.fileItem} ${selectedIds.has(file.id) ? s.fileItemSelected : ''} ${selectedFileId === file.id ? s.fileItemActive : ''} ${isParsing ? s.fileItemParsing : ''}`}
              title={file.error_message || undefined}
              onClick={() => onSelectFile?.(selectedFileId === file.id ? null : file.id)}
            >
              <input
                type="checkbox"
                className={s.fileCheckbox}
                checked={selectedIds.has(file.id)}
                onChange={(e) => { e.stopPropagation(); toggleSelect(file.id) }}
              />
              <span className={s.fileIcon}>{FILE_ICONS[file.file_type] || '📄'}</span>
              <div className={s.fileInfo}>
                <span className={s.filename}>{file.filename}</span>
                <div className={s.fileMeta}>
                  <span className={s.fileType}>{file.file_type.toUpperCase()}</span>
                  {file.chunks_count !== undefined && file.chunks_count > 0 && (
                    <>
                      <span className={s.metaDot}>·</span>
                      <span>{file.chunks_count} {t('knowledge.chunks')}</span>
                    </>
                  )}
                  {file.images_count !== undefined && file.images_count > 0 && (
                    <>
                      <span className={s.metaDot}>·</span>
                      <span>{file.image_described_count || 0}/{file.images_count} {t('knowledge.images')}</span>
                    </>
                  )}
                </div>
                {file.status === 'parsing' && file.progress !== undefined && file.progress > 0 && (
                  <div className={s.progressRow}>
                    <div className={s.progressTrack}>
                      <div className={s.progressFill} style={{ width: `${file.progress}%` }} />
                    </div>
                    <span className={s.progressText}>{file.progress}%</span>
                  </div>
                )}
              </div>
              <span className={`${s.statusDot} ${statusClass}`} title={getStatusLabel(file.status, t)} />
              <div className={s.menuBtnWrap} onClick={(e) => e.stopPropagation()}>
                {confirmingFileId === file.id ? (
                  <div className={s.inlineConfirm}>
                    <span>{t('conversation.confirmDelete')}</span>
                    <button className={s.confirmCancelBtn} onClick={(e) => { e.stopPropagation(); cancelDelete() }}>
                      {t('conversation.cancel')}
                    </button>
                    <button className={s.confirmDeleteBtn} onClick={(e) => { e.stopPropagation(); handleDelete(file.id) }}>
                      {t('conversation.delete')}
                    </button>
                  </div>
                ) : (
                  <>
                    <button className={s.menuBtn} onClick={(e) => { e.stopPropagation(); setMenuOpenFileId(menuOpenFileId === file.id ? null : file.id) }}><MoreVertical size={15} /></button>
                    <div className={`${s.dropdownMenu} ${menuOpenFileId === file.id ? s.dropdownMenuOpen : ''}`}>
                      {file.status === 'error' && (
                        <button className={s.menuItem} onClick={(e) => { e.stopPropagation(); handleRetry(file.id) }}>
                          <RefreshCw size={13} />
                          {t('knowledge.retry')}
                        </button>
                      )}
                      <button className={`${s.menuItem} ${s.menuItemDanger}`} onClick={(e) => { e.stopPropagation(); handleDelete(file.id) }}>
                        <Trash2 size={13} />
                        {t('knowledge.deleteFile')}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
            )
          })}

          {/* Batch actions bar */}
          {hasSelection && (
            <div className={s.batchBar}>
              <span className={s.batchCount}>
                {t('knowledge.selectedCount', { count: selectedIds.size })}
              </span>
              <button className={s.batchBtn} onClick={handleBatchDelete}>
                {t('knowledge.batchDelete')}
              </button>
              <button className={s.batchBtn} onClick={handleBatchReprocess}>
                {t('knowledge.batchReprocess')}
              </button>
            </div>
          )}

          {/* Load more */}
          {hasMore && (
            <button className={s.loadMoreBtn} onClick={onLoadMore} disabled={loadingMore}>
              {loadingMore ? t('knowledge.loading') : t('knowledge.loadMore')}
            </button>
          )}

          {/* Retry all failed */}
          {files.some(f => f.status === 'error') && (
            <button className={s.retryAllBtn} onClick={handleRetryAll}>
              <RefreshCw size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />{t('knowledge.retryAllFailed')}
            </button>
          )}
        </div>
      )}

      {files.length === 0 && (
        <div className={s.emptyFiles}>
          <p>{t('knowledge.noFiles')}</p>
        </div>
      )}
    </div>
  )
}

function getStatusLabel(status: FileRecord['status'], t: (key: string) => string) {
  const map: Record<string, string> = {
    pending: t('knowledge.statusPending'),
    parsing: t('knowledge.statusParsing'),
    done: t('knowledge.statusDone'),
    error: t('knowledge.statusError'),
  }
  return map[status] || status
}

function getStatusDotClass(status: FileRecord['status']) {
  const map: Record<string, string> = {
    done: s.dotDone,
    error: s.dotError,
    parsing: s.dotParsing,
    pending: s.dotPending,
  }
  return map[status] || s.dotPending
}
