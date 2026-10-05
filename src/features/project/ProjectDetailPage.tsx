/** src/features/project/ProjectDetailPage.tsx — Main project detail page with 4-tab structure */
import { useState, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, BookOpen, FileText, ClipboardList } from 'lucide-react'
import { KnowledgePage } from '../knowledge/KnowledgePage'
import { useToast } from '../knowledge/Toast'
import { useActivity } from '../activity/ActivityContext'
import { ReportsTab } from './tabs/ReportsTab'
import { WikiTab } from './tabs/WikiTab'
import type { WikiCompileData } from './tabs/WikiTab'
import { usePolling } from '../../hooks/usePolling'
import { FormsTab } from './tabs/FormsTab'
import { ProjectChatPanel } from './ProjectChatPanel'
import type { Project } from '../knowledge/types'
import type { ProjectType, ProjectStage } from './types'
import { PROJECT_TYPE_CONFIG, STAGE_MAP, getInitialStage } from './types'
import s from './ProjectDetailPage.module.css'

interface ProjectDetailPageProps {
  project: Project
  /** 通知导航信号（App 层 Activity Hub tab 导航）：收到后切换到目标 Tab */
  tabSignal?: { tab: string; ts: number } | null
  onOpenSettings?: () => void
  onProjectDataChanged?: () => void
}

type TabKey = 'materials' | 'wiki' | 'reports' | 'forms'

const TABS: { key: TabKey; labelKey: string; Icon: typeof FolderOpen }[] = [
  { key: 'materials', labelKey: 'project.tab.materials', Icon: FolderOpen },
  { key: 'wiki', labelKey: 'project.tab.wiki', Icon: BookOpen },
  { key: 'reports', labelKey: 'project.tab.reports', Icon: FileText },
  { key: 'forms', labelKey: 'project.tab.forms', Icon: ClipboardList },
]

