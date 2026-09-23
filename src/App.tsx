import { useState, useCallback, useEffect } from 'react'
import { TitleBar } from './components/layout/TitleBar'
import { AboutDialog } from './components/layout/AboutDialog'
import { Sidebar } from './components/layout/Sidebar'
import { ResizeHandle } from './components/layout/ResizeHandle'
import { ConversationList } from './features/chat/ConversationList'
import { ChatView } from './features/chat/ChatView'
import { SettingsPage } from './features/settings/SettingsPage'
import { LogViewer } from './features/settings/LogViewer'
import { KnowledgePage } from './features/knowledge/KnowledgePage'
import { ProjectList } from './features/knowledge/ProjectList'
import { HomePage } from './features/home/HomePage'
import { TaskPage } from './features/task/TaskPage'
import { TaskList } from './features/task/TaskList'
import { useConversations } from './features/chat/useConversations'
import { useChat } from './features/chat/useChat'
import { useTasks } from './features/task/useTasks'
import { useTaskChat } from './features/task/useTaskChat'
import { useSettings } from './features/settings/useSettings'
import { useResizeHandle } from './hooks/useResizeHandle'
import type { Project } from './features/knowledge/types'

export default function App() {
  const {
    conversations, activeId, setActiveId, loadConversations,
    handleNewChat, handleDeleteConversation, handleRenameConversation,
  } = useConversations()

  const {
    messages, isGenerating, streamContent, streamSteps,
    handleSendMessage, handleStopGenerate,
  } = useChat(activeId, useCallback(() => loadConversations(), []))

  const { settings, handleSaveSettings } = useSettings()
  const sidebar = useResizeHandle(260, 200, 480)

  // ── Task state ──
  const {
    tasks, archivedTasks, currentTasks, activeTab, setActiveTab,
    activeTaskId, setActiveTaskId, loadTasks, loading: tasksLoading,
    handleCreateTask, handleCreateTaskWithDir, handleDeleteTask, handleRenameTask,
    handleArchiveTask, handleUnarchiveTask, handleExportTask, handleSetTaskMode,
  } = useTasks()

  const {
    messages: taskMessages,
    isGenerating: taskIsGenerating,
    streamContent: taskStreamContent,
    streamSteps: taskStreamSteps,
    streamIsThought: taskStreamIsThought,
    planData: taskPlanData,
    pendingShellConfirm: taskPendingShellConfirm,
    pendingSkillConfirm: taskPendingSkillConfirm,
    handleSendMessage: handleTaskSendMessage,
    handleStopGenerate: handleTaskStopGenerate,
    handlePauseTask: handleTaskPause,
    handleShellConfirm: handleTaskShellConfirm,
    handleSkillConfirm: handleTaskSkillConfirm,
  } = useTaskChat(activeTaskId, useCallback(() => loadTasks(), []))

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [logViewerOpen, setLogViewerOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('home')
  const [processExitCode, setProcessExitCode] = useState<number | null>(null)
  const [canStartTask, setCanStartTask] = useState(true)

  // ── Knowledge base state ──
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)

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

  // Poll task status for concurrent limit
  useEffect(() => {
    if (activeNav !== 'tasks') return
    let cancelled = false
    const poll = async () => {
      try {
        const status = await window.electronAPI.invoke('tasks:getStatus') as { can_start: boolean }
        if (!cancelled) setCanStartTask(status.can_start)
      } catch { /* ignore */ }
    }
    poll()
    const timer = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [activeNav])

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
    setSelectedProjectId((prev) => (prev === projectId ? null : projectId))
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

  const handleCreateProject = async (name: string, description?: string) => {
    const result: any = await window.electronAPI.invoke('knowledge:createProject', { name, description })
    await loadProjects()
    // Auto-select the newly created project
    if (result?.id) {
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

  const handleNavChange = (nav: string) => {
    setActiveNav(nav)
    // Auto-close settings when switching tabs
    if (settingsOpen) setSettingsOpen(false)
  }

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

  // ── Keyboard shortcuts: Ctrl+1~5 for navigation ──
  useEffect(() => {
    const NAV_KEYS: Record<string, string> = {
      '1': 'home', '2': 'insights', '3': 'knowledge', '4': 'tasks', '5': 'reports',
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && !e.shiftKey && !e.altKey && NAV_KEYS[e.key]) {
        e.preventDefault()
        const target = NAV_KEYS[e.key]
        if (target === 'reports') return
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
            >
              {activeNav === 'insights' && (
                <ConversationList
                  conversations={conversations}
                  activeId={activeId}
                  onSelect={setActiveId}
                  onNew={handleNewChat}
                  onDelete={handleDeleteConversation}
                  onRename={handleRenameConversation}
                />
              )}
              {activeNav === 'knowledge' && (
                <ProjectList
                  projects={projects}
                  selectedProject={selectedProjectId}
                  onSelect={handleProjectSelect}
                  onDelete={handleDeleteProject}
                  onRename={handleRenameProject}
                  onCreate={handleCreateProject}
                />
              )}
              {activeNav === 'tasks' && (
                <TaskList
                  tasks={currentTasks}
                  activeTaskId={activeTaskId}
                  activeTab={activeTab}
                  loading={tasksLoading}
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
        ) : activeNav === 'home' ? (
          <HomePage
            conversations={conversations}
            projects={projects}
            onNavigate={handleNavChange}
            onOpenSettings={() => setSettingsOpen(true)}
            onSelectConversation={setActiveId}
            onSelectProject={(id) => {
              setSelectedProjectId(id)
            }}
            onNewChat={handleNewChat}
          />
        ) : activeNav === 'knowledge' ? (
          <KnowledgePage
            selectedProject={selectedProject}
            onOpenSettings={() => setSettingsOpen(true)}
            onProjectDataChanged={loadProjects}
          />
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
            streamIsThought={taskStreamIsThought}
            planData={taskPlanData}
            pendingShellConfirm={taskPendingShellConfirm}
            pendingSkillConfirm={taskPendingSkillConfirm}
            onSendMessage={handleTaskSendMessage}
            onStopGenerate={handleTaskStopGenerate}
            onPauseTask={handleTaskPause}
            onShellConfirm={handleTaskShellConfirm}
            onSkillConfirm={handleTaskSkillConfirm}
            onFilesUploaded={() => loadTasks()}
            onSwitchMode={(mode) => activeTaskId && handleSetTaskMode(activeTaskId, mode)}
          />
        ) : (
          <ChatView
            messages={messages}
            isGenerating={isGenerating}
            streamContent={streamContent}
            streamSteps={streamSteps}
            onSendMessage={handleSendMessage}
            onStopGenerate={handleStopGenerate}
            conversationTitle={conversations.find((c) => c.id === activeId)?.title}
          />
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
