/** src/features/project/tabs/ReportsTab.tsx — Reports tab with kanban layout (4 columns by stage)
 *  含 PPT 交付物区块（2.3 §7.1 PptSection）：列表/新建/详情入口 */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText, Plus, Clock, CheckCircle, AlertCircle, Trash2, RefreshCw, Pause, PauseCircle, Play, Presentation } from 'lucide-react'
import type { ProjectType, ProjectStage, Report } from '../types'
import { STAGE_COLUMNS } from '../types'
import { ReportWizard } from './ReportWizard'
import { ReportPreview } from './ReportPreview'
import { PptWizard } from './PptWizard'
import { PptDetail } from './PptDetail'
import { useToast } from '../../knowledge/Toast'
import s from './ReportsTab.module.css'
import p from './ppt.module.css'
import { useConfirm } from '../../../components/ConfirmDialog'
import { usePolling } from '../../../hooks/usePolling'
import { useEdgeRefetch } from '../../../hooks/useEdgeRefetch'
import { usePagedList } from '../../../hooks/usePagedList'
import { useActivity } from '../../activity/ActivityContext'
import { apiErrorText, translateStoredError } from '../../../i18n/errorCodes'
import { UsageText } from '../../usage/UsageText'

type ViewMode = 'list' | 'wizard' | 'preview' | 'pptWizard' | 'pptDetail'

/** ppts 行（与后端 list_ppts 返回对齐） */
interface PptRow {
  id: string
  title: string
  kind: string | null
  status: string
  stage: string | null
  progress: number | null
  page_count: number | null
  page_done: number | null
  export_path: string | null
  created_at: string
}

interface ReportsTabProps {
  projectId: string
  projectType: ProjectType
  /** 视图态上报（OutputTab 二级分区条仅在列表态显示，详情/向导占据全部内容区，2026-10-27） */
  onViewModeChange?: (mode: ViewMode) => void
}

