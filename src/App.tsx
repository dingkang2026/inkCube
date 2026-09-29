import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
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
import { TemplateManagement } from './features/project/tabs/TemplateManagement'
import { KnowledgePage } from './features/knowledge/KnowledgePage'
import { HomePage } from './features/home/HomePage'
import { TaskPage } from './features/task/TaskPage'
import { TaskList } from './features/task/TaskList'
import { useTasks } from './features/task/useTasks'
import { useTaskChat } from './features/task/useTaskChat'
import { useSettings } from './features/settings/useSettings'
import { useResizeHandle } from './hooks/useResizeHandle'
import { ToastProvider, useToast } from './features/knowledge/Toast'
import type { Project } from './features/knowledge/types'
import type { ProjectType } from './features/project/types'
import { getInitialStage } from './features/project/types'

/** Inner component: polls global wiki review status and shows toast notifications.
 *  Must live inside ToastProvider to access useToast. */
function WikiReviewMonitor({
  onWaitingReviewCountChange,
  onActiveIngestChange,
}: {
  onWaitingReviewCountChange: (count: number) => void
  onActiveIngestChange: (hasActive: boolean) => void
}) {
  const { t } = useTranslation()
  const showToast = useToast()
  const prevProjectIdsRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const result: any = await window.electronAPI.invoke('wiki:globalReviewStatus')
        if (cancelled) return
        const waiting: any[] = result?.waiting_reviews || []
        onWaitingReviewCountChange(waiting.length)
        const activeCompiles: any[] = result?.active_compiles || []
        onActiveIngestChange(activeCompiles.length > 0)

        // Detect NEW waiting reviews (not seen before) and show toast
        const currentIds = new Set(waiting.map((w: any) => w.project_id))
        for (const w of waiting) {
          if (!prevProjectIdsRef.current.has(w.project_id)) {
            showToast(
              t('project.wiki.reviewWaiting', {
                defaultValue: `「${w.project_name}」知识库编译等待审阅`,
                projectName: w.project_name,
              }),
              'warning',
              {
                action: {
                  label: t('project.wiki.goToReview', { defaultValue: '前往查看' }),
                  onClick: () => {
                    // Dispatch custom event to navigate to project wiki tab
                    window.dispatchEvent(
                      new CustomEvent('app:navigate-wiki', { detail: { projectId: w.project_id } })
                    )
                  },
                },
              },
            )
          }
        }
        prevProjectIdsRef.current = currentIds
      } catch {
        // Backend not ready yet
      }
    }
    check()
    const timer = setInterval(check, 5000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [showToast, t, onWaitingReviewCountChange])

  return null
}

