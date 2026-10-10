/** src/features/project/tabs/WikiTab.tsx — Wiki tab: container with sidebar + view routing */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  BookOpen, RefreshCw, AlertCircle, CheckCircle, Clock,
  AlertTriangle, Loader2, Home, Shield, Search, Share2,
  Maximize2, Minimize2, Square, ListOrdered,
} from 'lucide-react'
import type { ProjectType } from '../types'
import { WikiHomePage } from './WikiHomePage'
import { WikiPageReader } from './WikiPageReader'
import { WikiPageEditor } from './WikiPageEditor'
import { WikiChangeLog } from './WikiChangeLog'
import { WikiSearchView } from './WikiSearchView'
import { WikiGraphView } from './WikiGraphView'
import { WikiLintView } from './WikiLintView'
import { CompilePanel } from './CompilePanel'
import { WikiSchemaManager } from './WikiSchemaManager'
import { useFeatureGuard } from '../../readiness/useReadiness'
import { useToast } from '../../knowledge/Toast'
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
  /** 编译状态（由父组件 #4 轮询下发，v1.7 后本组件不再独立轮询，0.4 §8.1） */
  compileData?: WikiCompileData | null
  /** 编译明细面板展开/收起上报（父组件据此将 #4 轮询降至 2s） */
  onCompileDetailChange?: (open: boolean) => void
  /** 脉搏带/健康卡直达信号(一期 lint 直达;二期扩 compile 直达编译,裁决 6) */
  focusSignal?: { view: 'lint' | 'compile'; ts: number } | null
  /** 直达信号消费完成回调（consume-once：父级置空信号，防重挂载重复触发/跨项目泄漏） */
  onFocusHandled?: () => void
  /** 治理动作后通知父级刷新脉搏带计数（0.4 §3.1 操作后刷新，3.0 一期最终 review） */
  onReviewsChanged?: () => void
}