export function ProjectDetailPage({ project, tabSignal, onOpenSettings, onProjectDataChanged }: ProjectDetailPageProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const { byProject } = useActivity()
  const [activeTab, setActiveTab] = useState<TabKey>('materials')
  const [chatFileId, setChatFileId] = useState<string | null>(null)
  const [chatFilename, setChatFilename] = useState<string | null>(null)
  const [wikiExpanded, setWikiExpanded] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [closePreviewSignal, setClosePreviewSignal] = useState(0)

  // Notify App-level to hide sidebar when entering immersive mode
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('app:wiki-immersive', { detail: { expanded: wikiExpanded } }))
    return () => {
      // Cleanup: restore sidebar when component unmounts
      window.dispatchEvent(new CustomEvent('app:wiki-immersive', { detail: { expanded: false } }))
    }
  }, [wikiExpanded])

  // Wiki compile status polling #4（v1.7 迁移至 usePolling，0.4 §8.1）：
  // 保存完整快照（task + ingest_summaries + is_resumed）下发 WikiTab；Tab 角标从快照派生
  const [wikiCompileData, setWikiCompileData] = useState<WikiCompileData | null>(null)
  const [compileDialogOpen, setCompileDialogOpen] = useState(false)

  // Activity Hub 通知导航信号：切到目标 Tab（materials/wiki/reports）
  useEffect(() => {
    if (tabSignal?.tab && ['materials', 'wiki', 'reports', 'forms'].includes(tabSignal.tab)) {
      setActiveTab(tabSignal.tab as TabKey)
    }
  }, [tabSignal])

  const checkWikiCompileStatus = useCallback(async () => {
    // 抛错由 usePolling 计入连续失败并指数退避（0.4 §5.8）
    const result: any = await window.electronAPI.invoke('wiki:compileStatus', { projectId: project.id })
    setWikiCompileData({
      task: result?.task ?? null,
      ingest_summaries: result?.ingest_summaries ?? [],
      is_resumed: !!result?.is_resumed,
    })
  }, [project.id])

  usePolling({
    name: 'wiki-compile-status',
    task: checkWikiCompileStatus,
    // Adaptive: dialog open=2s, compiling=5s, waiting_review=10s, idle=30s
    interval: () => {
      if (compileDialogOpen) return 2000
      const status = wikiCompileData?.task?.status
      if (status && ['queued', 'ingesting', 'storing'].includes(status)) return 5000
      if (status === 'waiting_review') return 10000
      return 30000
    },
  })

  // Tab 角标派生态（v1.7 起从编译快照派生，不再单独轮询维护）
  const wikiCompileState: 'idle' | 'compiling' | 'waiting_review' =
    wikiCompileData?.task?.status === 'waiting_review'
      ? 'waiting_review'
      : wikiCompileData?.task && ['queued', 'ingesting', 'storing'].includes(wikiCompileData.task.status)
        ? 'compiling'
        : 'idle'

  // Read type/stage from project (persisted in backend)
  const [projectType, setProjectType] = useState<ProjectType>(project.type || 'general')
  const [currentStage, setCurrentStage] = useState<ProjectStage>(project.stage || 'none')

  // Re-sync local state when switching to a different project
  useEffect(() => {
    setProjectType(project.type || 'general')
    setCurrentStage(project.stage || 'none')
  }, [project.id, project.type, project.stage])

  const handleStageClick = useCallback(async (stage: ProjectStage) => {
    const prevStage = currentStage
    setCurrentStage(stage)
    try {
      await window.electronAPI.invoke('knowledge:updateProject', {
        projectId: project.id,
        stage,
      })
      onProjectDataChanged?.()
    } catch (err) {
      console.error('Failed to persist stage change:', err)
      setCurrentStage(prevStage)
      showToast(t('project.stageUpdateFailed'), 'error')
    }
  }, [project.id, currentStage, onProjectDataChanged, showToast, t])

  const handleTypeChange = useCallback(async (newType: ProjectType) => {
    const prevType = projectType
    const prevStage = currentStage
    setProjectType(newType)
    const newStage = getInitialStage(newType)
    setCurrentStage(newStage)
    try {
      await window.electronAPI.invoke('knowledge:updateProject', {
        projectId: project.id,
        type: newType,
        stage: newStage,
      })
      onProjectDataChanged?.()
    } catch (err) {
      console.error('Failed to persist type change:', err)
      setProjectType(prevType)
      setCurrentStage(prevStage)
      showToast(t('project.typeUpdateFailed'), 'error')
    }
  }, [project.id, projectType, currentStage, onProjectDataChanged, showToast, t])

  const typeConfig = PROJECT_TYPE_CONFIG[projectType]

  // Mutual exclusion: when preview opens, close chat; when chat opens, close preview
  const handlePreviewOpenChange = useCallback((isPreviewOpen: boolean) => {
    if (isPreviewOpen) {
      setChatOpen(false)
    }
  }, [])

  const handleChatOpen = useCallback(() => {
    setChatOpen(true)
    setClosePreviewSignal(prev => prev + 1) // Signal WikiTab to close preview
  }, [])

  return (
    <div className={`${s.page}${wikiExpanded ? ` ${s.expanded}` : ''}`}>
      {/* Project header */}
      <div className={s.header}>
        <div className={s.headerTop}>
          <div className={s.headerInfo}>
            <div className={s.typeBadge} title={t(typeConfig.labelKey)}>
              <typeConfig.Icon size={18} />
            </div>
            <div className={s.headerText}>
              <h2 className={s.projectName}>{project.name}</h2>
              {project.description && (
                <p className={s.projectDesc}>{project.description}</p>
              )}
              {(project.company_name || project.credit_code) && (
                <p className={s.companyInfo}>
                  {project.company_name && <span>{project.company_name}</span>}
                  {project.company_name && project.credit_code && <span className={s.companyDot}>·</span>}
                  {project.credit_code && <span className={s.creditCode}>{project.credit_code}</span>}
                </p>
              )}
            </div>
          </div>
          <div className={s.headerActions}>
            {/* Type selector */}
            <select
              className={s.typeSelect}
              value={projectType}
              onChange={(e) => handleTypeChange(e.target.value as ProjectType)}
            >
              {(Object.entries(PROJECT_TYPE_CONFIG) as [ProjectType, typeof typeConfig][]).map(([key, cfg]) => (
                <option key={key} value={key}>{t(cfg.labelKey)}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Stage pills — inline, clickable, bidirectional */}
        {STAGE_MAP[projectType].length > 0 && (
          <div className={s.stageRow}>
            {STAGE_MAP[projectType].map((stage, index) => {
              const isActive = stage.key === currentStage
              const isCompleted = index < STAGE_MAP[projectType].findIndex(s => s.key === currentStage)
              return (
                <button
                  key={stage.key}
                  className={`${s.stagePill} ${isActive ? s.stagePillActive : ''} ${isCompleted ? s.stagePillCompleted : ''}`}
                  onClick={() => handleStageClick(stage.key)}
                >
                  <span className={s.stageDot} />
                  <span>{t(stage.labelKey)}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Tab bar */}
      <div className={s.tabBar}>
        {TABS.map(({ key, labelKey, Icon }) => (
          <button
            key={key}
            className={`${s.tab} ${activeTab === key ? s.tabActive : ''}`}
            onClick={() => setActiveTab(key)}
          >
            <Icon size={15} />
            <span>{t(labelKey)}</span>
            {key === 'wiki' && wikiCompileState === 'waiting_review' && (
              <span className={s.wikiBadge} title={t('project.wiki.waitingReview', { defaultValue: '编译等待审阅' })}>
                {t('project.wiki.reviewBadge', { defaultValue: '待审阅' })}
              </span>
            )}
            {key === 'wiki' && wikiCompileState === 'compiling' && (
              <span className={s.tabPulse} />
            )}
            {key === 'materials' && (byProject.get(project.id)?.files ?? 0) > 0 && (
              <span
                className={s.tabPulse}
                title={t('activity.tipFiles', { count: byProject.get(project.id)?.files })}
              />
            )}
            {key === 'reports' && (byProject.get(project.id)?.reports ?? 0) > 0 && (
              <span
                className={s.tabPulse}
                title={t('activity.tipReports', { count: byProject.get(project.id)?.reports })}
              />
            )}
          </button>
        ))}
      </div>

      {/* Tab content + chat panel */}
      <div className={s.contentArea}>
        <div className={s.tabContent}>
          {activeTab === 'materials' && (
            <KnowledgePage
              selectedProject={project}
              onOpenSettings={onOpenSettings}
              onProjectDataChanged={onProjectDataChanged}
              compact
              onFileSelect={(fileId, filename) => { setChatFileId(fileId); setChatFilename(filename) }}
            />
          )}
          {activeTab === 'wiki' && (
            <WikiTab
              key={projectType}
              projectId={project.id}
              projectType={projectType}
              expanded={wikiExpanded}
              onExpandChange={setWikiExpanded}
              onPreviewOpenChange={handlePreviewOpenChange}
              closePreviewSignal={closePreviewSignal}
              compileData={wikiCompileData}
              onCompileDialogChange={setCompileDialogOpen}
            />
          )}
          {activeTab === 'reports' && (
            <ReportsTab key={projectType} projectId={project.id} projectType={projectType} />
          )}
          {activeTab === 'forms' && (
            <FormsTab key={projectType} projectType={projectType} />
          )}
        </div>

        {/* Chat panel — wrapper stays in flex row (toggle positioning), panel is absolute to .page (full height) */}
        <ProjectChatPanel
          projectId={project.id}
          projectName={project.name}
          selectedFileId={chatFileId || undefined}
          selectedFilename={chatFilename || undefined}
          onClearFileScope={() => { setChatFileId(null); setChatFilename(null) }}
          isOpen={chatOpen}
          onOpen={handleChatOpen}
          onClose={() => setChatOpen(false)}
        />
      </div>
    </div>
  )
}
