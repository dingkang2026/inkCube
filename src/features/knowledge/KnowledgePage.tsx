/** src/features/knowledge/KnowledgePage.tsx */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, Image as ImageIcon, FileText } from 'lucide-react'
import { FileUploader } from './FileUploader'
import { ImageGallery } from './ImageGallery'
import { SearchBar } from './SearchBar'
import { SearchResultList } from './SearchResultList'
import { useToast } from './Toast'
import { SkeletonFileList, SkeletonSearchResults } from './SkeletonLoader'
import { useFeatureGuard } from '../readiness/useReadiness'
import { useActivity } from '../activity/ActivityContext'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { useQueuePause } from '../../hooks/useQueuePause'
import type { Project, FileRecord, SearchResult, KnowledgeIpcChannels } from './types'
import { DEFAULT_KNOWLEDGE_IPC_CHANNELS } from './types'
import { apiErrorText } from '../../i18n/errorCodes'
import s from './KnowledgePage.module.css'

interface KnowledgePageProps {
  selectedProject: Project | null
  onOpenSettings?: () => void
  onProjectDataChanged?: () => void
  /** When true, hide project name/description header (used when embedded in ProjectDetailPage) */
  compact?: boolean
  /** Called when file selection changes (for chat scope tracking) */
  onFileSelect?: (fileId: string | null, filename: string | null) => void
  /** IPC channel name mapping — override to use different backend (e.g. baseLib:* channels) */
  ipcChannels?: Partial<KnowledgeIpcChannels>
  /** 零项目欢迎态内容(0.5 §4.3 三步引导卡):嵌入场景 selectedProject 恒非空,此分支不可达 */
  welcome?: ReactNode
}

type OutputTab = 'search' | 'images'
type FileStatusFilter = 'all' | 'processing' | 'done' | 'error'

// File panel drag resize bounds（右缘拖拽，与 chat/preview 面板同一交互模式）
const PANEL_MIN_WIDTH = 260
const PANEL_MAX_WIDTH = 960
const PANEL_DEFAULT_WIDTH = 320

const STATUS_FILTERS: { key: FileStatusFilter; labelKey: string }[] = [
  { key: 'all', labelKey: 'knowledge.filterAll' },
  { key: 'processing', labelKey: 'knowledge.filterProcessing' },
  { key: 'done', labelKey: 'knowledge.filterDone' },
  { key: 'error', labelKey: 'knowledge.filterError' },
]

export function KnowledgePage({ selectedProject, onOpenSettings, onProjectDataChanged, compact, onFileSelect, ipcChannels, welcome }: KnowledgePageProps) {
  const channels = { ...DEFAULT_KNOWLEDGE_IPC_CHANNELS, ...ipcChannels }
  return (
    <KnowledgePageInner selectedProject={selectedProject} onOpenSettings={onOpenSettings} onProjectDataChanged={onProjectDataChanged} compact={compact} onFileSelect={onFileSelect} ipc={channels} welcome={welcome} />
  )
}

