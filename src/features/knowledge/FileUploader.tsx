/** src/features/knowledge/FileUploader.tsx */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2, RefreshCw, MoreVertical, FileText, FileSpreadsheet, Globe, Image, ChevronLeft, ChevronRight, Pause, Play, ExternalLink } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { FileRecord, KnowledgeIpcChannels } from './types'
import { DEFAULT_KNOWLEDGE_IPC_CHANNELS } from './types'
import s from './FileUploader.module.css'
import { useConfirm } from '../../components/ConfirmDialog'
import { useToast } from './Toast'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import { translateStoredError } from '../../i18n/errorCodes'

interface FileUploaderProps {
  projectId: string
  files: FileRecord[]
  onRefresh: () => void
  filePage?: number
  fileTotal?: number
  fileLimit?: number
  onPageChange?: (page: number) => void
  loadingMore?: boolean
  selectedFileId?: string | null
  onSelectFile?: (fileId: string | null) => void
  onDeleteFile?: (fileIds: string[]) => void
  /** 文件解析队列暂停态（0.4 §队列暂停）：暂停 = 不再开始新解析，pending 排队等恢复 */
  parsingPaused?: boolean
  onToggleParsingPause?: () => void
  ipcChannels?: Partial<KnowledgeIpcChannels>
}

const FILE_ICONS: Record<string, LucideIcon> = {
  '.pdf': FileText,
  '.docx': FileText,
  '.doc': FileText,
  '.pptx': FileText,
  '.ppt': FileText,
  '.xlsx': FileSpreadsheet,
  '.xls': FileSpreadsheet,
  '.html': Globe,
  '.png': Image,
  '.jpg': Image,
  '.jpeg': Image,
}