export default function App() {
  const { t } = useTranslation()
  const { settings, handleSaveSettings } = useSettings()
  const sidebar = useResizeHandle(260, 200, 480)

  // ── Task state ──
  const {
    tasks, archivedTasks, currentTasks, activeTab, setActiveTab,
    activeTaskId, setActiveTaskId, loadTasks, loading: tasksLoading,
    creating: taskCreating,
    handleCreateTask, handleCreateTaskWithDir, handleDeleteTask, handleRenameTask,
    handleArchiveTask, handleUnarchiveTask, handleExportTask, handleSetTaskMode,
  } = useTasks(t('task.defaultTitle'))

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
  const [waitingReviewCount, setWaitingReviewCount] = useState(0)
  const [hasActiveIngest, setHasActiveIngest] = useState(false)

  // ── Knowledge base state ──
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [selectedBaseLibId, setSelectedBaseLibId] = useState<string | null>(null)
  const [showTemplateManagement, setShowTemplateManagement] = useState(false)

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

  // Refresh projects when navigating to projects tab
  useEffect(() => {
    if (activeNav === 'projects') {
      loadProjects()
    }
  }, [activeNav, loadProjects])

  // Auto-select first project when navigating to projects tab (so KnowledgePage features are immediately accessible)
  useEffect(() => {
    if (activeNav === 'projects' && projects.length > 0 && !selectedProjectId && !selectedBaseLibId) {
      setSelectedProjectId(projects[0].id)
    }
  }, [activeNav, projects, selectedProjectId, selectedBaseLibId])

  // Poll task status only when on tasks tab or a task is generating
  useEffect(() => {
    const shouldPoll = activeNav === 'tasks' || taskIsGenerating
    if (!shouldPoll) {
      setCanStartTask(true)
      setRunningTaskIds(new Set())
      return
    }
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
  }, [activeNav, taskIsGenerating])

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
    if (!confirm(t('knowledge.confirmDeleteProject'))) return
    try {
      await window.electronAPI.invoke('knowledge:deleteProject', projectId)
      await loadProjects()
      if (selectedProjectId === projectId) setSelectedProjectId(null)
    } catch (err) {
      console.error('Failed to delete project:', err)
    }
  }

  const handleCreateProject = async (name: string, description?: string, type?: ProjectType, companyName?: string, creditCode?: string) => {
    const projectType = type || 'general'
    const stage = getInitialStage(projectType)
    try {
      const result: any = await window.electronAPI.invoke('knowledge:createProject', {
        name,
        description,
        type: projectType,
        stage,
        companyName,
        creditCode,
      })
      await loadProjects()
      // Auto-select the newly created project
      if (result?.id) {
        setSelectedProjectId(result.id)
      }
    } catch (err) {
      console.error('Failed to create project:', err)
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

  // Listen for navigate-wiki event (from WikiReviewMonitor toast action)
  useEffect(() => {
    const handler = (e: Event) => {
      const { projectId } = (e as CustomEvent).detail
      setSelectedBaseLibId(null)
      setSelectedProjectId(projectId)
      setActiveNav('projects')
    }
    window.addEventListener('app:navigate-wiki', handler)
    return () => window.removeEventListener('app:navigate-wiki', handler)
  }, [])

  // Listen for wiki immersive mode — auto-hide app sidebar to maximize content
  const prevSidebarBeforeImmersive = useRef<boolean | null>(null)
  const sidebarCollapsedRef = useRef(sidebarCollapsed)
  sidebarCollapsedRef.current = sidebarCollapsed
  useEffect(() => {
    const handler = (e: Event) => {
      const { expanded } = (e as CustomEvent).detail
      if (expanded) {
        prevSidebarBeforeImmersive.current = sidebarCollapsedRef.current
        setSidebarCollapsed(true)
      } else {
        if (prevSidebarBeforeImmersive.current !== null) {
          setSidebarCollapsed(prevSidebarBeforeImmersive.current)
          prevSidebarBeforeImmersive.current = null
        }
      }
    }
    window.addEventListener('app:wiki-immersive', handler)
    return () => window.removeEventListener('app:wiki-immersive', handler)
  }, [])

  const handleNewTaskWithDir = async () => {
    const result = await window.electronAPI.invoke('tasks:selectDirectory') as { canceled?: boolean; path?: string }
    if (result.canceled || !result.path) return
    const confirmed = window.confirm(t('task.confirmWorkspace', { path: result.path }))
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
    <ToastProvider>
      <WikiReviewMonitor onWaitingReviewCountChange={setWaitingReviewCount} onActiveIngestChange={setHasActiveIngest} />
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
              waitingReviewCount={waitingReviewCount}
              hasActiveIngest={hasActiveIngest}
              footer={
                <BaseLibFooter
                  selectedBaseLib={selectedBaseLibId}
                  onSelectBaseLib={handleBaseLibSelect}
                  onOpenTemplateManagement={() => setShowTemplateManagement(true)}
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
        ) : showTemplateManagement ? (
          <TemplateManagement onBack={() => setShowTemplateManagement(false)} />
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
          <span>{t('app.processExit', { code: processExitCode })}</span>
          <button onClick={() => setSettingsOpen(true)}>{t('menu.viewLogs')}</button>
          <button onClick={() => setProcessExitCode(null)}>{t('window.close')}</button>
        </div>
      )}
    </ToastProvider>
  )
}
