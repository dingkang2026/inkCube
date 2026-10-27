/** src/features/project/tabs/ReportPreview.tsx — Report detail view (rendered markdown + sources + logs) */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Markdown } from '../../../components/Markdown'
import { FindBar } from '../../../components/FindBar'
import { useMarkdownBlockFind } from '../../../hooks/useInFileFind'
import { ArrowLeft, FileText, Paperclip, ChevronDown, ChevronUp, Activity, AlertCircle, Info, Loader2, Pause, PauseCircle, Play, RefreshCw, Search } from 'lucide-react'
import type { Report, ReportLog } from '../types'
import { useToast } from '../../knowledge/Toast'
import { useConfirm } from '../../../components/ConfirmDialog'
import { usePolling } from '../../../hooks/usePolling'
import { useEdgeRefetch } from '../../../hooks/useEdgeRefetch'
import { useActivity } from '../../activity/ActivityContext'
import { apiErrorText, translateStoredError } from '../../../i18n/errorCodes'
import s from './ReportPreview.module.css'

interface ReportContent {
  content: string
  registry: {
    entries?: Array<{
      ref_number: number
      source_title: string
      content: string
    }>
  }
  report: {
    id: string
    title: string
    status: string
  }
}

interface ReportPreviewProps {
  report: Report
  onBack: () => void
}

// 导出面板方向记忆（§1.3：默认纵向，localStorage 记忆上次选择）
const ORIENTATION_STORAGE_KEY = 'inkcube.report.export.orientation'
type ExportOrientation = 'portrait' | 'landscape'

function _loadStoredOrientation(): ExportOrientation {
  try {
    return localStorage.getItem(ORIENTATION_STORAGE_KEY) === 'landscape' ? 'landscape' : 'portrait'
  } catch {
    return 'portrait'
  }
}

