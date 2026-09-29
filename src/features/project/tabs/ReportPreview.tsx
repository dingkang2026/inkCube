/** src/features/project/tabs/ReportPreview.tsx — Report detail view (rendered markdown + sources + logs) */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, FileText, Paperclip, ChevronDown, ChevronUp, Activity, AlertCircle, Info, Loader2 } from 'lucide-react'
import type { Report, ReportLog } from '../types'
import { useToast } from '../../knowledge/Toast'
import s from './ReportPreview.module.css'

interface ReportContent {
  content: string
  registry: {
    entries?: Array<{
      ref_number: number
      source_title: string
      doc_name?: string
      page?: number
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
  const [showSources, setShowSources] = useState(true)
  const [showLogs, setShowLogs] = useState(false)
  const [content, setContent] = useState<ReportContent | null>(null)
  const [logs, setLogs] = useState<ReportLog[]>([])
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)

  // Load report content from backend
  useEffect(() => {
    const loadContent = async () => {
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
    }
    loadContent()
  }, [report.id])

  // Load logs from backend
  useEffect(() => {
    const loadLogs = async () => {
      try {
        const result: any = await window.electronAPI.invoke('reports:getReportLogs', {
          reportId: report.id,
          limit: 50,
        })
        if (result.logs) {
          setLogs(result.logs)
        }
      } catch (err) {
        console.error('Failed to load report logs:', err)
      }
    }
    loadLogs()

    // Auto-refresh logs when report is generating
    if (report.status === 'generating') {
      const interval = setInterval(loadLogs, 2000)
      return () => clearInterval(interval)
    }
  }, [report.id, report.status])

  const handleExport = async (format: string) => {
    setExporting(true)
    try {
      const result: any = await window.electronAPI.invoke('reports:exportReport', {
        reportId: report.id,
        format,
      })
      if (result.path) {
        showToast(t('project.report.preview.exportSuccess', { format, title: report.title }), 'success')
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

  // Extract sources from registry (source_title for new cards, doc_name for legacy)
  const sources = content?.registry?.entries
    ? Array.from(
        content.registry.entries.reduce((map, entry) => {
          const name = entry.source_title || entry.doc_name || '未知来源'
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
          <button className={s.exportBtn} onClick={() => handleExport('Word')} disabled={exporting}>
            <FileText size={14} /> Word
          </button>
          <button className={s.exportBtn} onClick={() => handleExport('PDF')} disabled={exporting}>
            <FileText size={14} /> PDF
          </button>
        </div>
      </div>

      {/* Report content */}
      <div className={s.contentArea}>
        {report.status === 'generating' ? (
          <div className={s.generatingState}>
            <Loader2 size={24} className={s.spinIcon} />
            <span>{t('project.report.wizard.generating')} {report.progress ? `${report.progress}%` : ''}</span>
          </div>
        ) : report.status === 'error' ? (
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
