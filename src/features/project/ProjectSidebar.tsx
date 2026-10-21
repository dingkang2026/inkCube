/** src/features/project/ProjectSidebar.tsx — Sidebar wrapper: ProjectList (base lib moved to permanent footer) */
import { useEffect, useRef } from 'react'
import { ProjectList } from '../knowledge/ProjectList'
import { useActivity } from '../activity/ActivityContext'
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
  /** 归档(2.1 §归档):有效/归档 tab 状态与分流;列表内容已由 App 按 tab 分发 */
  activeTab: 'active' | 'archived'
  onTabChange: (tab: 'active' | 'archived') => void
  archivedCount: number
  /** 列表分页（0.1dev-guide 统一约定）：当前 tab 的加载更多状态与回调（App 按双 tab 分派） */
  hasMoreProjects?: boolean
  loadingMoreProjects?: boolean
  onLoadMoreProjects?: () => void
  onArchiveProject: (projectId: string) => void
  onUnarchiveProject: (projectId: string) => void
  /** 导出项目（2.9 §8）：透传 ProjectList 菜单项 */
  onExportProject?: (projectId: string, projectName: string) => void
  /** 导入项目包（2.9 §8）：透传 ProjectList 导入按钮 */
  onImportProject?: () => void
  /** 导入完成边沿（2.9 §8.2）：后台导入结束（成功/失败）时回调一次，App 刷新列表并选中新项目 */
  onImportCompleted?: () => void
  /** 0.5 E1 T4:首页「新建项目」信号(透传 ProjectList 自动打开创建模态) */
  createSignal?: number
  onCreateSignalConsumed?: () => void
}

export function ProjectSidebar({
  projects,
  selectedProject,
  onSelectProject,
  onDeleteProject,
  onRenameProject,
  onCreateProject,
  healthData,
  activeTab,
  onTabChange,
  archivedCount,
  hasMoreProjects,
  loadingMoreProjects,
  onLoadMoreProjects,
  onArchiveProject,
  onUnarchiveProject,
  onExportProject,
  onImportProject,
  onImportCompleted,
  createSignal,
  onCreateSignalConsumed,
}: ProjectSidebarProps) {
  // 导入完成边沿检测：本组件渲染在 ActivityProvider 内（App 本体在 Provider 外，
  // useActivity 不可用，同 L233 注释先例），由这里上报 App（2.9 §8.2）
  const { isActive: importActive } = useActivity('project_import')
  const prevImportActiveRef = useRef(false)
  useEffect(() => {
    if (prevImportActiveRef.current && !importActive) onImportCompleted?.()
    prevImportActiveRef.current = importActive
  }, [importActive, onImportCompleted])

  return (
    <div className={s.container}>
      {/* Project list — fills the scrollable area */}
      <ProjectList
        projects={projects}
        selectedProject={selectedProject}
        onSelect={onSelectProject}
        onDelete={onDeleteProject}
        onRename={onRenameProject}
        onExportProject={onExportProject}
        onImportProject={onImportProject}
        onCreate={onCreateProject}
        healthData={healthData}
        activeTab={activeTab}
        onTabChange={onTabChange}
        archivedCount={archivedCount}
        hasMoreProjects={hasMoreProjects}
        loadingMoreProjects={loadingMoreProjects}
        onLoadMoreProjects={onLoadMoreProjects}
        onArchiveProject={onArchiveProject}
        onUnarchiveProject={onUnarchiveProject}
        createSignal={createSignal}
        onCreateSignalConsumed={onCreateSignalConsumed}
      />
    </div>
  )
}
