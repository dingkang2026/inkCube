import { useState, useCallback, useEffect } from 'react'
import { TitleBar } from './components/layout/TitleBar'
import { AboutDialog } from './components/layout/AboutDialog'
import { Sidebar } from './components/layout/Sidebar'
import { ResizeHandle } from './components/layout/ResizeHandle'
import { SettingsPage } from './features/settings/SettingsPage'
import { LogViewer } from './features/settings/LogViewer'
import { ProjectDetailPage } from './features/project/ProjectDetailPage'
import { ProjectSidebar } from './features/project/ProjectSidebar'
import { BaseLibFooter } from './features/project/BaseLibFooter'
import { BaseLibDetailPage } from './features/project/BaseLibDetailPage'
import { KnowledgePage } from './features/knowledge/KnowledgePage'
import { HomePage } from './features/home/HomePage'
import { TaskPage } from './features/task/TaskPage'
import { TaskList } from './features/task/TaskList'
import { useTasks } from './features/task/useTasks'
import { useTaskChat } from './features/task/useTaskChat'
import { useSettings } from './features/settings/useSettings'
import { useResizeHandle } from './hooks/useResizeHandle'
import type { Project } from './features/knowledge/types'
import type { ProjectType } from './features/project/types'
import { setProjectExtState } from './features/project/types'

