/** src/features/project/tabs/ReportPreview.tsx — Report detail view (rendered markdown + sources + logs) */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, FileText, Paperclip, ChevronDown, ChevronUp, Activity, AlertCircle, Info, Loader2, Pause, PauseCircle, Play, RefreshCw } from 'lucide-react'
import type { Report, ReportLog } from '../types'
import { useToast } from '../../knowledge/Toast'
import { useConfirm } from '../../../components/ConfirmDialog'
import { usePolling } from '../../../hooks/usePolling'
import { useEdgeRefetch } from '../../../hooks/useEdgeRefetch'
import { useActivity } from '../../activity/ActivityContext'
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

export function ReportPreview({ report, onBack }: ReportPreviewProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  const [showSources, setShowSources] = useState(true)
  const [showLogs, setShowLogs] = useState(false)
  const [content, setContent] = useState<ReportContent | null>(null)
  const [logs, setLogs] = useState<ReportLog[]>([])
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
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

  // ── 报告级暂停（2.5 §3.1 修订）：生成中可请求暂停（章节边界生效），paused 可恢复/重新生成
  const handlePause = async () => {
    try {
      const result: any = await window.electronAPI.invoke('reports:pauseReport', { reportId: report.id })
      if (result.error) {
        showToast(t('project.report.pauseFailed', { error: result.error }), 'error')
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
      if (result.error) showToast(t('project.report.retryFailed', { error: result.error }), 'error')
      else setStatusOverride('generating')  // 乐观翻转：不等快照同步，立即切生成中视图
    } catch (err) {
      showToast(t('project.report.retryFailed', { error: String(err) }), 'error')
    }
  }

  const handleRestart = async () => {
    if (!(await confirmDialog({ title: t('project.report.restartReport'), message: t('project.report.restartConfirm') }))) return
    try {
      const result: any = await window.electronAPI.invoke('reports:retryReport', { reportId: report.id, mode: 'restart' })
      if (result.error) showToast(t('project.report.retryFailed', { error: result.error }), 'error')
      else setStatusOverride('generating')  // 乐观翻转：不等快照同步，立即切生成中视图
    } catch (err) {
      showToast(t('project.report.retryFailed', { error: String(err) }), 'error')
    }
  }

  const handleExport = async () => {
    setExporting(true)
    try {
      const result: any = await window.electronAPI.invoke('reports:exportReport', {
        reportId: report.id,
      })
      if (result.path) {
        showToast(t('project.report.preview.exportSuccess', { format: 'Word', title: report.title }), 'success')
      } else {
        showToast(t('project.report.preview.exportFailed', { error: result.error }), 'error')
      }
    } catch (err) {
      console.error('Export failed:', err)
      showToast(t('project.report.preview.exportFailed', { error: String(err) }), 'error')
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

  const markdownContent = content?.content || t('project.report.preview.noContent')

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
          <button className={s.exportBtn} onClick={handleExport} disabled={exporting}>
            <FileText size={14} /> Word
          </button>
        </div>
      </div>

      {/* Report content */}
      <div className={s.contentArea}>
        {status === 'generating' ? (
          <div className={s.generatingState}>
            <Loader2 size={24} className={s.spinIcon} />
            <span>{t('project.report.wizard.generating')} {report.progress ? `${report.progress}%` : ''}</span>
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
            <span>{report.error_message || t('project.report.failed')}</span>
          </div>
        ) : (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            className={s.markdownBody}
          >
            {markdownContent}
          </ReactMarkdown>
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