export interface CompileTask {
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

/** 父组件下发的编译状态快照（wiki:compileStatus 返回结构） */
export interface WikiCompileData {
  task: CompileTask | null
  ingest_summaries: any[]
  is_resumed: boolean
}

interface WikiPage {
  id: string
  project_id: string
  page_key: string
  title: string
  content: string
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

interface Review {
  id: string
  type: string
  title: string
  description: string
  resolved: number
  created_at: string
}
type ViewMode = 'home' | 'read' | 'edit' | 'changelog' | 'search' | 'graph' | 'lint' | 'schema'

const PREVIEW_DEFAULT_WIDTH = 720
const PREVIEW_MIN_WIDTH = 260
const PREVIEW_MAX_WIDTH = 720

export function WikiTab({ projectId, projectType: _projectType, expanded, onExpandChange, onPreviewOpenChange, closePreviewSignal, compileData, onCompileDetailChange, focusSignal, onFocusHandled, onReviewsChanged }: WikiTabProps) {
  const { t } = useTranslation()
  const guard = useFeatureGuard()
  const showToast = useToast()
  const [pages, setPages] = useState<WikiPage[]>([])
  const [pendingCount, setPendingCount] = useState(0)
  const [purpose, setPurpose] = useState('')
  const [overview, setOverview] = useState('')
  const [sourcePages, setSourcePages] = useState<any[]>([])
  const [selectedPage, setSelectedPage] = useState<WikiPage | null>(null)
  const [pageDetail, setPageDetail] = useState<any>(null)
  const [compileTask, setCompileTask] = useState<CompileTask | null>(null)
  const [reviews, setReviews] = useState<Review[]>([])
  const [schemas, setSchemas] = useState<Array<{ page_key: string; title: string }>>([])
  const [loading, setLoading] = useState(true)
  const [compiling, setCompiling] = useState(false)
  const [viewMode, setViewMode] = useState<ViewMode>('home')
  const [ingestSummaries, setIngestSummaries] = useState<any[]>([])
  const [isResumed, setIsResumed] = useState(false)
  const [showDetailPanel, setShowDetailPanel] = useState(false)
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
      // 拖宽中途卸载(如 Tab 切走)也复位 body 样式,防游标/选区卡死(与 TaskDrawer 同款防护)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [])

  // 快切项目防乱序：同类型项目切换时 WikiTab 实例存活（父层 key=projectType 不变），
  // 在途响应晚到会跨项目覆盖 state 且无自愈——捕获 pid，await 后比对不一致即丢弃
  // （与父层 loadPulseData/checkWikiCompileStatus 的 latestPidRef 同款裁决，3.0 一期 T3+4）
  const latestPidRef = useRef(projectId)
  useEffect(() => { latestPidRef.current = projectId }, [projectId])

  // Load pages list + purpose/overview/source_pages
  const loadPages = useCallback(async () => {
    const pid = projectId
    try {
      const result: any = await window.electronAPI.invoke('wiki:listPages', { projectId: pid })
      if (latestPidRef.current !== pid) return
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

  // Load reviews
  const loadReviews = useCallback(async () => {
    const pid = projectId
    try {
      const result: any = await window.electronAPI.invoke('wiki:listReviews', { projectId: pid, resolved: false })
      if (latestPidRef.current !== pid) return
      setReviews(result.reviews || [])
    } catch (err) {
      console.error('Failed to load reviews:', err)
    }
  }, [projectId])

  // Load schemas（结构页定义，知识首页完成度数据源，3.0 一期 Task 6）
  const loadSchemas = useCallback(async () => {
    const pid = projectId
    try {
      const result: any = await window.electronAPI.invoke('wiki:getSchemas', { projectId: pid })
      if (latestPidRef.current !== pid) return
      setSchemas(result.schemas || [])
    } catch (err) {
      console.error('Failed to load schemas:', err)
    }
  }, [projectId])

  // Initial load（编译状态不再自行拉取，改由父组件 #4 轮询下发，v1.7 迁移自原 #3 独立轮询，0.4 §8.1）
  useEffect(() => {
    loadPages()
    loadReviews()
    loadSchemas()
  }, [loadPages, loadReviews, loadSchemas])

  // 直达信号（consume-once）：脉搏带矛盾/缺口 chip 或健康卡「去处理」→ 治理视图（3.0 §6.1.1；
  // ts 保证重复点击同型 chip 也能再次触发，语义同 handleGoLint；应用后即回调置空，
  // 防止 Tab 往返重挂载被残留信号反复拉回 lint，以及项目 A 信号泄漏到项目 B）
  useEffect(() => {
    if (!focusSignal?.view) return
    if (focusSignal.view === 'compile') {
      // 与点「编译」按钮等价(裁决 6);但编译进行中再触发会 409 并闪断状态条——
      // 与原顶栏按钮 disabled={compiling} 同语义,仅停留不重触发(Task 13 review M2)。
      // 用父级 props 快照判断(而非本地 compileTask state):consume effect 先于
      // compileData 同步 effect 执行,重挂载首轮本地态尚为空,props 已就绪
      const active = compiling
        || (compileData?.task != null && ['queued', 'ingesting', 'storing'].includes(compileData.task.status))
      if (!active) handleCompile()
    } else {
      setViewMode(focusSignal.view)
    }
    onFocusHandled?.()
  }, [focusSignal])

  // 编译状态同步：父组件 #4 轮询下发 compileData → 本地 state。
  // 不随活跃任务自动展开明细面板（2.6 §10 修订）：面板由用户动作
  // （触发编译 / 查看详情）控制，编译进行中经状态条与全局反馈面感知即可。
  useEffect(() => {
    if (!compileData) return
    setCompileTask(compileData.task)
    setIngestSummaries(compileData.ingest_summaries || [])
    setIsResumed(compileData.is_resumed)
  }, [compileData])

  // 编译状态变化副作用：终态收尾（刷 pages/reviews）
  const prevCompileStatusRef = useRef<string | null>(null)
  useEffect(() => {
    const status = compileTask?.status ?? null
    if (status === prevCompileStatusRef.current) return
    prevCompileStatusRef.current = status
    if (status && ['completed', 'failed', 'stopped'].includes(status)) {
      setCompiling(false)
      void loadPages()
      void loadReviews()
      void loadSchemas()
    }
  }, [compileTask?.status, loadPages, loadReviews, loadSchemas])

  // 明细面板展开/收起上报父组件（#4 轮询据此降至 2s，0.4 §8.1）；
  // unmount 时上报 false，避免父组件在 Tab 切走后仍以 2s 高频轮询
  useEffect(() => {
    onCompileDetailChange?.(showDetailPanel)
    return () => onCompileDetailChange?.(false)
  }, [showDetailPanel, onCompileDetailChange])

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

  // 治理动作后：重拉发现项计数 + 上报父级刷新脉搏带（0.4 §3.1「操作后刷新」，3.0 一期最终 review）
  const handleFindingsChanged = useCallback(() => {
    void loadReviews()
    onReviewsChanged?.()
  }, [loadReviews, onReviewsChanged])

  // Trigger compile (V2 per-file ingest, continuous run); entry guard per 0.4 §7.3
  const handleCompile = (force = false) => {
    // Force recompile deletes ALL wiki data — show custom confirmation dialog
    if (force) {
      setShowRecompileConfirm(true)
      return
    }
    void guard('wiki_compile', () => {
      void doCompile()
    })
  }

  const doCompile = async () => {
    if (compiling) return   // 纵深防御:乐观窗口内任何路径重入直接忽略(M2 同源,防未来新入口绕过)
    setCompiling(true)
    setCompileTask(null) // clear stale task so UI shows transitional state
    setIsResumed(false)
    setShowDetailPanel(true)
    // 状态刷新依赖父组件 #4 轮询（明细面板展开时 2s，0.4 §8.1）
    try {
      await window.electronAPI.invoke('wiki:compile', { projectId, mode: 'incremental', force: false })
    } catch (err) {
      console.error('Failed to trigger compile:', err)
      setCompiling(false)
      setShowDetailPanel(false)
      // 409 防重入 / 就绪度结构化报错必须可感知，不能只写 console
      showToast(t('project.wiki.compileTriggerFailed', {
        reason: err instanceof Error ? err.message : String(err),
        defaultValue: `编译触发失败: ${err instanceof Error ? err.message : String(err)}`,
      }), 'error')
    }
  }

  // Execute force recompile after confirmation
  const doForceRecompile = async () => {
    setShowRecompileConfirm(false)
    setCompiling(true)
    setCompileTask(null)
    setIsResumed(false)
    setShowDetailPanel(true)
    // 状态刷新依赖父组件 #4 轮询（明细面板展开时 2s，0.4 §8.1）
    try {
      await window.electronAPI.invoke('wiki:compile', { projectId, mode: 'incremental', force: true })
      // 触发成功后才清空本地列表（后端此时才 wipe）；失败时保留原数据仅提示
      setPages([])
      setReviews([])
      setSelectedPage(null)
      setPreviewPageKey(null)
      setViewMode('home')
      await loadPages()
    } catch (err) {
      console.error('Failed to trigger recompile:', err)
      setCompiling(false)
      setShowDetailPanel(false)
      showToast(t('project.wiki.compileTriggerFailed', {
        reason: err instanceof Error ? err.message : String(err),
        defaultValue: `编译触发失败: ${err instanceof Error ? err.message : String(err)}`,
      }), 'error')
    }
  }

  // Stop compile (cooperative: effective after the current file)
  const handleStopCompile = async () => {
    try {
      // 状态刷新依赖父组件 #4 轮询（0.4 §8.1）
      await window.electronAPI.invoke('wiki:stopCompile', { projectId })
    } catch (err) {
      console.error('Failed to stop compile:', err)
      showToast(t('project.wiki.stopFailed', {
        reason: err instanceof Error ? err.message : String(err),
        defaultValue: `停止编译失败: ${err instanceof Error ? err.message : String(err)}`,
      }), 'error')
    }
  }

  // Derived compile states
  // isCompiling: true only for active statuses, never for terminal states
  const isCompiling = compileTask
    ? ['queued', 'ingesting', 'storing'].includes(compileTask.status)
    : compiling

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

        {/* 顶栏待编译通知已删(裁决 6):触发编译入口统一为脉搏带待编译 chip,
            经 focusSignal compile 直达;pendingCount 保留(首页 pendingCta/startCompile 消费) */}
        {isCompiling && (
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
            {/* 查看详情：明细面板收起后经此重新展开（2.6 §10 修订） */}
            <button className={s.retryBtn} onClick={() => setShowDetailPanel(true)}>
              {t('project.wiki.viewDetails', { defaultValue: '查看详情' })}
            </button>
            {/* 停止（2.6 §4.3）：任务级协作式停止，当前文件完成后生效 → stopped 终态。
                wiki 无队列暂停（0.4 §6.7 修订）：停止 + 断点续传已覆盖暂停价值 */}
            <button
              className={s.stopBtn}
              onClick={() => void handleStopCompile()}
              title={t('project.wiki.compileStop')}
            >
              <Square size={13} />
            </button>
          </div>
        )}

        {compileTask && compileTask.status === 'completed' && (
          <div className={s.compileDone}>
            {ingestSummaries.some(s => s.error) ? (
              <AlertCircle size={14} style={{ color: 'var(--error, #ef4444)' }} />
            ) : (
              <CheckCircle size={14} />
            )}
            {ingestSummaries.some(s => s.error) ? (
              <span>
                {t('project.wiki.compileDoneOk', {
                  success: ingestSummaries.filter(s => !s.error).length,
                  defaultValue: `编译完成: ${ingestSummaries.filter(s => !s.error).length} 成功`,
                })}
                {/* 失败计数即快速定位入口：点击直达逐文件明细（2.6 §13.3） */}
                <button
                  className={s.failCountBtn}
                  onClick={() => setShowDetailPanel(true)}
                  title={t('project.wiki.viewDetails', { defaultValue: '查看详情' })}
                >
                  {t('project.wiki.compileDoneFailCount', {
                    errors: ingestSummaries.filter(s => s.error).length,
                    defaultValue: `${ingestSummaries.filter(s => s.error).length} 个文件失败`,
                  })}
                </button>
              </span>
            ) : (
              <span>
                {t('project.wiki.compileDone', { count: compileTask.pages_completed ?? 0, defaultValue: `编译完成 (${compileTask.pages_completed ?? 0} 个文件)` })}
              </span>
            )}
            {/* 重试失败（2.6 §13.3）：非 force 增量——失败文件无 source_pages 记录天然重跑，
                已成功文件 skip-if-unchanged 跳过；与 failed/stopped 状态条的重试同一语义 */}
            {ingestSummaries.some(s => s.error) && (
              <button
                className={s.retryBtn}
                onClick={() => handleCompile()}
                disabled={compiling}
                title={t('project.wiki.retryFailedTip')}
              >
                <RefreshCw size={13} />
                {t('project.wiki.retryFailed', { count: ingestSummaries.filter(s => s.error).length, defaultValue: `重试失败 (${ingestSummaries.filter(s => s.error).length})` })}
              </button>
            )}
            {/* 查看详情：终态均保留逐文件摘要回看入口，不再仅限有错误时（2.6 §10 修订） */}
            <button className={s.retryBtn} onClick={() => setShowDetailPanel(true)}>
              {t('project.wiki.viewDetails', { defaultValue: '查看详情' })}
            </button>
            <button className={s.retryBtn} onClick={() => handleCompile(true)} disabled={compiling} title={t('project.wiki.resetTip')}>
              <RefreshCw size={13} />
              {t('project.wiki.recompile', { defaultValue: '重新编译' })}
            </button>
          </div>
        )}

        {compileTask && compileTask.status === 'failed' && (
          <div className={s.compileError}>
            <AlertCircle size={14} />
            <span>{t('project.wiki.compileFailed', { error: compileTask.error_message })}</span>
            <button className={s.retryBtn} onClick={() => setShowDetailPanel(true)}>
              {t('project.wiki.viewDetails', { defaultValue: '查看详情' })}
            </button>
            <button className={s.retryBtn} onClick={() => handleCompile()}>{t('project.wiki.retry', { defaultValue: '重试' })}</button>
            <button className={s.retryBtn} onClick={() => handleCompile(true)} disabled={compiling} title={t('project.wiki.resetTipShort')}>{t('project.wiki.recompile', { defaultValue: '重新编译' })}</button>
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
            <button className={s.retryBtn} onClick={() => setShowDetailPanel(true)}>
              {t('project.wiki.viewDetails', { defaultValue: '查看详情' })}
            </button>
            <button className={s.retryBtn} onClick={() => handleCompile()}>{t('project.wiki.resumeCompile', { defaultValue: '继续编译' })}</button>
            <button className={s.retryBtn} onClick={() => handleCompile(true)} disabled={compiling} title={t('project.wiki.resetTipShort')}>{t('project.wiki.recompile', { defaultValue: '重新编译' })}</button>
          </div>
        )}

        {/* Expand / collapse button for immersive reading */}
        {onExpandChange && (
          <button
            className={s.expandBtn}
            onClick={() => onExpandChange(!expanded)}
            title={expanded
              ? t('project.wiki.exitImmersive')
              : t('project.wiki.enterImmersive')}
          >
            {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
        )}
      </div>

      {/* Compile detail panel（内嵌可折叠，2.6 §10）：逐文件摘要审阅，不遮内容 */}
      {showDetailPanel && compileTask && (
        <CompilePanel
          task={compileTask}
          ingestSummaries={ingestSummaries}
          onCollapse={() => setShowDetailPanel(false)}
        />
      )}

      <div className={s.mainArea}>
        {/* Left sidebar: navigation + page list */}
        <div className={s.sidebar}>
          {/* Navigation section */}
          <div className={s.sidebarNav}>
            <div
              className={`${s.navItem} ${viewMode === 'home' ? s.navItemActive : ''}`}
              onClick={handleGoHome}
              title={t('project.wiki.navTipHome')}
            >
              <Home size={13} />
              <span>{t('project.wiki.home')}</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'changelog' ? s.navItemActive : ''}`}
              onClick={handleGoChangelog}
              title={t('project.wiki.navTipChangeLog')}
            >
              <Clock size={13} />
              <span>{t('project.wiki.changeLog')}</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'search' ? s.navItemActive : ''}`}
              onClick={handleGoSearch}
              title={t('project.wiki.navTipSearch')}
            >
              <Search size={13} />
              <span>{t('project.wiki.search')}</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'graph' ? s.navItemActive : ''}`}
              onClick={() => handleGoGraph()}
              title={t('project.wiki.navTipGraph')}
            >
              <Share2 size={13} />
              <span>{t('project.wiki.graph')}</span>
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'lint' ? s.navItemActive : ''}`}
              onClick={handleGoLint}
              title={t('project.wiki.navTipLint')}
            >
              <Shield size={13} />
              <span>{t('project.wiki.lint')}</span>
              {reviews.length > 0 && (
                <span className={s.navBadge}>{reviews.length}</span>
              )}
            </div>
            <div
              className={`${s.navItem} ${viewMode === 'schema' ? s.navItemActive : ''}`}
              onClick={() => setViewMode('schema')}
              title={t('project.wiki.navTipSchema')}
            >
              <ListOrdered size={13} />
              <span>{t('project.wiki.schemaNav')}</span>
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
              onMetaSaved={() => void loadPages()}
              reviewCounts={{
                contradiction: reviews.filter((r) => r.type === 'contradiction').length,
                gap: reviews.filter((r) => r.type === 'data-gap' || r.type === 'missing-page').length,
                deadLink: reviews.filter((r) => r.type === 'broken-link').length,
              }}
              onOpenLint={() => setViewMode('lint')}
              schemas={schemas}
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
              onFindingsChanged={handleFindingsChanged}
            />
          ) : viewMode === 'schema' ? (
            /* Schema management (structure pages, 2.6 §3.1) */
            <WikiSchemaManager projectId={projectId} />
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

      {/* Preview backdrop — 知识问答面板同款：展开时点击面板外任意处收起（遮罩不透传点击） */}
      {previewPageKey && pageDetail && (
        <div className={s.previewBackdrop} onClick={handleClosePreview} />
      )}

      {/* Preview panel — slides in from right, full page height, resizable */}
      {previewPageKey && pageDetail && (
        <div className={s.previewPanel} style={{ width: previewWidth }}>
          <div
            className={`${s.previewDragHandle} ${isPreviewDragging ? s.previewDragHandleActive : ''}`}
            onMouseDown={handlePreviewDragMouseDown}
          />
          <div className={s.previewPanelHeader}>
            <button className={s.previewCloseBtn} onClick={handleClosePreview} title={t('common.close')}>
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
              sourcePages={sourcePages}
              onEdit={handleGoEdit}
              onNavigateToPage={handleNavigateToPage}
            />
          )}
        </div>
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
