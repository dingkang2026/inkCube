/** src/features/knowledge/KnowledgePage.tsx */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, Search, Image as ImageIcon, FileText } from 'lucide-react'
import { FileUploader } from './FileUploader'
import { ImageGallery } from './ImageGallery'
import { SearchBar } from './SearchBar'
import { SearchResultList } from './SearchResultList'
import { ToastProvider, useToast } from './Toast'
import { SkeletonFileList, SkeletonSearchResults } from './SkeletonLoader'
import type { Project, FileRecord, SearchResult } from './types'
import s from './KnowledgePage.module.css'

interface KnowledgePageProps {
  selectedProject: Project | null
  onOpenSettings?: () => void
  onProjectDataChanged?: () => void
  /** When true, hide project name/description header (used when embedded in ProjectDetailPage) */
  compact?: boolean
  /** Called when file selection changes (for chat scope tracking) */
  onFileSelect?: (fileId: string | null, filename: string | null) => void
}

type OutputTab = 'search' | 'images'
type FileStatusFilter = 'all' | 'processing' | 'done' | 'error'

const STATUS_FILTERS: { key: FileStatusFilter; labelKey: string }[] = [
  { key: 'all', labelKey: 'knowledge.filterAll' },
  { key: 'processing', labelKey: 'knowledge.filterProcessing' },
  { key: 'done', labelKey: 'knowledge.filterDone' },
  { key: 'error', labelKey: 'knowledge.filterError' },
]

export function KnowledgePage({ selectedProject, onOpenSettings, onProjectDataChanged, compact, onFileSelect }: KnowledgePageProps) {
  return (
    <ToastProvider>
      <KnowledgePageInner selectedProject={selectedProject} onOpenSettings={onOpenSettings} onProjectDataChanged={onProjectDataChanged} compact={compact} onFileSelect={onFileSelect} />
    </ToastProvider>
  )
}

