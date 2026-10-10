/** src/features/project/ProjectSidebar.tsx — Sidebar wrapper: ProjectList (base lib moved to permanent footer) */
import { ProjectList } from '../knowledge/ProjectList'
import type { Project, CreateProjectFn } from '../knowledge/types'
import type { ProjectHealthSummary } from './affairs/types'
import s from './ProjectSidebar.module.css'

interface ProjectSidebarProps {
  projects: Project[]
  selectedProject: string | null
  onSelectProject: (projectId: string) => void
  onDeleteProject: (projectId: string) => void
  onRenameProject: (projectId: string, name: string) => void
  onCreateProject: CreateProjectFn
  /** 3.0 三期:per 项目健康概要(透传 ProjectList 色点) */
  healthData?: Record<string, ProjectHealthSummary>
}

export function ProjectSidebar({
  projects,
  selectedProject,
  onSelectProject,
  onDeleteProject,
  onRenameProject,
  onCreateProject,
  healthData,
}: ProjectSidebarProps) {
  return (
    <div className={s.container}>
      {/* Project list — fills the scrollable area */}
      <ProjectList
        projects={projects}
        selectedProject={selectedProject}
        onSelect={onSelectProject}
        onDelete={onDeleteProject}
        onRename={onRenameProject}
        onCreate={onCreateProject}
        healthData={healthData}
      />
    </div>
  )
}
