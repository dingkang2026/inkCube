/** src/features/project/tabs/WikiTab.tsx — Wiki tab: container with sidebar + view routing */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  BookOpen, RefreshCw, AlertCircle, CheckCircle, Clock,
  AlertTriangle, Loader2, Home, Shield, Search, Share2,
  Maximize2, Minimize2,
} from 'lucide-react'
import type { ProjectType } from '../types'
import { WikiHomePage } from './WikiHomePage'
import { WikiPageReader } from './WikiPageReader'
import { WikiPageEditor } from './WikiPageEditor'
import { WikiChangeLog } from './WikiChangeLog'
import { WikiSearchView } from './WikiSearchView'
import { WikiGraphView } from './WikiGraphView'
import { WikiLintView } from './WikiLintView'
import { SupervisedCompileDialog } from './SupervisedCompileDialog'
import s from './WikiTab.module.css'

interface WikiTabProps {
  projectId: string
  projectType: ProjectType
  /** Whether Wiki is in expanded (immersive reading) mode */
  expanded?: boolean
  /** Callback when user toggles expand mode */
  onExpandChange?: (expanded: boolean) => void
  /** Callback when preview panel opens/closes */
  onPreviewOpenChange?: (isOpen: boolean) => void
  /** Signal from parent to close preview (increment to trigger) */
  closePreviewSignal?: number
}

interface WikiPage {
  id: string
  project_id: string
  page_key: string
  title: string
  content: string | null
  status: string
  compile_version: number
  page_type: string
  refs: string[]
  sources: string[]
  tags: string[]
  last_compiled_at: string | null
  created_at: string
  updated_at: string
}

interface CompileTask {
  id: string
  status: string
  stage: string | null
  pages_total: number
  pages_completed: number
  pages_failed: number
  error_message: string | null
  created_at: string
  completed_at: string | null
}

interface Review {
  id: string
  type: string
  title: string
  description: string
  resolved: number
  created_at: string
}

type ViewMode = 'home' | 'read' | 'edit' | 'changelog' | 'search' | 'graph' | 'lint'

const PREVIEW_DEFAULT_WIDTH = 720
const PREVIEW_MIN_WIDTH = 260
const PREVIEW_MAX_WIDTH = 720