function KnowledgePageInner({ selectedProject, onOpenSettings: _onOpenSettings, onProjectDataChanged, compact, onFileSelect }: KnowledgePageProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [files, setFiles] = useState<FileRecord[]>([])
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [recentChunks, setRecentChunks] = useState<SearchResult[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [outputTab, setOutputTab] = useState<OutputTab>('search')
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [statusFilter, setStatusFilter] = useState<FileStatusFilter>('all')
  const [filenameSearch, setFilenameSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [initialLoading, setInitialLoading] = useState(true)
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null)

  // Upload state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0, filename: '' })
  const [dragOver, setDragOver] = useState(false)
  const filenameSearchRef = useRef(filenameSearch)
  filenameSearchRef.current = filenameSearch

  const ACCEPT = ['.pdf', '.docx', '.pptx', '.xlsx', '.html', '.png', '.jpg', '.jpeg'].join(',')

  const loadRecentChunks = useCallback(async (projectId: string, fileId?: string | null) => {
    try {
      const result: any = await window.electronAPI.invoke('knowledge:recentChunks', {
        projectId,
        limit: 20,
        fileId: fileId || undefined,
      })
      setRecentChunks(result.results || [])
    } catch (err) {
      console.error('Failed to load recent chunks:', err)
    }
  }, [])

  const loadFiles = useCallback(async (projectId: string, search?: string) => {
    try {
      const result: any = await window.electronAPI.invoke('knowledge:getFiles', {
        projectId,
        limit: 20,
        filenameSearch: search || undefined,
      })
      setFiles(result.files || [])
      setHasMore(result.has_more || false)
    } catch (err) {
      console.error('Failed to load files:', err)
    } finally {
      setInitialLoading(false)
    }
  }, [])

  const processFiles = useCallback(
    async (fileList: FileList) => {
      if (!selectedProject) return
      const arr = Array.from(fileList)
      setUploading(true)
      setUploadProgress({ current: 0, total: arr.length, filename: '' })
      try {
        for (let i = 0; i < arr.length; i++) {
          const file = arr[i]
          setUploadProgress({ current: i + 1, total: arr.length, filename: file.name })
          const base64 = await readFileAsBase64(file)
          const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
          const result = await window.electronAPI.invoke('knowledge:uploadFile', {
            projectId: selectedProject.id,
            filename: file.name,
            fileType: ext,
            data: base64,
          }) as { success?: boolean; message?: string; error?: string }
          if (result && !result.success) {
            showToast(`${file.name}: ${result.message || result.error || 'Upload failed'}`, 'error')
          }
          // Refresh file list after each upload so the new file appears immediately
          loadFiles(selectedProject.id, filenameSearchRef.current)
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        showToast(t('knowledge.uploadFailed') + ': ' + msg, 'error')
      } finally {
        setUploading(false)
        setUploadProgress({ current: 0, total: 0, filename: '' })
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
    },
    [selectedProject, loadFiles, showToast, t]
  )

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length) processFiles(e.dataTransfer.files)
  }

  // Compute processing status for header
  const processingCount = useMemo(
    () => files.filter(f => f.status === 'parsing' || f.status === 'pending').length,
    [files]
  )

  // Filter files by status (client-side)
  const filteredFiles = useMemo(() => {
    if (statusFilter === 'all') return files
    return files.filter(f => {
      switch (statusFilter) {
        case 'processing': return f.status === 'parsing' || f.status === 'pending'
        case 'done': return f.status === 'done'
        case 'error': return f.status === 'error'
        default: return true
      }
    })
  }, [files, statusFilter])

  // Reset UI state when project changes
  useEffect(() => {
    if (selectedProject) {
      setSelectedFileId(null)
      onFileSelect?.(null, null)
    }
  }, [selectedProject?.id])

  // Notify parent when file selection changes (for chat scope)
  const prevNotifiedFilenameRef = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    const filename = selectedFileId ? (files.find(f => f.id === selectedFileId)?.filename ?? null) : null
    // Only notify when the resolved filename actually changes
    if (filename !== prevNotifiedFilenameRef.current) {
      prevNotifiedFilenameRef.current = filename
      onFileSelect?.(selectedFileId, filename)
    }
  }, [selectedFileId, files])

  // Reload files and recent chunks when project, search, or file selection changes
  useEffect(() => {
    if (selectedProject) {
      loadFiles(selectedProject.id, filenameSearch)
      loadRecentChunks(selectedProject.id, selectedFileId)
      setHasSearched(false)
      setSearchResults([])
    }
  }, [selectedProject?.id, filenameSearch, selectedFileId, loadFiles, loadRecentChunks])

  const loadMoreFiles = async () => {
    if (!selectedProject || loadingMore || !hasMore) return
    setLoadingMore(true)
    try {
      const lastFile = files[files.length - 1]
      const result: any = await window.electronAPI.invoke('knowledge:getFiles', {
        projectId: selectedProject.id,
        limit: 20,
        beforeCreatedAt: lastFile.created_at,
        beforeId: lastFile.id,
        filenameSearch: filenameSearch || undefined,
      })
      setFiles(prev => [...prev, ...(result.files || [])])
      setHasMore(result.has_more || false)
    } catch (err) {
      console.error('Failed to load more files:', err)
    } finally {
      setLoadingMore(false)
    }
  }

  // Poll active files for status updates
  const prevActiveCountRef = useRef(-1)
  useEffect(() => {
    if (!selectedProject) return
    const hasActive = files.some(f =>
      f.status === 'pending' || f.status === 'parsing' ||
      f.image_status === 'pending' || f.image_status === 'describing'
    )
    if (!hasActive) {
      prevActiveCountRef.current = -1
      return
    }

    const timer = setInterval(async () => {
      try {
        const result: any = await window.electronAPI.invoke(
          'knowledge:getActiveFiles', selectedProject.id
        )
        const activeFiles = result.files || []

        // Detect files that just finished (count dropped)
        const prevCount = prevActiveCountRef.current
        const curCount = activeFiles.length
        prevActiveCountRef.current = curCount

        if (curCount === 0) {
          // All processing done — full refresh including related data
          loadFiles(selectedProject.id, filenameSearch)
          loadRecentChunks(selectedProject.id, selectedFileId)
          onProjectDataChanged?.()
          return
        }

        if (prevCount > 0 && curCount < prevCount) {
          // Some files just finished — refresh related data
          loadRecentChunks(selectedProject.id, selectedFileId)
          onProjectDataChanged?.()
        }

        setFiles(prev => prev.map(f => {
          const updated = activeFiles.find((af: FileRecord) => af.id === f.id)
          return updated ? { ...f, ...updated } : f
        }))
      } catch (err) {
        console.error('Polling failed:', err)
      }
    }, 2000)

    return () => {
      clearInterval(timer)
      prevActiveCountRef.current = -1
    }
  }, [files, selectedProject?.id])

  const handleSearch = async (query: string, topK: number = 5) => {
    if (!selectedProject || !query.trim()) {
      setSearchResults([])
      setHasSearched(true)
      return
    }
    setIsSearching(true)
    setOutputTab('search')
    setHasSearched(true)
    try {
      const result: any = await window.electronAPI.invoke('knowledge:search', {
        projectId: selectedProject.id,
        query,
        topK,
        fileId: selectedFileId || undefined,
      })
      setSearchResults(result.results || [])
    } catch (err) {
      console.error('Search failed:', err)
      setSearchResults([])
    } finally {
      setIsSearching(false)
    }
  }


  const handleFileSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setFilenameSearch(searchInput.trim())
  }

  const handleClearFileSearch = () => {
    setSearchInput('')
    setFilenameSearch('')
  }

  const handleDeleteFile = useCallback((fileIds: string[]) => {
    const idSet = new Set(fileIds)
    // Clear selected file scope if deleted
    if (selectedFileId && idSet.has(selectedFileId)) {
      setSelectedFileId(null)
    }
    // Refresh recent chunks (may contain chunks from deleted file)
    if (selectedProject) {
      loadRecentChunks(selectedProject.id, selectedFileId)
    }
  }, [selectedFileId, selectedProject, loadRecentChunks])

  if (!selectedProject) {
    return (
      <div className={s.page}>
        <div className={s.emptyState}>
          <div className={s.emptyIcon}><BookOpen size={28} /></div>
          <h3>{t('knowledge.welcomeTitle')}</h3>
          <p>{t('knowledge.welcomeDesc')}</p>
        </div>
      </div>
    )
  }

  const processingTotal = files.length

  return (
    <div className={s.page}>
      {/* Project header — hidden in compact mode (already shown in ProjectDetailPage header) */}
      {!compact && (
        <div className={s.projectHeader}>
          <div className={s.projectInfoRow}>
            <div className={s.projectInfo}>
              <h3 className={s.projectName}>{selectedProject.name}</h3>
              <div className={s.projectStats}>
                <span className={s.statBadge}>
                  <span className={s.statNum}>{selectedProject.file_count}</span>
                  {t('knowledge.files')}
                </span>
                <span className={s.statDivider} />
                <span className={s.statBadge}>
                  <span className={s.statNum}>{selectedProject.chunk_count}</span>
                  {t('knowledge.chunks')}
                </span>
                {processingCount > 0 && (
                  <>
                    <span className={s.statDivider} />
                    <span className={s.statProcessing}>
                      {t('knowledge.processingStatus', { done: processingTotal - processingCount, total: processingTotal })}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>
          {selectedProject.description && (
            <p className={s.projectDesc}>{selectedProject.description}</p>
          )}
        </div>
      )}

      {/* Main area: Input | Output */}
      <div className={s.mainArea}>
        {/* ── Input panel (left) ── */}
        <div className={s.inputPanel}>
          <div className={s.inputHeader}>
            <form className={s.inputSearchForm} onSubmit={handleFileSearchSubmit}>
              <div className={s.inputSearchWrap}>
                <span className={s.inputSearchIcon}><Search size={14} /></span>
                <input
                  type="text"
                  className={s.inputSearchInput}
                  placeholder={t('knowledge.searchFilesPlaceholder')}
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                />
                {searchInput && (
                  <button type="button" className={s.inputSearchClear} onClick={handleClearFileSearch}>
                    ×
                  </button>
                )}
              </div>
            </form>
            <div className={s.statusFilter}>
              {STATUS_FILTERS.map(f => (
                <button
                  key={f.key}
                  className={`${s.statusFilterBtn} ${statusFilter === f.key ? s.statusFilterBtnActive : ''}`}
                  onClick={() => setStatusFilter(f.key)}
                >
                  {t(f.labelKey)}
                </button>
              ))}
            </div>
          </div>
          {/* Upload zone — always visible */}
          <div
            className={`${s.uploadZone} ${dragOver ? s.uploadZoneDragOver : ''}`}
            onDrop={handleDrop}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPT}
              onChange={(e) => { if (e.target.files?.length) processFiles(e.target.files) }}
              disabled={uploading}
              style={{ display: 'none' }}
            />
            <span className={s.uploadText} title={uploading ? `${uploadProgress.current}/${uploadProgress.total} — ${uploadProgress.filename}` : undefined}>
              {uploading
                ? t('knowledge.uploading') + ` ${uploadProgress.current}/${uploadProgress.total} — ${uploadProgress.filename}`
                : '+ ' + t('knowledge.dropZoneCompact')}
            </span>
          </div>
          <div className={s.inputContent}>
            {initialLoading ? (
              <div className={s.skeletonWrap}>
                <SkeletonFileList rows={4} />
              </div>
            ) : (
              <FileUploader
                projectId={selectedProject.id}
                files={filteredFiles}
                onRefresh={() => loadFiles(selectedProject.id, filenameSearch)}
                hasMore={hasMore}
                onLoadMore={loadMoreFiles}
                loadingMore={loadingMore}
                selectedFileId={selectedFileId}
                onSelectFile={setSelectedFileId}
                onDeleteFile={handleDeleteFile}
              />
            )}
          </div>
        </div>

        {/* ── Output panel (right) ── */}
        <div className={s.outputPanel}>
          <div className={s.outputTabBar}>
            <button
              className={`${s.outputTab} ${outputTab === 'search' ? s.outputTabActive : ''}`}
              onClick={() => setOutputTab('search')}
            >
              <FileText size={14} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />{t('knowledge.tabChunks')}
            </button>
            <button
              className={`${s.outputTab} ${outputTab === 'images' ? s.outputTabActive : ''}`}
              onClick={() => setOutputTab('images')}
            >
              <ImageIcon size={14} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />{t('knowledge.tabImages')}
            </button>
            {selectedFileId && (
              <span className={s.scopeIndicator}>
                {files.find(f => f.id === selectedFileId)?.filename}
                <button className={s.scopeClear} onClick={() => setSelectedFileId(null)} title={t('knowledge.clearSelection')}>×</button>
              </span>
            )}
          </div>
          <div className={s.outputContent}>
            {outputTab === 'search' ? (
              <div className={s.searchPanel}>
                <SearchBar
                  onSearch={handleSearch}
                  isSearching={isSearching}
                  placeholder={t('knowledge.searchPlaceholder')}
                />
                {isSearching ? (
                  <div className={s.skeletonWrap}>
                    <SkeletonSearchResults count={3} />
                  </div>
                ) : searchResults.length > 0 ? (
                  <SearchResultList results={searchResults} />
                ) : hasSearched ? (
                  <div className={s.emptySearch}>
                    <div className={s.emptyIcon}><Search size={20} /></div>
                    <p>{t('knowledge.noSearchResults')}</p>
                  </div>
                ) : recentChunks.length > 0 ? (
                  <SearchResultList results={recentChunks} isRecent />
                ) : (
                  <div className={s.emptySearch}>
                    <div className={s.emptyIcon}><Search size={20} /></div>
                    <p>{t('knowledge.searchHint')}</p>
                  </div>
                )}
              </div>
            ) : (
              <div className={s.imagePanel}>
                <ImageGallery projectId={selectedProject.id} fileId={selectedFileId || undefined} />
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  )
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
