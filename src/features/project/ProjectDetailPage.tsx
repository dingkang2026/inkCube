/** src/features/project/ProjectDetailPage.tsx — Main project detail page with 4-tab structure */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, BookOpen, FileText, ClipboardList } from 'lucide-react'
import { KnowledgePage } from '../knowledge/KnowledgePage'
import { useToast } from '../knowledge/Toast'
import { ReportsTab } from './tabs/ReportsTab'
import { WikiTab } from './tabs/WikiTab'
import { FormsTab } from './tabs/FormsTab'
import { ProjectChatPanel } from './ProjectChatPanel'
import type { Project } from '../knowledge/types'
import type { ProjectType, ProjectStage } from './types'
import { PROJECT_TYPE_CONFIG, STAGE_MAP, getInitialStage } from './types'
import s from './ProjectDetailPage.module.css'

interface ProjectDetailPageProps {
  project: Project
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

export function ProjectDetailPage({ project, onOpenSettings, onProjectDataChanged }: ProjectDetailPageProps) {
  const { t } = useTranslation()
  const showToast = useToast()
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

  // Wiki compile status polling (adaptive frequency based on state)
  const [wikiCompileState, setWikiCompileState] = useState<'idle' | 'compiling' | 'waiting_review'>('idle')
  const wikiCompilePollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let cancelled = false

    const checkStatus = async () => {
      try {
        const result: any = await window.electronAPI.invoke('wiki:compileStatus', { projectId: project.id })
        if (cancelled) return
        const task = result?.task
        if (task?.status === 'waiting_review') {
          setWikiCompileState('waiting_review')
        } else if (task && ['queued', 'ingesting', 'storing'].includes(task.status)) {
          setWikiCompileState('compiling')
        } else {
          setWikiCompileState('idle')
        }
      } catch (err) {
        console.debug('Wiki compile status check failed (backend may not be ready):', err)
      }
    }

    // Adaptive polling: compiling=5s, waiting_review=10s, idle=30s
    const getInterval = () => {
      if (wikiCompileState === 'compiling') return 5000
      if (wikiCompileState === 'waiting_review') return 10000
      return 30000
    }

    // Initial check
    checkStatus()

    const scheduleNext = () => {
      wikiCompilePollRef.current = setTimeout(async () => {
        await checkStatus()
        if (!cancelled) scheduleNext()
      }, getInterval())
    }
    scheduleNext()

    return () => {
      cancelled = true
      if (wikiCompilePollRef.current) clearTimeout(wikiCompilePollRef.current)
    }
  }, [project.id, wikiCompileState])

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
              <span className={s.wikiBadgeCompiling} />
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