export function WikiTab({ projectId, projectType: _projectType, expanded, onExpandChange, onPreviewOpenChange, closePreviewSignal }: WikiTabProps) {
  const { t } = useTranslation()
  const [pages, setPages] = useState<WikiPage[]>([])
  const [pendingCount, setPendingCount] = useState(0)
  const [purpose, setPurpose] = useState('')
  const [overview, setOverview] = useState('')
  const [sourcePages, setSourcePages] = useState<any[]>([])
  const [selectedPage, setSelectedPage] = useState<WikiPage | null>(null)
  const [pageDetail, setPageDetail] = useState<any>(null)
  const [compileTask, setCompileTask] = useState<CompileTask | null>(null)
  const [reviews, setReviews] = useState<Review[]>([])
  const [loading, setLoading] = useState(true)
  const [compiling, setCompiling] = useState(false)
  const [viewMode, setViewMode] = useState<ViewMode>('home')
  const [ingestSummaries, setIngestSummaries] = useState<any[]>([])
  const [isResumed, setIsResumed] = useState(false)
  const [showCompileDialog, setShowCompileDialog] = useState(false)
  const [showRecompileConfirm, setShowRecompileConfirm] = useState(false)
  const [graphHighlightNode, setGraphHighlightNode] = useState<string | undefined>(undefined)
  const [previewPageKey, setPreviewPageKey] = useState<string | null>(null)
  const [isEditing, setIsEditing] = useState(false)
  const [previewWidth, setPreviewWidth] = useState(PREVIEW_DEFAULT_WIDTH)
  const [isPreviewDragging, setIsPreviewDragging] = useState(false)
  const previewDragging = useRef(false)
  const previewDragStartX = useRef(0)
  const previewDragStartWidth = useRef(PREVIEW_DEFAULT_WIDTH)

  // ── Preview drag resize ──
  const handlePreviewDragMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    previewDragging.current = true
    setIsPreviewDragging(true)
    previewDragStartX.current = e.clientX
    previewDragStartWidth.current = previewWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [previewWidth])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!previewDragging.current) return
      const delta = previewDragStartX.current - e.clientX
      const newWidth = Math.min(PREVIEW_MAX_WIDTH, Math.max(PREVIEW_MIN_WIDTH, previewDragStartWidth.current + delta))
      setPreviewWidth(newWidth)
    }
    const handleMouseUp = () => {
      if (previewDragging.current) {
        previewDragging.current = false
        setIsPreviewDragging(false)
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

  // Load pages list + purpose/overview/source_pages
  const loadPages = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('wiki:listPages', { projectId })
      setPages(result.pages || [])
      setPendingCount(result.pending_compile_count || 0)
      setPurpose(result.wiki_purpose || '')
      setOverview(result.wiki_overview || '')
      setSourcePages(result.source_pages || [])
    } catch (err) {
      console.error('Failed to load wiki pages:', err)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  // Load compile status
  const loadCompileStatus = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('wiki:compileStatus', { projectId })
      setCompileTask(result.task || null)
      setIngestSummaries(result.ingest_summaries || [])
      setIsResumed(!!result.is_resumed)
      // Auto-show dialog if compile is active or was resumed
      const task = result.task
      if (task && ['queued', 'ingesting', 'storing', 'waiting_review'].includes(task.status)) {
        setShowCompileDialog(true)
      }
    } catch (err) {
      console.error('Failed to load compile status:', err)
    }
  }, [projectId])

  // Load reviews
  const loadReviews = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('wiki:listReviews', { projectId, resolved: false })
      setReviews(result.reviews || [])
    } catch (err) {
      console.error('Failed to load reviews:', err)
    }
  }, [projectId])

  // Initial load
  useEffect(() => {
    loadPages()
    loadCompileStatus()
    loadReviews()
  }, [loadPages, loadCompileStatus, loadReviews])

  // Notify parent when preview panel opens/closes
  useEffect(() => {
    onPreviewOpenChange?.(previewPageKey !== null)
  }, [previewPageKey, onPreviewOpenChange])

  // Close preview when parent signals (for mutual exclusion with chat panel)
  const prevCloseSignalRef = useRef(closePreviewSignal)
  useEffect(() => {
    if (closePreviewSignal !== prevCloseSignalRef.current) {
      prevCloseSignalRef.current = closePreviewSignal
      setPreviewPageKey(null)
      setIsEditing(false)
    }
  }, [closePreviewSignal])

  // Poll compile status when compiling
  useEffect(() => {
    if (!compileTask || !['queued', 'ingesting', 'storing', 'waiting_review'].includes(compileTask.status)) {
      return
    }
    let polling = false
    const interval = setInterval(async () => {
      if (polling) return
      polling = true
      try {
        const result: any = await window.electronAPI.invoke('wiki:compileStatus', { projectId })
        setCompileTask(result.task || null)
        setIngestSummaries(result.ingest_summaries || [])
        const task = result.task
        if (task && ['completed', 'failed', 'partial', 'stopped'].includes(task.status)) {
          setCompiling(false)
          await loadPages()
          await loadReviews()
          clearInterval(interval)
        } else if (task?.status === 'waiting_review') {
          // Supervised mode: refresh page list after each file so user sees new pages
          await loadPages()
          await loadReviews()
        }
      } catch (err) {
        console.error('Poll compile status failed:', err)
      } finally {
        polling = false
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [compileTask?.status, projectId, loadCompileStatus, loadPages, loadReviews])

  // Load page detail when selected for reading
  const loadPageDetail = useCallback(async (page: WikiPage) => {
    try {
      const result: any = await window.electronAPI.invoke('wiki:getPage', {
        projectId,
        pageKey: page.page_key,
      })
      setPageDetail(result)
    } catch (err) {
      console.error('Failed to load page detail:', err)
    }
  }, [projectId])

  // Navigate to a page for reading (slide-out preview)
  const handleSelectPage = useCallback((page: WikiPage) => {
    setSelectedPage(page)
    setPreviewPageKey(page.page_key)
    loadPageDetail(page)
  }, [loadPageDetail])

  // Navigate to page by key (from wiki-links, backlinks, etc.)
  const handleNavigateToPage = useCallback((pageKey: string) => {
    const page = pages.find((p) => p.page_key === pageKey)
    if (page) {
      handleSelectPage(page)
    } else {
      console.warn('Page not found:', pageKey)
    }
  }, [pages, handleSelectPage])

  // Go to home view
  const handleGoHome = useCallback(() => {
    setSelectedPage(null)
    setPageDetail(null)
    setPreviewPageKey(null)
    setViewMode('home')
  }, [])

  // Close preview panel
  const handleClosePreview = useCallback(() => {
    setPreviewPageKey(null)
    setIsEditing(false)
  }, [])

  // Go to edit mode (show editor in preview panel)
  const handleGoEdit = useCallback(() => {
    setIsEditing(true)
  }, [])

  // After save, reload and go back to preview
  const handlePageSaved = useCallback(async () => {
    await loadPages()
    if (selectedPage) {
      await loadPageDetail(selectedPage)
    }
    setIsEditing(false)
  }, [loadPages, selectedPage, loadPageDetail])

  // Go to changelog
  const handleGoChangelog = useCallback(() => {
    setViewMode('changelog')
  }, [])

  // Go to search
  const handleGoSearch = useCallback(() => {
    setViewMode('search')
  }, [])

  // Go to graph view (optionally highlight a specific node)
  const handleGoGraph = useCallback((highlightPageKey?: string) => {
    setGraphHighlightNode(highlightPageKey)
    setViewMode('graph')
  }, [])

  // Go to lint view
  const handleGoLint = useCallback(() => {
    setViewMode('lint')
  }, [])

  // Trigger compile (supervised mode by default)
  const handleCompile = async (force = false) => {
    // Force recompile deletes ALL wiki data — show custom confirmation dialog
    if (force) {
      setShowRecompileConfirm(true)
      return
    }
    setCompiling(true)
    setCompileTask(null) // clear stale task so UI shows transitional state
    setIsResumed(false)
    setShowCompileDialog(true)
    try {
      await window.electronAPI.invoke('wiki:compile', { projectId, mode: 'incremental', supervised: true, force })
      await loadCompileStatus()
    } catch (err) {
      console.error('Failed to trigger compile:', err)
      setCompiling(false)
      setShowCompileDialog(false)
      await loadCompileStatus() // reload to show previous task state
    }
  }

  // Execute force recompile after confirmation
  const doForceRecompile = async () => {
    setShowRecompileConfirm(false)
    setCompiling(true)
    setCompileTask(null)
    setIsResumed(false)
    setShowCompileDialog(true)
    // Clear stale page list immediately (backend just wiped all wiki data)
    setPages([])
    setReviews([])
    setSelectedPage(null)
    setPreviewPageKey(null)
    setViewMode('home')
    try {
      await window.electronAPI.invoke('wiki:compile', { projectId, mode: 'incremental', supervised: true, force: true })
      await loadCompileStatus()
      await loadPages()
    } catch (err) {
      console.error('Failed to trigger recompile:', err)
      setCompiling(false)
      setShowCompileDialog(false)
      await loadCompileStatus()
    }
  }

  // Continue supervised compile
  const handleContinueCompile = async () => {
    try {
      await window.electronAPI.invoke('wiki:continueCompile', { projectId })
      await loadCompileStatus()
    } catch (err) {
      console.error('Failed to continue compile:', err)
    }
  }

  // Stop supervised compile
  const handleStopCompile = async () => {
    try {
      await window.electronAPI.invoke('wiki:stopCompile', { projectId })
      await loadCompileStatus()
    } catch (err) {
      console.error('Failed to stop compile:', err)
    }
  }

  // Derived compile states
  // isCompiling: true only for active statuses, never for terminal states
  const isCompiling = compileTask
    ? ['queued', 'ingesting', 'storing', 'waiting_review'].includes(compileTask.status)
    : compiling
  const isSupervisedWaiting = compileTask?.status === 'waiting_review'

  // ── Render ──

  if (loading) {
    return (
      <div className={s.container}>
        <div className={s.loading}>
          <Loader2 size={24} className={s.spin} />
        </div>
      </div>
    )
  }

  return (
    <div className={`${s.container}${expanded ? ` ${s.expanded}` : ''}`}>
      {/* Top bar: compile status + pending notification */}
      <div className={s.topBar}>
        {/* Crash recovery notice */}
        {isResumed && compileTask && (
          <div className={s.recoveryNotice}>
            <AlertTriangle size={14} />
            <span>
              {t('project.wiki.compileResumed', {
                defaultValue: `上次编译被中断，已从第 ${ingestSummaries.length + 1} 个文件恢复继续`,
                fileNum: ingestSummaries.length + 1,
              })}
            </span>
          </div>
        )}

        {pendingCount > 0 && !isCompiling && !isResumed && (
          <div className={s.pendingNotice}>
            <AlertTriangle size={14} />
            <span>{t('project.wiki.pendingCompile', { count: pendingCount, defaultValue: `有 ${pendingCount} 个新文件待编译` })}</span>
            <button className={s.compileBtn} onClick={() => handleCompile()} disabled={compiling} title="AI 读取每份文件，自动提取实体、概念、主题，生成结构化 Wiki 页面">
              <RefreshCw size={13} />
              {t('project.wiki.compile', { defaultValue: '编译' })}
            </button>
          </div>
        )}

        {isCompiling && !isSupervisedWaiting && (
          <div className={s.compileStatus}>
            <Loader2 size={14} className={s.spin} />
            <span>
              {compileTask?.status === 'queued' && t('project.wiki.statusQueued', { defaultValue: '编译排队中...' })}
              {compileTask?.status === 'ingesting' && t('project.wiki.statusIngesting', { defaultValue: '摄入文件内容...' })}
              {compileTask?.status === 'storing' && t('project.wiki.statusStoring', { defaultValue: '保存知识库...' })}
            </span>
            {compileTask && compileTask.pages_total > 0 && (
              <span className={s.compileProgress}>
                {compileTask.pages_completed ?? 0}/{compileTask.pages_total} {t('project.wiki.pages', { defaultValue: '页面' })}
              </span>
            )}
          </div>
        )}

        {isSupervisedWaiting && compileTask && (
          <div className={s.compileStatus}>
            <AlertTriangle size={14} className={s.waitingIcon} />
            <span>{t('project.wiki.supervisedWaiting', { defaultValue: '等待审阅...' })}</span>
            <span className={s.compileProgress}>
              {compileTask.pages_completed}/{compileTask.pages_total} {t('project.wiki.supervisedFiles', { defaultValue: '文件' })}
            </span>
          </div>
        )}

        {compileTask && compileTask.status === 'completed' && (
          <div className={s.compileDone}>
            {ingestSummaries.some(s => s.error) ? (
              <AlertCircle size={14} style={{ color: 'var(--error, #ef4444)' }} />
            ) : (
              <CheckCircle size={14} />
            )}
            <span>
              {ingestSummaries.some(s => s.error)
                ? t('project.wiki.compileDoneWithErrors', {
                    defaultValue: `编译完成: ${ingestSummaries.filter(s => !s.error).length} 成功, ${ingestSummaries.filter(s => s.error).length} 失败`,
                    success: ingestSummaries.filter(s => !s.error).length,
                    errors: ingestSummaries.filter(s => s.error).length,
                  })
                : t('project.wiki.compileDone', { count: compileTask.pages_completed ?? 0, defaultValue: `编译完成 (${compileTask.pages_completed ?? 0} 个文件)` })
              }
            </span>
            {ingestSummaries.some(s => s.error) && (
              <button className={s.retryBtn} onClick={() => setShowCompileDialog(true)}>
                {t('project.wiki.viewDetails', { defaultValue: '查看详情' })}
              </button>
            )}
            <button className={s.retryBtn} onClick={() => handleCompile(true)} disabled={compiling} title="清除所有 Wiki 数据（页面、版本、审查项），从零开始重新处理">
              <RefreshCw size={13} />
              {t('project.wiki.recompile', { defaultValue: '重新编译' })}
            </button>
          </div>
        )}

        {compileTask && compileTask.status === 'partial' && (
          <div className={s.compileError}>
            <AlertCircle size={14} />
            <span>{t('project.wiki.compilePartial', { completed: compileTask.pages_completed ?? 0, total: compileTask.pages_total, failed: compileTask.pages_failed ?? 0, defaultValue: `编译部分完成: ${compileTask.pages_completed ?? 0}/${compileTask.pages_total} 页面, ${compileTask.pages_failed ?? 0} 失败` })}</span>
            <button className={s.retryBtn} onClick={() => handleCompile()}>{t('project.wiki.retry', { defaultValue: '重试' })}</button>
            <button className={s.retryBtn} onClick={() => handleCompile(true)} disabled={compiling} title="清除所有 Wiki 数据，从零开始重新处理">{t('project.wiki.recompile', { defaultValue: '重新编译' })}</button>
          </div>
        )}

        {compileTask && compileTask.status === 'failed' && (
          <div className={s.compileError}>
            <AlertCircle size={14} />
            <span>编译失败: {compileTask.error_message}</span>
            <button className={s.retryBtn} onClick={() => handleCompile()}>{t('project.wiki.retry', { defaultValue: '重试' })}</button>
            <button className={s.retryBtn} onClick={() => handleCompile(true)} disabled={compiling} title="清除所有 Wiki 数据，从零开始重新处理">{t('project.wiki.recompile', { defaultValue: '重新编译' })}</button>
          </div>
        )}

        {compileTask && compileTask.status === 'stopped' && (
          <div className={s.compileError}>
            <AlertCircle size={14} />
            <span>
              {compileTask.pages_total > 0
                ? t('project.wiki.compileStopped', {
                    completed: compileTask.pages_completed ?? 0,
                    total: compileTask.pages_total,
                    defaultValue: `编译已停止: ${compileTask.pages_completed ?? 0}/${compileTask.pages_total} 文件`,
                  })
                : t('project.wiki.compileStoppedSimple', { defaultValue: '编译已停止' })
              }
            </span>
            <button className={s.retryBtn} onClick={() => handleCompile()}>{t('project.wiki.continueCompile', { defaultValue: '继续编译' })}</button>
            <button className={s.retryBtn} onClick={() => handleCompile(true)} disabled={compiling} title="清除所有 Wiki 数据，从零开始重新处理">{t('project.wiki.recompile', { defaultValue: '重新编译' })}</button>
          </div>
        )}

        {/* Expand / collapse button for immersive reading */}
        {onExpandChange && (
          <button
            className={s.expandBtn}
            onClick={() => onExpandChange(!expanded)}
            title={expanded
              ? '退出沉浸模式，恢复侧栏导航'
              : '进入沉浸模式，隐藏侧栏，最大化内容区域'}
          >
            {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
        )}
      </div>

      <div className={s.mainArea}>
        {/* Left sidebar: navigation + page list */}
        <div className={s.sidebar}>
          {/* Navigation section */}
          <div className={s.sidebarNav}>
            <div
              className={`${s.navItem} ${viewMode === 'home' ? s.navItemActive : ''}`}
              onClick={handleGoHome}
              title="返回 Wiki 首页，查看内容索引"
            >
              <Home size={13} />
              <span>首页</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'changelog' ? s.navItemActive : ''}`}
              onClick={handleGoChangelog}
              title="查看每次编译的操作记录"
            >
              <Clock size={13} />
              <span>变更日志</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'search' ? s.navItemActive : ''}`}
              onClick={handleGoSearch}
              title="在 Wiki 页面中搜索关键词"
            >
              <Search size={13} />
              <span>搜索</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'graph' ? s.navItemActive : ''}`}
              onClick={() => handleGoGraph()}
              title="以知识图谱可视化页面间的关联"
            >
              <Share2 size={13} />
              <span>图谱</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'lint' ? s.navItemActive : ''}`}
              onClick={handleGoLint}
              title="检查 Wiki 页面质量（死链、孤立页、矛盾等）"
            >
              <Shield size={13} />
              <span>质量检查</span>
              {reviews.length > 0 && (
                <span className={s.navBadge}>{reviews.length}</span>
              )}
            </div>
          </div>

        </div>

        {/* Right content area */}
        <div className={s.content}>
          {viewMode === 'home' ? (
            /* Home page = index.md: purpose + overview + categorized page index */
            <WikiHomePage
              projectId={projectId}
              purpose={purpose}
              overview={overview}
              pages={pages}
              sourcePageCount={sourcePages.length}
              pendingCount={pendingCount}
              onNavigate={handleGoChangelog}
              onNavigateToPage={handleNavigateToPage}
              onCompile={() => handleCompile()}
            />
          ) : viewMode === 'search' ? (
            /* Search view */
            <WikiSearchView
              projectId={projectId}
              onNavigateToPage={handleNavigateToPage}
            />
          ) : viewMode === 'graph' ? (
            /* Graph visualization */
            <WikiGraphView
              projectId={projectId}
              onNavigateToPage={handleNavigateToPage}
              highlightNode={graphHighlightNode}
            />
          ) : viewMode === 'lint' ? (
            /* Lint view */
            <WikiLintView
              projectId={projectId}
              onNavigateToPage={handleNavigateToPage}
            />
          ) : viewMode === 'changelog' ? (
            /* Changelog timeline */
            <WikiChangeLog
              projectId={projectId}
              onNavigateToPage={handleNavigateToPage}
            />
          ) : (
            <div className={s.noSelection}>
              <BookOpen size={32} className={s.noSelectionIcon} />
              <p>{t('project.wiki.selectPage', { defaultValue: '选择一个页面查看内容' })}</p>
            </div>
          )}
        </div>
      </div>

      {/* Preview panel — slides in from right, full page height, resizable */}
      {previewPageKey && pageDetail && (
        <div className={s.previewPanel} style={{ width: previewWidth }}>
          <div
            className={`${s.previewDragHandle} ${isPreviewDragging ? s.previewDragHandleActive : ''}`}
            onMouseDown={handlePreviewDragMouseDown}
          />
          <div className={s.previewPanelHeader}>
            <button className={s.previewCloseBtn} onClick={handleClosePreview} title="关闭预览">
              ×
            </button>
          </div>
          {isEditing ? (
            <WikiPageEditor
              projectId={projectId}
              page={pageDetail.page}
              onBack={() => setIsEditing(false)}
              onSaved={handlePageSaved}
            />
          ) : (
            <WikiPageReader
              projectId={projectId}
              pageDetail={pageDetail}
              onEdit={handleGoEdit}
              onNavigateToPage={handleNavigateToPage}
            />
          )}
        </div>
      )}

      {/* Supervised compile dialog */}
      {showCompileDialog && compileTask && (
        <SupervisedCompileDialog
          task={compileTask}
          ingestSummaries={ingestSummaries}
          onContinue={handleContinueCompile}
          onStop={handleStopCompile}
          onClose={() => setShowCompileDialog(false)}
        />
      )}

      {/* Recompile confirmation dialog */}
      {showRecompileConfirm && (
        <div className={s.confirmOverlay} onClick={() => setShowRecompileConfirm(false)}>
          <div className={s.confirmDialog} onClick={(e) => e.stopPropagation()}>
            <div className={s.confirmHeader}>
              <AlertTriangle size={16} />
              <span>{t('project.wiki.recompile', { defaultValue: '重新编译' })}</span>
            </div>
            <div className={s.confirmBody}>
              {t('project.wiki.recompileConfirm', {
                defaultValue: '重新编译将清除所有 Wiki 页面、版本历史、审查项和变更日志，从零开始。此操作不可撤销，确定继续？',
              })}
            </div>
            <div className={s.confirmActions}>
              <button className={s.confirmCancelBtn} onClick={() => setShowRecompileConfirm(false)}>
                {t('common.cancel', { defaultValue: '取消' })}
              </button>
              <button className={s.confirmDangerBtn} onClick={doForceRecompile}>
                {t('project.wiki.recompile', { defaultValue: '重新编译' })}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
