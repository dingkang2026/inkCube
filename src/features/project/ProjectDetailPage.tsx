/** src/features/project/ProjectDetailPage.tsx — Main project detail page with 4-tab structure */
import { useState, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, BookOpen, FileText, ClipboardList } from 'lucide-react'
import { KnowledgePage } from '../knowledge/KnowledgePage'
import { ReportsTab } from './tabs/ReportsTab'
import { WikiTab } from './tabs/WikiTab'
import { FormsTab } from './tabs/FormsTab'
import { ProjectChatPanel } from './ProjectChatPanel'
import type { Project } from '../knowledge/types'
import type { ProjectType, ProjectStage } from './types'
import { PROJECT_TYPE_CONFIG, STAGE_MAP, getProjectExtState, projectExtState } from './types'
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

/** UI-First: project type/stage stored in shared module-level state */

export function ProjectDetailPage({ project, onOpenSettings, onProjectDataChanged }: ProjectDetailPageProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabKey>('materials')
  const [chatFileId, setChatFileId] = useState<string | null>(null)
  const [chatFilename, setChatFilename] = useState<string | null>(null)

  const ext = getProjectExtState(project.id)
  const [projectType, setProjectType] = useState(ext.type)
  const [currentStage, setCurrentStage] = useState(ext.stage)

  // Re-sync local state when switching to a different project
  useEffect(() => {
    const newExt = getProjectExtState(project.id)
    setProjectType(newExt.type)
    setCurrentStage(newExt.stage)
  }, [project.id])

  const handleStageClick = useCallback((stage: ProjectStage) => {
    setCurrentStage(stage)
    projectExtState.set(project.id, { type: projectType, stage })
  }, [projectType, project.id])

  const handleTypeChange = useCallback((newType: ProjectType) => {
    setProjectType(newType)
    const stages = STAGE_MAP[newType]
    const newStage: ProjectStage = stages.length > 0 ? stages[0].key : 'none'
    setCurrentStage(newStage)
    projectExtState.set(project.id, { type: newType, stage: newStage })
  }, [project.id])

  const typeConfig = PROJECT_TYPE_CONFIG[projectType]

  return (
    <div className={s.page}>
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
          </button>
        ))}
      </div>

      {/* Tab content + chat panel (side by side) */}
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
            <WikiTab projectType={projectType} />
          )}
          {activeTab === 'reports' && (
            <ReportsTab projectType={projectType} currentStage={currentStage} />
          )}
          {activeTab === 'forms' && (
            <FormsTab projectType={projectType} currentStage={currentStage} />
          )}
        </div>

        {/* Right-side chat panel */}
        <ProjectChatPanel
          projectId={project.id}
          projectName={project.name}
          selectedFileId={chatFileId || undefined}
          selectedFilename={chatFilename || undefined}
          onClearFileScope={() => { setChatFileId(null); setChatFilename(null) }}
        />
      </div>
    </div>
  )
}