function KnowledgePageInner({ selectedProject, onOpenSettings: _onOpenSettings, onProjectDataChanged, compact, onFileSelect, ipc, welcome }: KnowledgePageProps & { ipc: KnowledgeIpcChannels }) {
  const { t } = useTranslation()
  const showToast = useToast()
  const guard = useFeatureGuard()
  // 队列停止开关（0.4 §队列暂停，UI 呈现为停止/继续）：解析/描述各自独立；基础库共用同一队列，
  // 经 ipcChannels 覆写的页面同样受同一开关控制
  const parsingPause = useQueuePause('parsing')
  const describePause = useQueuePause('describe')
  const [files, setFiles] = useState<FileRecord[]>([])
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [recentChunks, setRecentChunks] = useState<SearchResult[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [outputTab, setOutputTab] = useState<OutputTab>('images')
  const [fileTotal, setFileTotal] = useState(0)
  // 状态分布（搜索口径、不含状态筛选）——筛选 Tab 计数源；随每次 loadFiles 刷新
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [filePage, setFilePage] = useState(1)
  const [loadingMore, setLoadingMore] = useState(false)
  const [statusFilter, setStatusFilter] = useState<FileStatusFilter>('all')
  const [filenameSearch, setFilenameSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [initialLoading, setInitialLoading] = useState(true)
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null)
  // File panel width (drag-resizable, default 320px)
  const [panelWidth, setPanelWidth] = useState(PANEL_DEFAULT_WIDTH)
  const [isPanelDragging, setIsPanelDragging] = useState(false)
  const panelDragging = useRef(false)
  const panelDragStartX = useRef(0)
  const panelDragStartWidth = useRef(PANEL_DEFAULT_WIDTH)

  // Upload state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState({ current: 0, total: 0, filename: '' })
  const [dragOver, setDragOver] = useState(false)
  const filenameSearchRef = useRef(filenameSearch)
  filenameSearchRef.current = filenameSearch
  // 状态筛选服务端下沉：ref 供轮询/边沿补拉等回调读到最新筛选（切换时由按钮显式传新值）
  const statusFilterRef = useRef<FileStatusFilter>(statusFilter)
  statusFilterRef.current = statusFilter
  // 轮询/边沿补拉刷新保持用户当前页：硬编码 page=1 会在后台解析完成时把正在
  // 浏览第 N 页的用户强行拉回首页
  const filePageRef = useRef(filePage)
  filePageRef.current = filePage

  const ACCEPT = ['.pdf', '.docx', '.pptx', '.xlsx', '.html', '.png', '.jpg', '.jpeg'].join(',')

  const loadRecentChunks = useCallback(async (projectId: string, fileId?: string | null) => {
    try {
      const result: any = await window.electronAPI.invoke(ipc.recentChunks, {
        projectId,
        limit: 20,
        fileId: fileId || undefined,
      })
      setRecentChunks(result.results || [])
    } catch (err) {
      console.error('Failed to load recent chunks:', err)
    }
  }, [])

  const FILE_LIMIT = 20

  const loadFiles = useCallback(async (projectId: string, page = 1, search?: string, status?: FileStatusFilter) => {
    const fetchPage = async (p: number) => {
      // status 缺省读 ref（轮询/翻页/补拉沿用当前筛选），切换筛选时由按钮显式传新值
      const effectiveStatus = status ?? statusFilterRef.current
      return window.electronAPI.invoke(ipc.getFiles, {
        projectId,
        limit: FILE_LIMIT,
        offset: (p - 1) * FILE_LIMIT,
        filenameSearch: search || undefined,
        status: effectiveStatus === 'all' ? undefined : effectiveStatus,
      })
    }
    try {
      const result: any = await fetchPage(page)
      setStatusCounts(result.status_counts || {})
      const rows = result.files || []
      const total = result.total || 0
      // 越界自愈：总数缩小（解析完成/删除）后保持中的当前页可能越界变空页 → 回退末页
      if (!rows.length && page > 1 && total > 0) {
        const lastPage = Math.max(1, Math.ceil(total / FILE_LIMIT))
        if (lastPage < page) {
          const retry: any = await fetchPage(lastPage)
          setStatusCounts(retry.status_counts || {})
          setFiles(retry.files || [])
          setFileTotal(retry.total || 0)
          setFilePage(lastPage)
          return
        }
      }
      setFiles(rows)
      setFileTotal(total)
      setFilePage(page)
    } catch (err) {
      console.error('Failed to load files:', err)
    } finally {
      setInitialLoading(false)
    }
  }, [])

  const doProcessFiles = useCallback(
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
          const result = await window.electronAPI.invoke(ipc.uploadFile, {
            projectId: selectedProject.id,
            filename: file.name,
            fileType: ext,
            data: base64,
          }) as { success?: boolean; message?: string; error?: string; code?: string }
          if (result && !result.success) {
            showToast(`${file.name}: ${apiErrorText(result.code, result.message || result.error || t('knowledge.uploadFailed'), t)}`, 'error')
          }
          // Refresh file list after each upload so the new file appears immediately
          loadFiles(selectedProject.id, 1, filenameSearchRef.current)
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

  // 入口 guard（0.4 §7.3 第二层）：缺解析/embedding 模型 → 引导弹层
  const processFiles = (fileList: FileList) => {
    void guard('file_parsing', () => {
      void doProcessFiles(fileList)
    })
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length) processFiles(e.dataTransfer.files)
  }

  // ── File panel drag resize ──
  const handlePanelDragMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    panelDragging.current = true
    setIsPanelDragging(true)
    panelDragStartX.current = e.clientX
    panelDragStartWidth.current = panelWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [panelWidth])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!panelDragging.current) return
      // 面板在左侧：向右拖增宽
      const delta = e.clientX - panelDragStartX.current
      const newWidth = Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, panelDragStartWidth.current + delta))
      setPanelWidth(newWidth)
    }
    const handleMouseUp = () => {
      if (panelDragging.current) {
        panelDragging.current = false
        setIsPanelDragging(false)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  // Compute processing status for header
  const processingCount = useMemo(
    () => files.filter(f => f.status === 'parsing' || f.status === 'pending').length,
    [files]
  )

  // 筛选 Tab 计数：全部=各状态求和；处理中=排队+解析中（pending+parsing，与后端筛选同口径）
  const filterCount = (key: FileStatusFilter) => {
    if (key === 'all') return Object.values(statusCounts).reduce((a, b) => a + b, 0)
    if (key === 'processing') return (statusCounts.pending || 0) + (statusCounts.parsing || 0)
    return statusCounts[key] || 0
  }

  // 状态筛选已下沉服务端（跨页有效），files 即当前筛选下的当前页快照

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

  // 文件列表重置：仅换项目/改文件名搜索时回第 1 页（并重置输出面板）。
  // 选中文件（selectedFileId）不在此列——它只影响最近片段的作用域，
  // 不能把列表拉回第 1 页（bug：点击行选中 → 列表跳回首页）
  useEffect(() => {
    if (selectedProject) {
      loadFiles(selectedProject.id, 1, filenameSearch)
      setHasSearched(false)
      setSearchResults([])
    }
  }, [selectedProject?.id, filenameSearch, loadFiles])

  // 最近片段跟随项目与文件选中变化（后者只切作用域，不动分页）
  useEffect(() => {
    if (selectedProject) {
      loadRecentChunks(selectedProject.id, selectedFileId)
    }
  }, [selectedProject?.id, selectedFileId, loadRecentChunks])

  const handleFilePageChange = async (page: number) => {
    if (!selectedProject || loadingMore || page < 1) return
    const totalPages = Math.max(1, Math.ceil(fileTotal / FILE_LIMIT))
    if (page > totalPages) return
    setLoadingMore(true)
    try {
      await loadFiles(selectedProject.id, page, filenameSearch)
    } finally {
      setLoadingMore(false)
    }
  }

  // Poll active files for status updates（#5，v1.7 迁移 usePolling；谓词收编全局心跳 useActivity('file')，
  // 0.4 §6.2——本地 files 是分页/搜索过滤后的快照，活跃文件不在当前页时本地谓词漏判熄火；
  // 基础库复用本页（baseLib:* 通道）时同样生效，活跃项 container='baselib'）
  const prevActiveCountRef = useRef(-1)
  const { items: activeFileItems } = useActivity('file')
  const hasActiveFiles = !!selectedProject && activeFileItems.some(a =>
    a.project_id === selectedProject.id && (a.count ?? 0) > 0
  )
  usePolling({
    name: 'knowledge-active-files',
    enabled: hasActiveFiles,
    interval: 2000,
    task: async () => {
      if (!selectedProject) return
      // 抛错由 usePolling 计入连续失败并退避（0.4 §5.8）
      const result: any = await window.electronAPI.invoke(
        ipc.getActiveFiles, selectedProject.id
      )
      const activeFiles = result.files || []

      // Detect files that just finished (count dropped)
      const prevCount = prevActiveCountRef.current
      const curCount = activeFiles.length
      prevActiveCountRef.current = curCount

      if (curCount === 0) {
        // All processing done — full refresh including related data
        loadFiles(selectedProject.id, filePageRef.current, filenameSearchRef.current)
        loadRecentChunks(selectedProject.id, selectedFileId)
        onProjectDataChanged?.()
        return
      }

      if (prevCount > 0 && curCount < prevCount) {
        // Some files just finished — refresh related data
        // 部分完成也要重拉列表：置顶排序的行位置与筛选 Tab 计数都来自
        // 服务端（本地 setFiles 合并只更新行内状态，不重排也不刷计数）
        loadFiles(selectedProject.id, filePageRef.current, filenameSearchRef.current)
        loadRecentChunks(selectedProject.id, selectedFileId)
        onProjectDataChanged?.()
      }

      setFiles(prev => prev.map(f => {
        const updated = activeFiles.find((af: FileRecord) => af.id === f.id)
        return updated ? { ...f, ...updated } : f
      }))
    },
  })
  // 终态边沿补拉（0.4 §6.3）：心跳先行翻 false 时轮询停摆可能早于 task 内
  // curCount===0 的全量刷新分支 → 边沿补拉明细与关联数据
  useEdgeRefetch(hasActiveFiles, () => {
    if (!selectedProject) return
    void loadFiles(selectedProject.id, filePageRef.current, filenameSearchRef.current)
    void loadRecentChunks(selectedProject.id, selectedFileId)
    onProjectDataChanged?.()
  })

  // 无活跃文件时重置完成计数基准（原 effect cleanup 逻辑）
  useEffect(() => {
    if (!hasActiveFiles) prevActiveCountRef.current = -1
  }, [hasActiveFiles])

  const runSearch = async (query: string, topK: number = 5) => {
    if (!selectedProject || !query.trim()) {
      setSearchResults([])
      setHasSearched(true)
      return
    }
    setIsSearching(true)
    setOutputTab('search')
    setHasSearched(true)
    try {
      const result: any = await window.electronAPI.invoke(ipc.search, {
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

  // 入口 guard（0.4 §7.3 第二层）：检索依赖 embedding 模型，缺失时引导下载
  const handleSearch = (query: string, topK?: number) => {
    void guard('knowledge_search', () => {
      void runSearch(query, topK)
    })
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
    // 零项目欢迎态:内容由调用方注入(0.5 §4.3 三步引导卡);嵌入场景(详情页/基础库)selectedProject 恒非空,此分支不可达
    return (
      <div className={`${s.page} ${s.pageWelcome}`}>
        {welcome ?? null}
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
        {/* ── Input panel (left, drag-resizable) ── */}
        <div className={s.inputPanel} style={{ width: panelWidth }}>
          {/* Drag handle — right edge, drag to resize (260~960px) */}
          <div
            className={`${s.panelDragHandle} ${isPanelDragging ? s.panelDragHandleActive : ''}`}
            onMouseDown={handlePanelDragMouseDown}
          />
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
                  onClick={() => {
                    setStatusFilter(f.key)
                    loadFiles(selectedProject.id, 1, filenameSearch, f.key)
                  }}
                >
                  {t(f.labelKey)}{filterCount(f.key) > 0 ? ` (${filterCount(f.key)})` : ''}
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
                files={files}
                onRefresh={() => loadFiles(selectedProject.id, filePage, filenameSearch)}
                filePage={filePage}
                fileTotal={fileTotal}
                fileLimit={FILE_LIMIT}
                onPageChange={handleFilePageChange}
                loadingMore={loadingMore}
                selectedFileId={selectedFileId}
                onSelectFile={setSelectedFileId}
                onDeleteFile={handleDeleteFile}
                parsingPaused={parsingPause.paused}
                onToggleParsingPause={() => void parsingPause.toggle()}
                ipcChannels={ipc}
              />
            )}
          </div>
        </div>

        {/* ── Output panel (right) ── */}
        <div className={s.outputPanel}>
          <div className={s.outputTabBar}>
            <button
              className={`${s.outputTab} ${outputTab === 'images' ? s.outputTabActive : ''}`}
              onClick={() => setOutputTab('images')}
            >
              <ImageIcon size={14} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />{t('knowledge.tabImages')}
            </button>
            <button
              className={`${s.outputTab} ${outputTab === 'search' ? s.outputTabActive : ''}`}
              onClick={() => setOutputTab('search')}
            >
              <FileText size={14} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 4 }} />{t('knowledge.tabChunks')}
            </button>
            {selectedFileId && (
              <span className={s.scopeIndicator}>
                <span className={s.scopeName} title={files.find(f => f.id === selectedFileId)?.filename}>
                  {files.find(f => f.id === selectedFileId)?.filename}
                </span>
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
                <ImageGallery
                  projectId={selectedProject.id}
                  fileId={selectedFileId || undefined}
                  describePaused={describePause.paused}
                  onToggleDescribePause={() => void describePause.toggle()}
                  ipcChannels={ipc}
                />
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
