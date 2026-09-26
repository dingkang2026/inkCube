/** src/features/project/tabs/ReportsTab.tsx — Reports tab with kanban layout (4 columns by stage) */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText, Plus, Settings2, Clock, CheckCircle, AlertCircle, Trash2, RefreshCw } from 'lucide-react'
import type { ProjectType, ProjectStage, Report } from '../types'
import { STAGE_COLUMNS } from '../types'
import { ReportWizard } from './ReportWizard'
import { ReportPreview } from './ReportPreview'
import { TemplateManagement } from './TemplateManagement'
import s from './ReportsTab.module.css'

type ViewMode = 'list' | 'wizard' | 'preview' | 'templateManagement'

interface ReportsTabProps {
  projectId: string
  projectType: ProjectType
  currentStage: ProjectStage
}

export function ReportsTab({ projectId, projectType, currentStage }: ReportsTabProps) {
  const { t } = useTranslation()
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [reports, setReports] = useState<Report[]>([])
  const [previewReport, setPreviewReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)

  // Load reports from backend
  useEffect(() => {
    const loadReports = async () => {
      try {
        const result: any = await window.electronAPI.invoke('reports:listReports', { projectId })
        if (result.reports) {
          setReports(result.reports)
        }
      } catch (err) {
        console.error('Failed to load reports:', err)
      } finally {
        setLoading(false)
      }
    }
    loadReports()
  }, [projectId])

  const handleWizardComplete = async () => {
    // Reload reports after wizard completes
    try {
      const result: any = await window.electronAPI.invoke('reports:listReports', { projectId })
      if (result.reports) {
        setReports(result.reports)
      }
    } catch (err) {
      console.error('Failed to reload reports:', err)
    }
    setViewMode('list')
  }

  const handleOpenReport = (report: Report) => {
    setPreviewReport(report)
    setViewMode('preview')
  }

  const handleDeleteReport = async (report: Report) => {
    if (!confirm(t('project.report.confirmDelete'))) return
    try {
      await window.electronAPI.invoke('reports:deleteReport', report.id)
      // Reload reports
      const result: any = await window.electronAPI.invoke('reports:listReports', { projectId })
      if (result.reports) setReports(result.reports)
    } catch (err) {
      console.error('Failed to delete report:', err)
    }
  }

  const handleExportReport = async (report: Report) => {
    try {
      const result: any = await window.electronAPI.invoke('reports:exportReport', {
        reportId: report.id,
        format: report.output_format,
      })
      if (result.path) {
        alert(t('project.report.exportSuccess', { format: report.output_format, title: report.title }))
      } else {
        alert(t('project.report.exportFailed', { error: result.error }))
      }
    } catch (err) {
      console.error('Export failed:', err)
      alert(t('project.report.exportFailed', { error: String(err) }))
    }
  }

  const handleRetryReport = async (report: Report, mode: 'resume' | 'restart' = 'resume') => {
    try {
      const result: any = await window.electronAPI.invoke('reports:retryReport', {
        reportId: report.id,
        mode,
      })
      if (result.error) {
        alert(t('project.report.retryFailed', { error: result.error }))
      } else {
        // Reload reports to show updated status
        const res: any = await window.electronAPI.invoke('reports:listReports', { projectId })
        if (res.reports) setReports(res.reports)
      }
    } catch (err) {
      console.error('Retry failed:', err)
      alert(t('project.report.retryFailed', { error: String(err) }))
    }
  }

  // ── Wizard view ──
  if (viewMode === 'wizard') {
    return (
      <ReportWizard
        projectId={projectId}
        projectType={projectType}
        currentStage={currentStage}
        onComplete={handleWizardComplete}
        onCancel={() => setViewMode('list')}
      />
    )
  }

  // ── Preview view ──
  if (viewMode === 'preview' && previewReport) {
    return (
      <ReportPreview
        report={previewReport}
        onBack={() => { setViewMode('list'); setPreviewReport(null) }}
      />
    )
  }

  // ── Template management view ──
  if (viewMode === 'templateManagement') {
    return (
      <TemplateManagement onBack={() => setViewMode('list')} />
    )
  }

  // ── Kanban view (default) ──
  // Determine columns to show based on project type
  const columns = projectType === 'general'
    ? STAGE_COLUMNS
    : STAGE_COLUMNS.filter(c => c.key !== 'none' || reports.some(r => r.stage === 'none'))

  const visibleColumns = projectType === 'general'
    ? [{ key: 'none' as ProjectStage, labelKey: 'project.stage.other' }]
    : columns

  if (loading) {
    return (
      <div className={s.container}>
        <div className={s.loading}>{t('common.loading')}</div>
      </div>
    )
  }

  return (
    <div className={s.container}>
      {/* Header */}
      <div className={s.header}>
        <button className={s.primaryBtn} onClick={() => setViewMode('wizard')}>
          <Plus size={16} />
          {t('project.report.newReport')}
        </button>
        <button className={s.iconBtn} onClick={() => setViewMode('templateManagement')} title={t('project.report.templateManagement')}>
          <Settings2 size={16} />
        </button>
      </div>

      {/* Kanban board */}
      <div className={s.kanbanBoard}>
        {visibleColumns.map((col) => {
          const colReports = reports.filter(r => r.stage === col.key)
          return (
            <div key={col.key} className={s.kanbanColumn}>
              <div className={s.columnHeader}>
                <span className={s.columnTitle}>{t(col.labelKey)}</span>
                <span className={s.columnCount}>{colReports.length}</span>
              </div>
              <div className={s.columnBody}>
                {colReports.length === 0 ? (
                  <div className={s.columnEmpty}>{t('project.report.kanban.noReports')}</div>
                ) : (
                  colReports.map((report) => (
                    <div key={report.id} className={s.reportCard} onClick={() => handleOpenReport(report)}>
                      <div className={s.reportCardHeader}>
                        <FileText size={14} className={s.reportIcon} />
                        <span className={s.reportTitle}>{report.title}</span>
                      </div>
                      <div className={s.reportMeta}>
                        <span>{report.template_name}</span>
                        <span className={s.dot}>·</span>
                        <span>{report.output_format}</span>
                        <span className={s.dot}>·</span>
                        <span>{new Date(report.created_at).toLocaleDateString()}</span>
                      </div>
                      <div className={s.reportCardFooter}>
                        {report.status === 'done' && (
                          <span className={s.statusCompleted}>
                            <CheckCircle size={12} /> {t('project.report.completed')}
                          </span>
                        )}
                        {report.status === 'generating' && (
                          <span className={s.statusGenerating}>
                            <Clock size={12} /> {report.progress || 0}%
                          </span>
                        )}
                        {report.status === 'error' && (
                          <span
                            className={s.statusFailed}
                            title={report.error_message || t('project.report.failed')}
                          >
                            <AlertCircle size={12} /> {t('project.report.failed')}
                          </span>
                        )}
                        {report.status === 'error' && (
                          <button
                            className={s.retryBtn}
                            onClick={(e) => { e.stopPropagation(); handleRetryReport(report, 'resume') }}
                            title={t('project.report.retry')}
                          >
                            <RefreshCw size={12} />
                          </button>
                        )}
                        <button
                          className={s.exportBtn}
                          onClick={(e) => { e.stopPropagation(); handleDeleteReport(report) }}
                          title={t('project.report.delete')}
                        >
                          <Trash2 size={12} />
                        </button>
                        <button
                          className={s.exportBtn}
                          onClick={(e) => { e.stopPropagation(); handleExportReport(report) }}
                          disabled={report.status !== 'done'}
                        >
                          {t('project.report.export')}
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className={s.footer}>
        {t('project.report.kanban.totalReports', { count: reports.length })}
      </div>
    </div>
  )
}
