/** src/features/project/tabs/ReportsTab.tsx — Reports tab with wizard, preview, template mgmt (UI-First) */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText, Plus, Settings2, Clock, CheckCircle, AlertCircle } from 'lucide-react'
import type { ProjectType, ProjectStage, MockReport } from '../types'
import { ReportWizard } from './ReportWizard'
import { ReportPreview } from './ReportPreview'
import { TemplateManagement } from './TemplateManagement'
import s from './ReportsTab.module.css'

interface ReportsTabProps {
  projectType: ProjectType
  currentStage: ProjectStage
}

/** Initial mock generated reports */
const INITIAL_REPORTS: MockReport[] = [
  { id: 'r1', title: 'XX公司尽职调查报告', templateName: '尽职调查报告', outputFormat: 'Word', createdAt: '2026-09-20', status: 'completed' },
  { id: 'r2', title: '新能源行业研究报告', templateName: '行业研究报告', outputFormat: 'Word', createdAt: '2026-09-18', status: 'completed' },
  { id: 'r3', title: 'XX项目招商推介', templateName: '招商推介方案', outputFormat: 'PPT', createdAt: '2026-09-23', status: 'generating', progress: 60 },
]

const STATUS_FILTER_KEYS = ['all', 'dueDiligence', 'industryResearch', 'investmentAnalysis', 'recruitmentPlan'] as const

type ViewMode = 'list' | 'wizard' | 'preview' | 'templateManagement'

export function ReportsTab({ projectType, currentStage }: ReportsTabProps) {
  const { t } = useTranslation()
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [activeFilter, setActiveFilter] = useState('all')
  const [reports, setReports] = useState<MockReport[]>(INITIAL_REPORTS)
  const [previewReport, setPreviewReport] = useState<MockReport | null>(null)

  const statusFilters = STATUS_FILTER_KEYS.map(key => ({
    key,
    label: t(`project.report.filter.${key}`),
  }))

  const handleWizardComplete = (newReport: MockReport) => {
    setReports(prev => [newReport, ...prev])
    setViewMode('list')
  }

  const handleOpenReport = (report: MockReport) => {
    setPreviewReport(report)
    setViewMode('preview')
  }

  const handleExportReport = (report: MockReport) => {
    alert(t('project.report.exportSuccess', { format: report.outputFormat, title: report.title }))
  }

  // ── Wizard view ──
  if (viewMode === 'wizard') {
    return (
      <ReportWizard
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

  // ── List view (default) ──
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

      {/* Filters */}
      <div className={s.filters}>
        {statusFilters.map((f) => (
          <button
            key={f.key}
            className={`${s.filterBtn} ${activeFilter === f.key ? s.filterBtnActive : ''}`}
            onClick={() => setActiveFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Report list */}
      <div className={s.reportList}>
        {reports.map((report) => (
          <div key={report.id} className={s.reportCard}>
            <div className={s.reportIcon}>
              <FileText size={20} />
            </div>
            <div className={s.reportInfo}>
              <div className={s.reportTitle}>{report.title}</div>
              <div className={s.reportMeta}>
                <span>{t('project.report.template')}: {report.templateName}</span>
                <span className={s.dot}>·</span>
                <span>{report.outputFormat}</span>
                <span className={s.dot}>·</span>
                <span>{report.createdAt}</span>
              </div>
            </div>
            <div className={s.reportStatus}>
              {report.status === 'completed' && (
                <span className={s.statusCompleted}>
                  <CheckCircle size={14} /> {t('project.report.completed')}
                </span>
              )}
              {report.status === 'generating' && (
                <span className={s.statusGenerating}>
                  <Clock size={14} /> {report.progress}%
                </span>
              )}
              {report.status === 'failed' && (
                <span className={s.statusFailed}>
                  <AlertCircle size={14} /> {t('project.report.failed')}
                </span>
              )}
            </div>
            <div className={s.reportActions}>
              <button className={s.actionBtn} onClick={() => handleOpenReport(report)}>
                {t('project.report.open')}
              </button>
              <button className={s.actionBtn} onClick={() => handleExportReport(report)}>
                {t('project.report.export')}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className={s.footer}>
        {t('project.report.totalReports', { count: reports.length })}
      </div>
    </div>
  )
}
