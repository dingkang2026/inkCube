import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
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
import { AntivirusBanner } from './features/task/AntivirusBanner'
import { TaskList } from './features/task/TaskList'
import { useTasks } from './features/task/useTasks'
import { useTaskChat } from './features/task/useTaskChat'
import { TaskStatusMonitor } from './features/task/TaskStatusMonitor'
import { useSettings } from './features/settings/useSettings'
import { OnboardingWizard } from './features/onboarding/OnboardingWizard'
import { useResizeHandle } from './hooks/useResizeHandle'
import { useToast } from './features/knowledge/Toast'
import { ActivityProvider } from './features/activity/ActivityContext'
import { ActivityMonitor } from './features/activity/ActivityMonitor'
import { useConfirm } from './components/ConfirmDialog'
import type { Project } from './features/knowledge/types'
import type { ProjectType } from './features/project/types'
import { getInitialStage } from './features/project/types'
import { MODELS_IPC } from './types'
import { summarizeDueByProject } from './features/project/affairs/types'
import type { DueTask } from './features/project/affairs/types'

export default function App() {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()
  const { settings, settingsLoaded, loadFailed, handleSaveSettings, reloadSettings } = useSettings()
  const sidebar = useResizeHandle(260, 200, 480)

  // ── Task state ──
  const {
    tasks, archivedTasks, currentTasks, activeTab, setActiveTab,
    activeTaskId, setActiveTaskId, loadTasks, loadMore: loadMoreTasks,
    hasMore: tasksHasMore, loading: tasksLoading,
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
    loadMessages: loadTaskMessages,
  } = useTaskChat(activeTaskId, useCallback(() => loadTasks(), []))

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  /** 设置页锚点直达信号（0.4 §7.4）：section + 时间戳，重复导航也生效 */
  const [settingsSectionSignal, setSettingsSectionSignal] = useState<{ name: string; ts: number } | null>(null)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [logViewerOpen, setLogViewerOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('home')
  const [processExitCode, setProcessExitCode] = useState<number | null>(null)
  const [canStartTask, setCanStartTask] = useState(true)
  const [runningTaskIds, setRunningTaskIds] = useState<Set<string>>(new Set())
  /** 通知导航到项目 Tab 的信号（tab + 时间戳，ProjectDetailPage 消费后切换 Tab） */
  // taskId 可选:首页待办行点击携带 → 事务 Tab + 自动打开执行抽屉(3.0 §6.1.8)
  const [projectTabSignal, setProjectTabSignal] = useState<{ tab: string; ts: number; taskId?: string } | null>(null)

  // ── First-run onboarding (0.4 §7.3): 一次性判定所需状态 ──
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [onboardingDone, setOnboardingDone] = useState<boolean | null>(null)
  const [anyModelDownloaded, setAnyModelDownloaded] = useState<boolean | null>(null)
  const onboardingCheckedRef = useRef(false)
  /** 向导挂起（§7.3 ①②流程内步骤）：「去配置 / 去下载」跳设置期间记录恢复步骤；
   *  null = 无挂起。设置页关闭后恢复向导并推进（去配置→②，去下载→③），保证引导不断开 */
  const [onboardingResumeStep, setOnboardingResumeStep] = useState<number | null>(null)

  // ── Knowledge base state ──
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)
  const [selectedBaseLibId, setSelectedBaseLibId] = useState<string | null>(null)
  const [showTemplateManagement, setShowTemplateManagement] = useState(false)

  // ── 3.0 三期：首页今日工作台 + 侧栏色点单一数据快照（§7.2）──
  const [dueData, setDueData] = useState<{ tasks: DueTask[]; reviews_counts: Record<string, number> } | null>(null)
  const dueReqRef = useRef(0)

  const loadDueSummary = useCallback(async () => {
    const seq = ++dueReqRef.current
    try {
      const result: any = await window.electronAPI.invoke('affairs:listDue')
      if (seq !== dueReqRef.current) return  // 已有更新的请求在途，丢弃陈旧响应（同 AffairsTab latestPidRef 惯例，CodeReview W1）
      setDueData({ tasks: result?.tasks ?? [], reviews_counts: result?.reviews_counts ?? {} })
    } catch {
      // 后端未就绪/失败：待办与日历隐藏、色点不渲染——展示层对 null 已降级
      if (seq === dueReqRef.current) setDueData(null)
    }
  }, [])

  // 打开时拉取 + 导航边沿重拉（侧栏色点常驻，任何 nav 切换都可能改变项目数据）；非轮询（0.4 §3.1）
  useEffect(() => { void loadDueSummary() }, [activeNav, loadDueSummary])

  // 任务写操作边沿（AffairsTab 写操作 / 首页打勾后 dispatch）
  useEffect(() => {
    const h = () => { void loadDueSummary() }
    window.addEventListener('app:tasks-changed', h)
    return () => window.removeEventListener('app:tasks-changed', h)
  }, [loadDueSummary])

  const healthByProject = useMemo(
    () => summarizeDueByProject(dueData?.tasks ?? [], dueData?.reviews_counts),
    [dueData],
  )

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

  // One-shot data for first-run detection (onboarding flag + any downloaded internal model)
  useEffect(() => {
    void window.electronAPI.invoke('system:getOnboarding')
      .then((res) => setOnboardingDone(!!(res as { done?: boolean }).done))
      .catch(() => setOnboardingDone(false))
    void window.electronAPI.invoke(MODELS_IPC.status)
      .then((res) => {
        const models = Object.values(res as Record<string, { downloaded?: boolean }>)
        setAnyModelDownloaded(models.some((m) => !!m.downloaded))
      })
      .catch(() => setAnyModelDownloaded(false))
  }, [])

  // First-run onboarding wizard (0.4 §7.3): fire once after all data sources settle —
  // new user = no api_key, no downloaded internal model, no projects, no tasks
  useEffect(() => {
    if (onboardingCheckedRef.current) return
    if (!settingsLoaded || tasksLoading) return
    if (onboardingDone === null || anyModelDownloaded === null) return
    onboardingCheckedRef.current = true
    const isNewUser =
      !onboardingDone &&
      !settings.api_key &&
      !anyModelDownloaded &&
      projects.length === 0 &&
      currentTasks.length === 0
    if (isNewUser) setShowOnboarding(true)
  }, [settingsLoaded, tasksLoading, onboardingDone, anyModelDownloaded, settings.api_key, projects, currentTasks])

  // 向导挂起恢复（0.4 §7.3）：设置页关闭（任意路径：NavTabs 切换 / Ctrl+1/2/3）→ 回到向导下一步，
  // 引导流程 ①→②→③ 不因去配置 / 去下载断开；resumeStep 保留至 onFinish 才清，供 initialStep 传值
  useEffect(() => {
    if (!settingsOpen && onboardingResumeStep !== null) {
      setShowOnboarding(true)
    }
  }, [settingsOpen, onboardingResumeStep])

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

  // Task status polling（#2，v3.9 顺序 5 收编 0.4 §8.1）：谓词改全局心跳派生 + 轮询本体
  // 在 TaskStatusMonitor（App 自身渲染 Provider，useActivity 不可用）；App 只持有
  // 派生结果供 TaskList 消费，非轮询期重置 / 终态补拉均由组件内处理
  const handleTaskStatus = useCallback((status: { can_start: boolean; running_task_ids?: string[] }) => {
    setCanStartTask(status.can_start)
    setRunningTaskIds(new Set(status.running_task_ids || []))
  }, [])

  const selectedProject = selectedProjectId
    ? projects.find((p) => p.id === selectedProjectId) || null
    : null

  const handleProjectSelect = (projectId: string) => {
    setSettingsOpen(false)
    setShowTemplateManagement(false)
    setSelectedBaseLibId(null)
    setSelectedProjectId((prev) => (prev === projectId ? null : projectId))
  }

  const handleBaseLibSelect = (baseLibId: string) => {
    setSettingsOpen(false)
    setShowTemplateManagement(false)
    setSelectedProjectId(null)
    setSelectedBaseLibId((prev) => (prev === baseLibId ? null : baseLibId))
  }

  const handleDeleteProject = async (projectId: string) => {
    if (!(await confirmDialog({ title: t('knowledge.deleteProject'), message: t('knowledge.confirmDeleteProject') }))) return
    try {
      await window.electronAPI.invoke('knowledge:deleteProject', projectId)
      await loadProjects()
      if (selectedProjectId === projectId) setSelectedProjectId(null)
    } catch (err) {
      console.error('Failed to delete project:', err)
      showToast(t('knowledge.deleteProjectFailed'), 'error')
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
      return result as { id: string; seeded_tasks_count?: number } | undefined   // 裁决 8:回传向导确认态
    } catch (err) {
      console.error('Failed to create project:', err)
      showToast(t('knowledge.createProjectFailed'), 'error')
      return undefined
    }
  }

  const handleRenameProject = async (projectId: string, name: string) => {
    try {
      await window.electronAPI.invoke('knowledge:renameProject', { projectId, name })
      await loadProjects()
    } catch (err) {
      console.error('Failed to rename project:', err)
      showToast(t('knowledge.renameProjectFailed'), 'error')
    }
  }

  // 同级页面（设置 / 模板管理 / 基础库详情）打开期间，侧边栏不残留项目 / 任务列表——
  // 与首页同样清空；切回导航时 handleNavChange 清掉三个 peer 状态后列表复原
  const peerPageOpen = settingsOpen || showTemplateManagement || !!selectedBaseLibId

  const handleNavChange = useCallback((nav: string) => {
    setActiveNav(nav)
    // Peer pages: switching nav exits settings / template management / base lib detail
    setSettingsOpen(false)
    setShowTemplateManagement(false)
    setSelectedBaseLibId(null)
  }, [])

  // Settings is a peer page too: opening it exits template management / base lib detail
  // section 可选：设置页锚点直达（0.4 §7.4，首页红卡 / 引导弹层 / 向导）
  const handleOpenSettings = useCallback((section?: string) => {
    setShowTemplateManagement(false)
    setSelectedBaseLibId(null)
    setSettingsSectionSignal(section ? { name: section, ts: Date.now() } : null)
    setSettingsOpen(true)
  }, [])

  // 向导流程内步骤「去配置 / 去下载」（0.4 §7.3 ①②）：挂起向导（不写 onboarding_done）
  // 跳设置锚点，恢复由下方 settingsOpen 关闭 effect 接管
  const handleOnboardingSuspend = useCallback(
    (section: 'externalModel' | 'internalModel') => {
      setOnboardingResumeStep(section === 'externalModel' ? 2 : 3)
      setShowOnboarding(false)
      handleOpenSettings(section)
    },
    [handleOpenSettings],
  )

  // Listen for navigate-project-tab (Activity Hub toast/home feed action) and navigate-tasks
  useEffect(() => {
    const tabHandler = (e: Event) => {
      const { projectId, tab, container, taskId } = (e as CustomEvent).detail
      setSettingsOpen(false)
      setShowTemplateManagement(false)
      // 基础库容器（0.4 §3.6 导航分流）：projectId 即 baseLibId，与项目详情互斥；
      // tab 信号只对项目详情页生效，基础库详情内嵌素材区即 file 活动目标位置
      if (container === 'baselib') {
        setSelectedProjectId(null)
        setSelectedBaseLibId(projectId)
      } else {
        setSelectedBaseLibId(null)
        setSelectedProjectId(projectId)
      }
      setActiveNav('projects')
      if (tab) setProjectTabSignal({ tab, ts: Date.now(), taskId })
    }
    const tasksHandler = () => handleNavChange('tasks')
    window.addEventListener('app:navigate-project-tab', tabHandler)
    window.addEventListener('app:navigate-tasks', tasksHandler)
    return () => {
      window.removeEventListener('app:navigate-project-tab', tabHandler)
      window.removeEventListener('app:navigate-tasks', tasksHandler)
    }
  }, [handleNavChange])

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
    const confirmed = await confirmDialog({ message: t('task.confirmWorkspace', { path: result.path }), danger: false })
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
      handleOpenSettings()
    })
    const offViewLogs = window.electronAPI.on('menu:view-logs', () => {
      setLogViewerOpen(true)
    })
    const offExportLogs = window.electronAPI.on('menu:export-logs', () => {
      window.electronAPI.invoke('system:exportLogs')
    })
    // Listen for navigate-settings from useErrorHandler / HealthStatus / readiness guard
    // (detail.section = 设置页锚点，0.4 §7.2 action 编码)
    const handleNavSettings = (e: Event) => {
      const { section } = (e as CustomEvent).detail ?? {}
      handleOpenSettings(section)
    }
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
  }, [handleOpenSettings])

  return (
    <ActivityProvider>
      {/* 单一常驻活动轮询：Toast / 徽章 / 首页活动区共同数据源（0.4 §8.1 #0） */}
      <ActivityMonitor />
      {/* 任务系统状态轮询（#2，顺序 5 收编）：谓词全局心跳派生，须挂 Provider 内 */}
      <TaskStatusMonitor
        activeOnTasks={activeNav === 'tasks'}
        localGenerating={taskIsGenerating}
        onStatus={handleTaskStatus}
      />
      <TitleBar
        onOpenSettings={handleOpenSettings}
        onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
        onShowAbout={() => setAboutOpen(true)}
      />
      {/* 白名单授权横幅（2.8 §12 F4）：全局停机状态全局可见——未验证加白前
          Python 侧阻断一切依赖安装，不应只提示任务页用户（原挂 TaskChatView
          的缺陷：停在首页/项目页/设置页时完全无感知）。#root 纵向 flex，
          此处自然成为 TitleBar 之下的全宽顶条 */}
      <AntivirusBanner />
      <div className="app-layout">
        {/* Sidebar */}
        {!sidebarCollapsed && (
          <>
            <Sidebar
              width={sidebar.width}
              activeNav={!peerPageOpen ? activeNav : ''}
              onNavChange={handleNavChange}
              onOpenSettings={handleOpenSettings}
              footer={
                <BaseLibFooter
                  selectedBaseLib={selectedBaseLibId}
                  onSelectBaseLib={handleBaseLibSelect}
                  templateManagementActive={showTemplateManagement}
                  onOpenTemplateManagement={() => {
                    setSettingsOpen(false)
                    setSelectedBaseLibId(null)
                    setShowTemplateManagement(true)
                  }}
                />
              }
            >
              {!peerPageOpen && activeNav === 'projects' && (
                <ProjectSidebar
                  projects={projects}
                  selectedProject={selectedProjectId}
                  onSelectProject={handleProjectSelect}
                  onDeleteProject={handleDeleteProject}
                  onRenameProject={handleRenameProject}
                  onCreateProject={handleCreateProject}
                  healthData={healthByProject}
                />
              )}
              {!peerPageOpen && activeNav === 'tasks' && (
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
                  hasMore={activeTab === 'active' ? tasksHasMore.active : tasksHasMore.archived}
                  onLoadMore={() => loadMoreTasks(activeTab)}
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
            loadFailed={loadFailed}
            onRetryLoad={reloadSettings}
            sectionSignal={settingsSectionSignal}
          />
        ) : showTemplateManagement ? (
          <TemplateManagement />
        ) : selectedBaseLibId ? (
          /* Base library is parallel to projects — accessible from any nav */
          <BaseLibDetailPage baseLibId={selectedBaseLibId} />
        ) : activeNav === 'home' ? (
          <HomePage
            projects={projects}
            onNavigate={handleNavChange}
            onOpenSettings={handleOpenSettings}
            onSelectProject={(id) => {
              setSelectedProjectId(id)
            }}
            dueData={dueData}
            healthByProject={healthByProject}
          />
        ) : activeNav === 'projects' ? (
          selectedProject ? (
            <ProjectDetailPage
              project={selectedProject}
              tabSignal={projectTabSignal}
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
            onFilesUploaded={() => {
              loadTasks()
              // 上传成功后立即刷新消息列表：后端写入的「[系统] 用户上传了文件」马上可见
              if (activeTaskId) loadTaskMessages(activeTaskId)
            }}
            onSwitchMode={(mode) => activeTaskId && handleSetTaskMode(activeTaskId, mode)}
          />
        ) : (
          <div />
        )}
      </div>
      <AboutDialog open={aboutOpen} onClose={() => setAboutOpen(false)} />
      {showOnboarding && (
        <OnboardingWizard
          onFinish={() => {
            setShowOnboarding(false)
            setOnboardingResumeStep(null)
          }}
          onSuspend={handleOnboardingSuspend}
          initialStep={onboardingResumeStep ?? 0}
        />
      )}
      {logViewerOpen && <LogViewer onClose={() => setLogViewerOpen(false)} />}
      {processExitCode !== null && (
        <div className="process-exit-banner">
          <span>{t('app.processExit', { code: processExitCode })}</span>
          <button onClick={() => handleOpenSettings()}>{t('menu.viewLogs')}</button>
          <button onClick={() => setProcessExitCode(null)}>{t('window.close')}</button>
        </div>
      )}
    </ActivityProvider>
  )
}