export default function App() {
  const { settings, handleSaveSettings } = useSettings()
  const sidebar = useResizeHandle(260, 200, 480)

  // ── Task state ──
  const {
    tasks, archivedTasks, currentTasks, activeTab, setActiveTab,
    activeTaskId, setActiveTaskId, loadTasks, loading: tasksLoading,
    creating: taskCreating,
    handleCreateTask, handleCreateTaskWithDir, handleDeleteTask, handleRenameTask,
    handleArchiveTask, handleUnarchiveTask, handleExportTask, handleSetTaskMode,
  } = useTasks()

  const {
    messages: taskMessages,
    isGenerating: taskIsGenerating,
    streamContent: taskStreamContent,
    streamSteps: taskStreamSteps,
    streamReasoning: taskStreamReasoning,
    streamIteration: taskStreamIteration,
    streamIterationThoughts: taskStreamIterationThoughts,
    streamIterationReasoning: taskStreamIterationReasoning,
    planData: taskPlanData,
    pendingShellConfirm: taskPendingShellConfirm,
    pendingSkillConfirm: taskPendingSkillConfirm,
    handleSendMessage: handleTaskSendMessage,
    handleStopGenerate: handleTaskStopGenerate,
    handlePauseTask: handleTaskPause,
    handleShellConfirm: handleTaskShellConfirm,
    handleSkillConfirm: handleTaskSkillConfirm,
    handleResumeTask: handleTaskResume,
    canResume: taskCanResume,
    resetGeneratingState: resetTaskGeneratingState,
  } = useTaskChat(activeTaskId, useCallback(() => loadTasks(), []))

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [logViewerOpen, setLogViewerOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('home')
  const [processExitCode, setProcessExitCode] = useState<number | null>(null)
  const [canStartTask, setCanStartTask] = useState(true)
  const [runningTaskIds, setRunningTaskIds] = useState<Set<string>>(new Set())

  // ── Knowledge base state ──
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [selectedBaseLibId, setSelectedBaseLibId] = useState<string | null>(null)

  const loadProjects = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('knowledge:listProjects')
      setProjects(result.projects || [])
    } catch (err) {
      console.error('Failed to load projects:', err)
    }
  }, [])

  useEffect(() => {
    loadProjects()
  }, [loadProjects])

  // Auto-select first project when navigating to projects tab (so KnowledgePage features are immediately accessible)
  useEffect(() => {
    if (activeNav === 'projects' && projects.length > 0 && !selectedProjectId && !selectedBaseLibId) {
      setSelectedProjectId(projects[0].id)
    }
  }, [activeNav, projects, selectedProjectId, selectedBaseLibId])

  // Poll task status for concurrent limit + running task IDs
  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const status = await window.electronAPI.invoke('tasks:getStatus') as {
          can_start: boolean; running_task_ids?: string[]
        }
        if (!cancelled) {
          setCanStartTask(status.can_start)
          setRunningTaskIds(new Set(status.running_task_ids || []))
        }
      } catch { /* ignore */ }
    }
    poll()
    const timer = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [])

  // Refresh task status when generation finishes
  useEffect(() => {
    if (!taskIsGenerating) {
      const timer = setTimeout(async () => {
        try {
          const status = await window.electronAPI.invoke('tasks:getStatus') as { can_start: boolean }
          setCanStartTask(status.can_start)
        } catch { /* ignore */ }
      }, 500)
      return () => clearTimeout(timer)
    }
  }, [taskIsGenerating])

  const selectedProject = selectedProjectId
    ? projects.find((p) => p.id === selectedProjectId) || null
    : null

  const handleProjectSelect = (projectId: string) => {
    setSelectedBaseLibId(null)
    setSelectedProjectId((prev) => (prev === projectId ? null : projectId))
  }

  const handleBaseLibSelect = (baseLibId: string) => {
    setSelectedProjectId(null)
    setSelectedBaseLibId((prev) => (prev === baseLibId ? null : baseLibId))
  }

  const handleDeleteProject = async (projectId: string) => {
    if (!confirm('确认删除此项目？所有文件和索引将被清除。')) return
    try {
      await window.electronAPI.invoke('knowledge:deleteProject', projectId)
      await loadProjects()
      if (selectedProjectId === projectId) setSelectedProjectId(null)
    } catch (err) {
      console.error('Failed to delete project:', err)
    }
  }

  const handleCreateProject = async (name: string, description?: string, type?: ProjectType) => {
    const result: any = await window.electronAPI.invoke('knowledge:createProject', { name, description })
    await loadProjects()
    // Auto-select the newly created project
    if (result?.id) {
      // Set project type if specified
      if (type) {
        setProjectExtState(result.id, type)
      }
      setSelectedProjectId(result.id)
    }
  }

  const handleRenameProject = async (projectId: string, name: string) => {
    try {
      await window.electronAPI.invoke('knowledge:renameProject', { projectId, name })
      await loadProjects()
    } catch (err) {
      console.error('Failed to rename project:', err)
    }
  }

  const handleNavChange = useCallback((nav: string) => {
    setActiveNav(nav)
    // Auto-close settings when switching tabs
    setSettingsOpen(false)
  }, [])

  const handleNewTaskWithDir = async () => {
    const result = await window.electronAPI.invoke('tasks:selectDirectory') as { canceled?: boolean; path?: string }
    if (result.canceled || !result.path) return
    const confirmed = window.confirm(
      `确认使用此目录作为工作区？\n\n${result.path}\n\n目录中的文件可能会被任务修改。`
    )
    if (confirmed) {
      handleCreateTaskWithDir(result.path)
    }
  }

  // ── Keyboard shortcuts: Ctrl+1~3 for navigation ──
  useEffect(() => {
    const NAV_KEYS: Record<string, string> = {
      '1': 'home', '2': 'tasks', '3': 'projects',
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.shiftKey && !e.altKey && NAV_KEYS[e.key]) {
        e.preventDefault()
        const target = NAV_KEYS[e.key]
        handleNavChange(target)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleNavChange])

  // ── Electron IPC listeners ──
  useEffect(() => {
    const offSettings = window.electronAPI.on('menu:open-settings', () => {
      setSettingsOpen(true)
    })
    const offViewLogs = window.electronAPI.on('menu:view-logs', () => {
      setLogViewerOpen(true)
    })
    const offExportLogs = window.electronAPI.on('menu:export-logs', () => {
      window.electronAPI.invoke('system:exportLogs')
    })
    // Listen for navigate-settings from useErrorHandler / HealthStatus
    const handleNavSettings = () => setSettingsOpen(true)
    window.addEventListener('app:navigate-settings', handleNavSettings)
    // Listen for Python process exit
    const offProcessExit = window.electronAPI.on('python:process-exit', (data: unknown) => {
      const { code } = data as { code: number }
      setProcessExitCode(code)
      // Reset generating state to prevent UI from being stuck
      resetTaskGeneratingState()
    })
    return () => {
      offSettings()
      offViewLogs()
      offExportLogs()
      offProcessExit?.()
      window.removeEventListener('app:navigate-settings', handleNavSettings)
    }
  }, [])

  return (
    <>
      <TitleBar
        onOpenSettings={() => setSettingsOpen(true)}
        onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
        onShowAbout={() => setAboutOpen(true)}
      />
      <div className="app-layout">
        {/* Sidebar */}
        {!sidebarCollapsed && (
          <>
            <Sidebar
              width={sidebar.width}
              activeNav={activeNav}
              onNavChange={handleNavChange}
              onOpenSettings={() => setSettingsOpen(true)}
              footer={
                <BaseLibFooter
                  selectedBaseLib={selectedBaseLibId}
                  onSelectBaseLib={handleBaseLibSelect}
                />
              }
            >
              {activeNav === 'projects' && (
                <ProjectSidebar
                  projects={projects}
                  selectedProject={selectedProjectId}
                  onSelectProject={handleProjectSelect}
                  onDeleteProject={handleDeleteProject}
                  onRenameProject={handleRenameProject}
                  onCreateProject={handleCreateProject}
                />
              )}
              {activeNav === 'tasks' && (
                <TaskList
                  tasks={currentTasks}
                  activeTaskId={activeTaskId}
                  activeTab={activeTab}
                  loading={tasksLoading}
                  creating={taskCreating}
                  onSelect={setActiveTaskId}
                  onNew={() => handleCreateTask()}
                  onNewWithDir={handleNewTaskWithDir}
                  onDelete={handleDeleteTask}
                  onRename={handleRenameTask}
                  onArchive={handleArchiveTask}
                  onUnarchive={handleUnarchiveTask}
                  onExport={handleExportTask}
                  onTabChange={setActiveTab}
                  archivedCount={archivedTasks.length}
                  canCreateTask={canStartTask}
                  runningTaskIds={runningTaskIds}
                />
              )}
            </Sidebar>
            <ResizeHandle
              onResize={sidebar.handleResize}
              onDoubleClick={sidebar.resetWidth}
            />
          </>
        )}

        {settingsOpen ? (
          <SettingsPage
            settings={settings}
            onSave={handleSaveSettings}
            onClose={() => setSettingsOpen(false)}
          />
        ) : selectedBaseLibId ? (
          /* Base library is parallel to projects — accessible from any nav */
          <BaseLibDetailPage
            baseLibId={selectedBaseLibId}
            onBack={() => setSelectedBaseLibId(null)}
          />
        ) : activeNav === 'home' ? (
          <HomePage
            projects={projects}
            onNavigate={handleNavChange}
            onOpenSettings={() => setSettingsOpen(true)}
            onSelectProject={(id) => {
              setSelectedProjectId(id)
            }}
          />
        ) : activeNav === 'projects' ? (
          selectedProject ? (
            <ProjectDetailPage
              project={selectedProject}
              onOpenSettings={() => setSettingsOpen(true)}
              onProjectDataChanged={loadProjects}
            />
          ) : (
            <KnowledgePage
              selectedProject={null}
              onOpenSettings={() => setSettingsOpen(true)}
              onProjectDataChanged={loadProjects}
            />
          )
        ) : activeNav === 'tasks' ? (
          <TaskPage
            taskId={activeTaskId}
            taskTitle={tasks.find((t) => t.id === activeTaskId)?.title}
            taskMode={tasks.find((t) => t.id === activeTaskId)?.mode || 'coding'}
            hasActiveTask={!!activeTaskId}
            messages={taskMessages}
            isGenerating={taskIsGenerating}
            streamContent={taskStreamContent}
            streamSteps={taskStreamSteps}
            streamReasoning={taskStreamReasoning}
            streamIteration={taskStreamIteration}
            streamIterationThoughts={taskStreamIterationThoughts}
            streamIterationReasoning={taskStreamIterationReasoning}
            planData={taskPlanData}
            pendingShellConfirm={taskPendingShellConfirm}
            pendingSkillConfirm={taskPendingSkillConfirm}
            onSendMessage={handleTaskSendMessage}
            onStopGenerate={handleTaskStopGenerate}
            onPauseTask={handleTaskPause}
            onShellConfirm={handleTaskShellConfirm}
            onSkillConfirm={handleTaskSkillConfirm}
            onResumeTask={handleTaskResume}
            canResume={taskCanResume}
            onFilesUploaded={() => loadTasks()}
            onSwitchMode={(mode) => activeTaskId && handleSetTaskMode(activeTaskId, mode)}
          />
        ) : (
          <div />
        )}
      </div>
      <AboutDialog open={aboutOpen} onClose={() => setAboutOpen(false)} />
      {logViewerOpen && <LogViewer onClose={() => setLogViewerOpen(false)} />}
      {processExitCode !== null && (
        <div className="process-exit-banner">
          <span>服务进程已退出 (code: {processExitCode})，请检查日志或重启应用</span>
          <button onClick={() => setSettingsOpen(true)}>查看日志</button>
          <button onClick={() => setProcessExitCode(null)}>关闭</button>
        </div>
      )}
    </>
  )
}