// 引用上标（视觉尾注，2026-10-27）：content.md 保持语义 [n] 单一事实源，
// 预览端转 <sup>（导出端 renderer.apply_endnote_superscript 转 pandoc 上标
// ^[n]^，同契约）。排除：行首 [n]（文末「参考来源」列表条目自身）、[n](
// 链接形态、[[n]] 嵌套；相邻引用 [40][41] 逐个转换。表格行不受影响
// （Markdown 组件表格行走纯 Markdown 管线，[n] 保持原样，降级可接受）。
const CITATION_SUP_RE = /(?<!^)(?<!\[)\[(\d{1,3})\](?!\()(?!\])/gm

function applyCitationSuperscript(content: string): string {
  return content.replace(CITATION_SUP_RE, '<sup>[$1]</sup>')
}

export function ReportPreview({ report, onBack }: ReportPreviewProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  // 面板默认态（§1.3）：正文优先，来源/日志默认收起；生成中日志默认展开供盯进展
  // （日志新上旧下渲染，最新进展首屏可见；自动滚底已随旧上新下排序一并撤销，2026-10-27）
  const [showSources, setShowSources] = useState(false)
  const [showLogs, setShowLogs] = useState(() => report.status === 'generating')
  const [content, setContent] = useState<ReportContent | null>(null)
  const [logs, setLogs] = useState<ReportLog[]>([])
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  // 导出面板（§1.3）：点「Word」弹出、点外部关闭；方向 radio 共用于两形式
  const [showExportPanel, setShowExportPanel] = useState(false)
  const [orientation, setOrientation] = useState<ExportOrientation>(_loadStoredOrientation)
  const exportPanelRef = useRef<HTMLDivElement | null>(null)
  // 乐观本地态（复审建议，2.5 §3.1 修订）：resume/restart 成功后后端已翻 generating，
  // 但快照要等心跳(≤5s)+轮询(3s)同步——窗口期内 paused 按钮仍可点、二次点击会被
  // 后端 400 拒绝弹出误导错误。本地立即置 generating：按钮即刻消失、spinner 即刻
  // 出现，快照同步后自然回落真值；切报告时重置，避免跨报告泄漏。
  const [statusOverride, setStatusOverride] = useState<Report['status'] | null>(null)
  useEffect(() => { setStatusOverride(null) }, [report.id])

  // Load report content from backend
  const loadContent = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('reports:getReportContent', report.id)
      if (result.content !== undefined) {
        setContent(result)
      }
    } catch (err) {
      console.error('Failed to load report content:', err)
    } finally {
      setLoading(false)
    }
  }, [report.id])

  useEffect(() => {
    void loadContent()
  }, [loadContent])

  // Load logs from backend（抛错由 usePolling 计入退避，0.4 §5.8）
  const loadLogs = useCallback(async () => {
    const result: any = await window.electronAPI.invoke('reports:getReportLogs', {
      reportId: report.id,
      limit: 50,
    })
    if (result.logs) {
      setLogs(result.logs)
    }
  }, [report.id])

  // #8 谓词收编全局心跳（顺序 4）：entity 级精确匹配（区别于 #7 的项目级）——
  // 本地 report 是打开预览时的快照，终态靠 ReportsTab applyReports 同步（v3.4），
  // 心跳翻假可早于快照同步，轮询与边沿都以心跳为准驱动
  const { items: activeReportItems } = useActivity('report')
  const generating = activeReportItems.some(a => a.entity_id === report.id)
  // 渲染态 = 本地乐观覆盖优先，快照同步后回落后端真值（轮询/边沿仍用心跳 generating）
  const status = statusOverride ?? report.status

  // 挂载 / 报告切换即拉日志（§6.3 启动间隙）：心跳 5s 粒度可能尚未发现新生成，
  // 轮询未开时也先有日志基线；与轮询首 tick 重复一次幂等读可接受
  useEffect(() => {
    void loadLogs()
  }, [loadLogs])

  // Auto-refresh logs when report is generating（#8，v1.7 迁移 usePolling 2s→5s，0.4 §8.1；
  // 谓词收编全局心跳，顺序 4）
  usePolling({
    name: 'report-preview-logs',
    enabled: generating,
    interval: 5000,
    task: loadLogs,
  })
  // 终态边沿补拉（0.4 §6.3）：心跳停轮可早于终态写入与快照同步——generating
  // 翻 false 的边沿重拉 content 渲染正文 + logs 刷终态日志（原 status effect 改为
  // 挂载即拉后，终态日志刷新统一由本边沿负责）
  useEdgeRefetch(generating, () => { void loadContent(); void loadLogs() })

  // 终态收敛（§1.3）：生成完正文优先，日志自动收起（仍可手动展开）。
  // 仅 generating 翻假时触发，不覆盖稳态下的手动展开
  useEffect(() => {
    if (!generating) setShowLogs(false)
  }, [generating])

  // 导出面板点外部关闭（面板自身与触发按钮在 ref 内，不触发关闭）
  useEffect(() => {
    if (!showExportPanel) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (exportPanelRef.current && !exportPanelRef.current.contains(e.target as Node)) {
        setShowExportPanel(false)
      }
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [showExportPanel])

  // ── 文档内查找（块级定位，公共基座 useMarkdownBlockFind + FindBar）──
  // 正文可见（非 generating/paused/error，与 Markdown 渲染分支同条件）才提供查找
  const rawContent = content?.content || t('project.report.preview.noContent')
  // 渲染用上标转换后的文本；查找仍用 rawContent（sup 只包标签不改可见文本，原文索引最稳）
  const markdownContent = useMemo(() => applyCitationSuperscript(rawContent), [rawContent])
  const canFind = status !== 'generating' && status !== 'paused' && status !== 'error'
  const contentAreaRef = useRef<HTMLDivElement | null>(null)
  const find = useMarkdownBlockFind(contentAreaRef, rawContent, canFind)

  // Ctrl/Cmd+F 接管：预览视图占满 Tab 内容区，报告即页面主内容（ReportsTab 列表视图与本视图互斥）
  useEffect(() => {
    if (!canFind) return
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        e.stopPropagation()
        find.openFind()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [canFind, find.openFind])

  // ── 报告级暂停（2.5 §3.1 修订）：生成中可请求暂停（章节边界生效），paused 可恢复/重新生成
  const handlePause = async () => {
    try {
      const result: any = await window.electronAPI.invoke('reports:pauseReport', { reportId: report.id })
      if (result.error) {
        showToast(t('project.report.pauseFailed', { error: apiErrorText(result.code, result.error, t) }), 'error')
      } else {
        showToast(t('project.report.pauseRequested'), 'info')
      }
    } catch (err) {
      showToast(t('project.report.pauseFailed', { error: String(err) }), 'error')
    }
  }

  // resume/restart 失败必须可见（对齐 handlePause 的错误处理）：后端 429/404/500
  // 若被静默吞掉，用户只会看到「点了没反应」而反复重试
  const handleResume = async () => {
    try {
      const result: any = await window.electronAPI.invoke('reports:retryReport', { reportId: report.id, mode: 'resume' })
      if (result.error) showToast(t('project.report.retryFailed', { error: apiErrorText(result.code, result.error, t) }), 'error')
      else setStatusOverride('generating')  // 乐观翻转：不等快照同步，立即切生成中视图
    } catch (err) {
      showToast(t('project.report.retryFailed', { error: String(err) }), 'error')
    }
  }

  const handleRestart = async () => {
    if (!(await confirmDialog({ title: t('project.report.restartReport'), message: t('project.report.restartConfirm') }))) return
    try {
      const result: any = await window.electronAPI.invoke('reports:retryReport', { reportId: report.id, mode: 'restart' })
      if (result.error) showToast(t('project.report.retryFailed', { error: apiErrorText(result.code, result.error, t) }), 'error')
      else setStatusOverride('generating')  // 乐观翻转：不等快照同步，立即切生成中视图
    } catch (err) {
      showToast(t('project.report.retryFailed', { error: String(err) }), 'error')
    }
  }

  // 导出（§1.3 两形式共用方向）：直接打开（另存为=false，渲染到产物目录默认位置后
  // 系统默认程序打开）/ 另存为（showSaveDialog 选定路径渲染，toast 展示完整路径）。
  // IPC 错误统一 throw（handlePythonResponse），在 catch 中可见呈现
  const handleExport = async (saveAs: boolean) => {
    setExporting(true)
    try {
      try { localStorage.setItem(ORIENTATION_STORAGE_KEY, orientation) } catch { /* ignore */ }
      const result: any = await window.electronAPI.invoke('reports:exportReport', {
        reportId: report.id,
        title: report.title,
        orientation,
        saveAs,
      })
      if (result.canceled) return // 用户取消另存为，静默收场
      if (result.openError) {
        showToast(t('project.report.preview.exportOpenFailed', { error: result.openError }), 'error')
      } else if (saveAs) {
        showToast(t('project.report.preview.exportedTo', { path: result.path }), 'success')
      } else {
        showToast(t('project.report.preview.exportOpened'), 'success')
      }
    } catch (err: any) {
      console.error('Export failed:', err)
      showToast(t('project.report.preview.exportFailed', { error: String(err?.message || err) }), 'error')
    } finally {
      setExporting(false)
    }
  }

  // Extract sources from registry (aggregate fragment count by source_title)
  const sources = content?.registry?.entries
    ? Array.from(
        content.registry.entries.reduce((map, entry) => {
          const name = entry.source_title || '未知来源'
          const existing = map.get(name)
          if (existing) {
            existing.count++
          } else {
            map.set(name, { name, count: 1 })
          }
          return map
        }, new Map<string, { name: string; count: number }>())
      ).map(([, v]) => v)
    : []

  if (loading) {
    return (
      <div className={s.container}>
        <div className={s.loading}>{t('common.loading')}</div>
      </div>
    )
  }

  return (
    <div className={s.container}>
      {/* Top bar */}
      <div className={s.topBar}>
        <button className={s.backBtn} onClick={onBack}>
          <ArrowLeft size={16} />
          {t('project.report.preview.backToList')}
        </button>
        <h3 className={s.reportTitle}>{report.title}</h3>
        <div className={s.toolbar}>
          {canFind && (
            <button className={s.exportBtn} onClick={find.openFind} title={t('project.report.preview.findInReport')}>
              <Search size={14} />
            </button>
          )}
          <div className={s.exportWrap} ref={exportPanelRef}>
            <button
              className={s.exportBtn}
              onClick={() => setShowExportPanel(v => !v)}
              // 非 done 状态禁用：终稿 content.md 要到收尾才落盘，生成中导出必报文件不存在
              disabled={exporting || status !== 'done'}
            >
              <FileText size={14} /> Word
            </button>
            {showExportPanel && (
              <div className={s.exportPanel}>
                <div className={s.exportPanelTitle}>
                  {t('project.report.preview.exportPanel.title')}
                </div>
                <div className={s.exportOrientation}>
                  <label className={s.radioLabel}>
                    <input
                      type="radio"
                      name="export-orientation"
                      checked={orientation === 'portrait'}
                      onChange={() => setOrientation('portrait')}
                    />
                    {t('project.report.preview.exportPanel.portrait')}
                  </label>
                  <label className={s.radioLabel}>
                    <input
                      type="radio"
                      name="export-orientation"
                      checked={orientation === 'landscape'}
                      onChange={() => setOrientation('landscape')}
                    />
                    {t('project.report.preview.exportPanel.landscape')}
                  </label>
                </div>
                <button
                  className={s.exportActionBtn}
                  onClick={() => void handleExport(false)}
                  disabled={exporting}
                >
                  {t('project.report.preview.exportPanel.openDirectly')}
                </button>
                <button
                  className={s.exportActionBtn}
                  onClick={() => void handleExport(true)}
                  disabled={exporting}
                >
                  {t('project.report.preview.exportPanel.saveAs')}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {find.findOpen && canFind && (
        <FindBar
          inputRef={find.inputRef}
          query={find.findQuery}
          count={find.matches.length}
          current={find.safeIdx}
          onQueryChange={find.setFindQuery}
          onPrev={() => find.gotoMatch(find.safeIdx - 1)}
          onNext={() => find.gotoMatch(find.safeIdx + 1)}
          onClose={find.closeFind}
        />
      )}

      {/* Report content */}
      <div className={s.contentArea} ref={contentAreaRef}>
        {status === 'generating' ? (
          <div className={s.generatingState}>
            <Loader2 size={24} className={s.spinIcon} />
            <span>{t('project.report.wizard.generating')} {report.progress ? `${report.progress}%` : ''}</span>
            <div
              className={s.progressBar}
              role="progressbar"
              aria-valuenow={report.progress || 0}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className={s.progressFill} style={{ width: `${report.progress || 0}%` }} />
            </div>
            <button className={s.pauseActionBtn} onClick={() => void handlePause()} title={t('project.report.pauseTip')}>
              <Pause size={14} /> {t('project.report.pauseReport')}
            </button>
          </div>
        ) : status === 'paused' ? (
          <div className={s.pausedState}>
            <PauseCircle size={24} />
            <span>{t('project.report.pausedStatus')} {report.progress ? `${report.progress}%` : ''}</span>
            <div className={s.pausedActions}>
              <button className={s.pauseActionBtn} onClick={() => void handleResume()} title={t('project.report.resumeTip')}>
                <Play size={14} /> {t('project.report.resumeReport')}
              </button>
              <button className={s.pauseActionBtn} onClick={() => void handleRestart()}>
                <RefreshCw size={14} /> {t('project.report.restartReport')}
              </button>
            </div>
          </div>
        ) : status === 'error' ? (
          <div className={s.errorState}>
            <AlertCircle size={24} />
            <span>{report.error_message ? translateStoredError(report.error_message, t) : t('project.report.failed')}</span>
            {/* 崩溃恢复入口（§3.8.3）：error 保留 checkpoint，与 paused 同构提供
                恢复（断点续跑）/ 重新生成（全量重跑，确认后）——兑现中断日志
                「可重新生成续跑」的承诺，免去退回列表找重试按钮 */}
            <div className={s.pausedActions}>
              <button className={s.pauseActionBtn} onClick={() => void handleResume()} title={t('project.report.resumeTip')}>
                <Play size={14} /> {t('project.report.resumeReport')}
              </button>
              <button className={s.pauseActionBtn} onClick={() => void handleRestart()}>
                <RefreshCw size={14} /> {t('project.report.restartReport')}
              </button>
            </div>
          </div>
        ) : (
          <Markdown content={markdownContent} className={s.markdownBody} />
        )}
      </div>

      {/* Sources panel */}
      {sources.length > 0 && (
        <div className={s.sourcesPanel}>
          <button className={s.sourcesToggle} onClick={() => setShowSources(!showSources)}>
            <Paperclip size={14} />
            <span>{t('project.report.preview.sources', { count: sources.length, total: sources.reduce((a, s) => a + s.count, 0) })}</span>
            {showSources ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
          {showSources && (
            <div className={s.sourcesList}>
              {sources.map((src) => (
                <div key={src.name} className={s.sourceItem}>
                  <span className={s.sourceName}>{src.name}</span>
                  <span className={s.sourceCount}>{src.count} {t('project.report.preview.fragments')}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Progress logs panel */}
      <div className={s.logsPanel}>
        <button className={s.sourcesToggle} onClick={() => setShowLogs(!showLogs)}>
          <Activity size={14} />
          <span>{t('project.report.preview.progressLogs')} ({logs.length})</span>
          {showLogs ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
        {showLogs && (
          <div className={s.logsList}>
            {logs.length === 0 ? (
              <div className={s.logEmpty}>{t('project.report.preview.noLogs')}</div>
            ) : (
              logs.map((log) => (
                <div key={log.id} className={`${s.logItem} ${s[`log_${log.level}`] || ''}`}>
                  <span className={s.logIcon}>
                    {log.level === 'error' ? <AlertCircle size={12} /> :
                     log.level === 'warning' ? <AlertCircle size={12} /> :
                     <Info size={12} />}
                  </span>
                  <span className={s.logMessage}>{log.message}</span>
                  <span className={s.logTime}>{new Date(log.created_at).toLocaleTimeString()}</span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}