export function FileUploader({ projectId, files, onRefresh, filePage = 1, fileTotal = 0, fileLimit = 20, onPageChange, selectedFileId, onSelectFile, onDeleteFile, parsingPaused, onToggleParsingPause, ipcChannels }: FileUploaderProps) {
  const ipc = { ...DEFAULT_KNOWLEDGE_IPC_CHANNELS, ...ipcChannels }
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const toast = useToast()
  const handleError = useErrorHandler()
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
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
    setMenuOpenFileId(null)
    if (!(await confirmDialog({ title: t('knowledge.deleteFile'), message: t('common.confirmDelete') }))) return
    try {
      await window.electronAPI.invoke(ipc.deleteFile, { projectId, fileId })
      onRefresh()
      onDeleteFile?.([fileId])
    } catch (err) {
      handleError(err)
    }
  }

  // 打开原文件（2.2 §10.4）：parsing/error 状态同样可用——文件上传即落盘，
  // 解析失败不影响原文件存在；成功无 toast（系统程序弹出即反馈）
  const handleOpenOriginal = async (file: FileRecord) => {
    setMenuOpenFileId(null)
    try {
      const result: any = await window.electronAPI.invoke('file:openOriginal', {
        projectId,
        fileId: file.id,
        filename: file.filename,
      })
      if (result && !result.success) {
        toast(
          result.reason === 'not_found'
            ? t('knowledge.originalNotFound', { defaultValue: '原文件不存在，可能已被删除' })
            : t('knowledge.openFailed', { defaultValue: '打开失败: {{reason}}', reason: result.message || result.reason }),
          'error',
        )
      }
    } catch (err) {
      console.error('Open original failed:', err)
      toast(t('knowledge.openFailed', { defaultValue: '打开失败: {{reason}}', reason: String(err) }), 'error')
    }
  }

  const handleRetry = async (fileId: string) => {
    try {
      await window.electronAPI.invoke(ipc.retryFile, { projectId, fileId })
      onRefresh()
    } catch (err) {
      handleError(err)
    }
  }

  const handleRetryAll = async () => {
    try {
      await window.electronAPI.invoke(ipc.retryFailedFiles, projectId)
      onRefresh()
    } catch (err) {
      handleError(err)
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
    if (!(await confirmDialog({ title: t('knowledge.batchDelete'), message: t('knowledge.confirmBatchDelete', { count: selectedIds.size }) }))) return
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
      handleError(err)
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
      handleError(err)
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
              {/* 标题计数用 fileTotal（当前筛选/搜索下总数，与分页一致）；
                  files.length 是当前页行数（满页时恰好等于每页上限，会被误读成总数） */}
              <span>{t('knowledge.uploadedFiles')} ({fileTotal})</span>
            </label>
            <div className={s.headerActions}>
              {/* 解析队列暂停开关（0.4 §队列暂停）：仅当父组件接入 useQueuePause 时显示 */}
              {onToggleParsingPause && (
                <button
                  className={`${s.refreshBtn} ${parsingPaused ? s.pauseBtnActive : ''}`}
                  onClick={onToggleParsingPause}
                  title={parsingPaused ? t('knowledge.resumeParsing') : t('knowledge.pauseParsing')}
                >
                  {parsingPaused ? <Play size={14} /> : <Pause size={14} />}
                </button>
              )}
              <button className={s.refreshBtn} onClick={onRefresh} title={t('knowledge.refresh')}>
                <RefreshCw size={14} />
              </button>
            </div>
          </div>
          {files.map((file) => {
            const statusClass = getStatusDotClass(file.status)
            const isParsing = file.status === 'parsing'
            return (
            <div
              key={file.id}
              className={`${s.fileItem} ${selectedIds.has(file.id) ? s.fileItemSelected : ''} ${selectedFileId === file.id ? s.fileItemActive : ''} ${isParsing ? s.fileItemParsing : ''}`}
              title={file.error_message ? translateStoredError(file.error_message, t) : undefined}
              onClick={() => onSelectFile?.(selectedFileId === file.id ? null : file.id)}
            >
              <input
                type="checkbox"
                className={s.fileCheckbox}
                checked={selectedIds.has(file.id)}
                onChange={(e) => { e.stopPropagation(); toggleSelect(file.id) }}
              />
              <span className={s.fileIcon}>{(() => { const Icon = FILE_ICONS[file.file_type] || FileText; return <Icon size={14} /> })()}</span>
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
                <button className={s.menuBtn} onClick={(e) => { e.stopPropagation(); setMenuOpenFileId(menuOpenFileId === file.id ? null : file.id) }}><MoreVertical size={15} /></button>
                <div className={`${s.dropdownMenu} ${menuOpenFileId === file.id ? s.dropdownMenuOpen : ''}`}>
                  <button className={s.menuItem} onClick={(e) => { e.stopPropagation(); void handleOpenOriginal(file) }}>
                    <ExternalLink size={13} />
                    {t('knowledge.openOriginal', { defaultValue: '打开原文件' })}
                  </button>
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

          {/* File pagination */}
          {(() => {
            const totalPages = Math.max(1, Math.ceil(fileTotal / fileLimit))
            if (totalPages <= 1) return null
            const pages: number[] = []
            if (totalPages <= 7) {
              for (let i = 1; i <= totalPages; i++) pages.push(i)
            } else if (filePage <= 4) {
              for (let i = 1; i <= 7; i++) pages.push(i)
            } else if (filePage >= totalPages - 3) {
              for (let i = totalPages - 6; i <= totalPages; i++) pages.push(i)
            } else {
              for (let i = filePage - 3; i <= filePage + 3; i++) pages.push(i)
            }
            return (
              <div className={s.filePagination}>
                <button className={s.pageBtn} disabled={filePage <= 1} onClick={() => onPageChange?.(filePage - 1)}>
                  <ChevronLeft size={14} />
                </button>
                {pages.map(p => (
                  <button key={p} className={`${s.pageBtn} ${p === filePage ? s.pageBtnActive : ''}`} onClick={() => onPageChange?.(p)}>
                    {p}
                  </button>
                ))}
                <button className={s.pageBtn} disabled={filePage >= totalPages} onClick={() => onPageChange?.(filePage + 1)}>
                  <ChevronRight size={14} />
                </button>
                <span className={s.pageInfo}>{filePage} / {totalPages}</span>
              </div>
            )
          })()}

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
