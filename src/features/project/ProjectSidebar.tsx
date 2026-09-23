/** src/features/project/ProjectSidebar.tsx — Sidebar wrapper: ProjectList (base lib moved to permanent footer) */
import { ProjectList } from '../knowledge/ProjectList'
import type { Project } from '../knowledge/types'
import type { ProjectType } from './types'
import s from './ProjectSidebar.module.css'

interface ProjectSidebarProps {
  projects: Project[]
  selectedProject: string | null
  onSelectProject: (projectId: string) => void
  onDeleteProject: (projectId: string) => void
  onRenameProject: (projectId: string, name: string) => void
  onCreateProject: (name: string, description?: string, type?: ProjectType) => void
}

export function ProjectSidebar({
  projects,
  selectedProject,
  onSelectProject,
  onDeleteProject,
  onRenameProject,
  onCreateProject,
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
      />
    </div>
  )
}