export function ReportsTab({ projectId, projectType, onViewModeChange }: ReportsTabProps) {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [previewReport, setPreviewReport] = useState<Report | null>(null)
  const [selectedPptId, setSelectedPptId] = useState<string | null>(null)

  // 视图态上报（挂载即报 'list'，切换即随上报；父级据此显隐二级分区条）
  useEffect(() => {
    onViewModeChange?.(viewMode)
  }, [viewMode, onViewModeChange])

  // 列表分页（0.1dev-guide 统一约定）：50/页「加载更多」；projectId 变化经 params 自动重拉首页
  const {
    items: reports,
    total: reportsTotal,
    hasMore: hasMoreReports,
    loadingMore: loadingMoreReports,
    loading: reportsLoading,
    error: reportsError,
    reload: reloadReports,
    loadMore: loadMoreReports,
  } = usePagedList<Report>({ channel: 'reports:listReports', itemsKey: 'reports', params: { projectId } })
  const {
    items: ppts,
    total: pptsTotal,
    hasMore: hasMorePpts,
    loadingMore: loadingMorePpts,
    error: pptsError,
    reload: reloadPpts,
    loadMore: loadMorePpts,
  } = usePagedList<PptRow>({ channel: 'ppt:list', itemsKey: 'ppts', params: { projectId } })

  // 列表加载失败提示（对齐 FormsTab m-3：静默吞掉会让列表空白且无任何提示）
  useEffect(() => {
    if (reportsError) showToast(`${t('common.listLoadFailed')}: ${reportsError}`, 'error')
  }, [reportsError, showToast, t])
  useEffect(() => {
    if (pptsError) showToast(`${t('common.listLoadFailed')}: ${pptsError}`, 'error')
  }, [pptsError, showToast, t])

  // 列表与 preview 快照同步：previewReport 是点击时的快照，若只更新列表不更新它，
  // 生成中打开的预览拿陈旧 status，ReportPreview 终态边沿（0.4 §6.3）永无触发前提
  useEffect(() => {
    setPreviewReport((prev) => (prev ? reports.find((r) => r.id === prev.id) ?? prev : prev))
  }, [reports])

  useEffect(() => {
    // 同类型项目切换清预览/向导残留（review Info-7，WikiTab H-2 同款隔离语义）：
    // previewReport 是点击时快照，find 不到会永久保留旧项目报告，
    // 且预览内导出/删除/暂停只按 report.id 定位（后端无项目校验）
    setPreviewReport(null)
    setViewMode('list')
  }, [projectId])

  // ── PPT 交付物（2.3 §7.1 PptSection）──
  // 活跃期心跳驱动轮询 + 终态边沿补拉（同报告列表模式，0.4 §6.2/§6.3）
  const { items: activePptItems } = useActivity('ppt')
  const hasActivePpt = activePptItems.some((a) => a.project_id === projectId)
  usePolling({
    name: 'ppts-active',
    enabled: hasActivePpt,
    interval: 3000,
    // reload 按已加载量重拉（不退回第一页）；内部 catch 吞错由 usePolling 视为成功
    task: () => reloadPpts(),
  })
  useEdgeRefetch(hasActivePpt, () => { void reloadPpts() })

  // Auto-refresh when any report is generating（#7，v1.7 迁移 usePolling；谓词收编全局心跳
  // useActivity('report')，顺序 4）：本地列表是挂载快照，存在陈旧漏判（如停轮期间
  // 其他页面触发的生成）；心跳是全局事实，以它驱动轮询启停
  const { items: activeReportItems } = useActivity('report')
  const hasGenerating = activeReportItems.some(a => a.project_id === projectId)
  usePolling({
    name: 'reports-generating',
    enabled: hasGenerating,
    interval: 3000,
    // reload 按已加载量重拉（不退回第一页）；内部 catch 吞错由 usePolling 视为成功
    task: () => reloadReports(),
  })

  // 终态边沿补拉（0.4 §6.3）：心跳停轮可早于最后一个轮询 tick，列表可能停在
  // generating；hasGenerating 翻 false 的边沿补拉一次，终态（done/error）立即上屏
  useEdgeRefetch(hasGenerating, () => { void reloadReports() })

  const handleWizardComplete = async () => {
    await reloadReports()
    setViewMode('list')
  }

  const handleOpenReport = (report: Report) => {
    setPreviewReport(report)
    setViewMode('preview')
  }

  const handleDeleteReport = async (report: Report) => {
    if (!(await confirmDialog({ title: t('project.report.delete'), message: t('project.report.confirmDelete') }))) return
    try {
      await window.electronAPI.invoke('reports:deleteReport', report.id)
      await reloadReports()
    } catch (err) {
      console.error('Failed to delete report:', err)
      showToast(t('project.report.deleteFailed'), 'error')
    }
  }

  // 看板卡片导出：无面板，直接用详情页导出面板记忆的方向（§1.3 同一 localStorage 键）
  const handleExportReport = async (report: Report) => {
    try {
      let orientation: string = 'portrait'
      try {
        if (localStorage.getItem('inkcube.report.export.orientation') === 'landscape') orientation = 'landscape'
      } catch { /* ignore */ }
      const result: any = await window.electronAPI.invoke('reports:exportReport', {
        reportId: report.id,
        title: report.title,
        orientation,
        saveAs: true,
      })
      if (result.canceled) return
      showToast(t('project.report.exportedTo', { path: result.path }), 'success')
    } catch (err: any) {
      console.error('Export failed:', err)
      showToast(t('project.report.exportFailed', { error: String(err?.message || err) }), 'error')
    }
  }

  const handleRetryReport = async (report: Report, mode: 'resume' | 'restart' = 'resume') => {
    try {
      const result: any = await window.electronAPI.invoke('reports:retryReport', {
        reportId: report.id,
        mode,
      })
      if (result.error) {
        showToast(t('project.report.retryFailed', { error: apiErrorText(result.code, result.error, t) }), 'error')
      } else {
        await reloadReports()
      }
    } catch (err) {
      console.error('Retry failed:', err)
      showToast(t('project.report.retryFailed', { error: String(err) }), 'error')
    }
  }

  // ── 报告级暂停（2.5 §3.1 修订）：写 pause_requested，当前章节完成后生效 → paused；
  // 恢复走 retry(resume) 断点续跑，重新生成走 retry(restart) 全量重跑（需确认）
  const handlePauseReport = async (report: Report) => {
    try {
      const result: any = await window.electronAPI.invoke('reports:pauseReport', { reportId: report.id })
      if (result.error) {
        showToast(t('project.report.pauseFailed', { error: apiErrorText(result.code, result.error, t) }), 'error')
      } else {
        showToast(t('project.report.pauseRequested'), 'info')
        await reloadReports()
      }
    } catch (err) {
      showToast(t('project.report.pauseFailed', { error: String(err) }), 'error')
    }
  }

  const handleRestartReport = async (report: Report) => {
    if (!(await confirmDialog({ title: t('project.report.restartReport'), message: t('project.report.restartConfirm') }))) return
    handleRetryReport(report, 'restart')
  }

  // ── Wizard view ──
  if (viewMode === 'wizard') {
    return (
      <ReportWizard
        projectId={projectId}
        projectType={projectType}
        onComplete={handleWizardComplete}
        onCancel={() => { void reloadReports(); setViewMode('list') }}
      />
    )
  }

  // ── PPT wizard / detail view ──
  if (viewMode === 'pptWizard') {
    return (
      <PptWizard
        projectId={projectId}
        onCreated={(pptId) => { setSelectedPptId(pptId); setViewMode('pptDetail') }}
        onBack={() => { void reloadPpts(); setViewMode('list') }}
      />
    )
  }
  if (viewMode === 'pptDetail' && selectedPptId) {
    return (
      <PptDetail
        pptId={selectedPptId}
        onBack={() => { void reloadPpts(); setViewMode('list') }}
        onDeleted={() => { void reloadPpts(); setSelectedPptId(null); setViewMode('list') }}
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

  // ── Kanban view (default) ──
  // Determine columns to show based on project type
  // For investment/recruitment: show before/during/after + other (for templates without stage)
  // For general: only show "other" column
  const visibleColumns = projectType === 'general'
    ? [{ key: 'none' as ProjectStage, labelKey: 'project.stage.other' }]
    : STAGE_COLUMNS  // before, during, after, none (other)

  if (reportsLoading) {
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
        <button
          className={s.primaryBtn}
          onClick={() => setViewMode('wizard')}
        >
          <Plus size={16} />
          {t('project.report.newReport')}
        </button>
        <button
          className={p.secondaryBtn}
          onClick={() => setViewMode('pptWizard')}
        >
          <Presentation size={16} />
          {t('project.ppt.new')}
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
                        {/* 该报告生成总消耗（2.10 §6.1/线框 6：含模板提取+变量+生成全部调用） */}
                        {report.usage?.calls ? (<> · <UsageText usage={report.usage} /></>) : null}
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
                        {report.status === 'paused' && (
                          <span className={s.statusPaused}>
                            <PauseCircle size={12} /> {t('project.report.pausedStatus')} {report.progress || 0}%
                          </span>
                        )}
                        {report.status === 'error' && (
                          <span
                            className={s.statusFailed}
                            title={report.error_message ? translateStoredError(report.error_message, t) : t('project.report.failed')}
                          >
                            <AlertCircle size={12} /> {t('project.report.failed')}
                          </span>
                        )}
                        {report.status === 'generating' && (
                          <button
                            className={s.retryBtn}
                            onClick={(e) => { e.stopPropagation(); void handlePauseReport(report) }}
                            title={t('project.report.pauseTip')}
                          >
                            <Pause size={12} />
                          </button>
                        )}
                        {report.status === 'paused' && (
                          <>
                            <button
                              className={s.retryBtn}
                              onClick={(e) => { e.stopPropagation(); handleRetryReport(report, 'resume') }}
                              title={t('project.report.resumeReport')}
                            >
                              <Play size={12} />
                            </button>
                            <button
                              className={s.retryBtn}
                              onClick={(e) => { e.stopPropagation(); void handleRestartReport(report) }}
                              title={t('project.report.restartReport')}
                            >
                              <RefreshCw size={12} />
                            </button>
                          </>
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
                          disabled={report.status === 'generating'}
                          title={report.status === 'generating' ? t('project.report.deleteDisabledWhileGenerating') : t('project.report.delete')}
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
        {t('project.report.kanban.totalReports', { count: reportsTotal ?? reports.length })}
      </div>
      {hasMoreReports && (
        <button
          className={s.loadMoreBtn}
          onClick={() => { void loadMoreReports() }}
          disabled={loadingMoreReports}
        >
          {loadingMoreReports ? t('common.loading') : t('common.loadMore')}
        </button>
      )}

      {/* PPT 交付物区块（2.3 §7.1 PptSection）*/}
      <div className={p.pptSection}>
        <div className={p.pptSectionHeader}>
          <span className={p.sectionTitle}>{t('project.ppt.sectionTitle')}</span>
          {(ppts.length > 0 || pptsTotal) && (
            <span className={p.pptCardMeta}>{t('project.ppt.total', { count: pptsTotal ?? ppts.length })}</span>
          )}
        </div>
        {ppts.length === 0 ? (
          <div className={p.pptEmpty}>
            {t('project.ppt.empty')}
            <div className={p.pptEmptyHint}>{t('project.ppt.emptyHint')}</div>
          </div>
        ) : (
          <div className={p.pptGrid}>
            {ppts.map((ppt) => {
              const active = ppt.status === 'running' || ppt.status === 'awaiting_input'
              const progress = Math.round((ppt.progress || 0) * 100)
              return (
                <div key={ppt.id} className={p.pptCard} onClick={() => { setSelectedPptId(ppt.id); setViewMode('pptDetail') }}>
                  <div className={p.pptCardHeader}>
                    <Presentation size={14} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
                    <span className={p.pptCardTitle}>{ppt.title}</span>
                  </div>
                  <div className={p.pptCardMeta}>
                    <span className={`${p.statusChip} ${p[`status_${ppt.status}`] || ''}`}>
                      {t(`project.ppt.status.${ppt.status}`)}
                    </span>
                    {ppt.stage && <span>{t(`project.ppt.stage.${ppt.stage}`)}</span>}
                    {active && <span>· {progress}%</span>}
                  </div>
                  {active && (
                    <div className={p.pptProgressBar}>
                      <div className={p.pptProgressFill} style={{ width: `${progress}%` }} />
                    </div>
                  )}
                  <div className={p.pptCardFooter}>
                    {!!ppt.page_count && (
                      <span>{t('project.ppt.pageProgress', { done: ppt.page_done || 0, count: ppt.page_count })}</span>
                    )}
                    <span>{new Date(ppt.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {hasMorePpts && (
          <button
            className={s.loadMoreBtn}
            onClick={() => { void loadMorePpts() }}
            disabled={loadingMorePpts}
          >
            {loadingMorePpts ? t('common.loading') : t('common.loadMore')}
          </button>
        )}
      </div>
    </div>
  )
}
