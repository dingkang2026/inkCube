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
import { ProjectWelcomeGuide } from './features/knowledge/ProjectWelcomeGuide'
import { HomePage } from './features/home/HomePage'
import { TaskPage } from './features/task/TaskPage'
import { SystemAlerts } from './components/layout/SystemAlerts'
import { TaskList } from './features/task/TaskList'
import { useTasks } from './features/task/useTasks'
import { useTaskChat } from './features/task/useTaskChat'
import { TaskStatusMonitor } from './features/task/TaskStatusMonitor'
import { ScheduleDrawer, type ScheduleView } from './features/schedule/ScheduleDrawer'
import { useSettings } from './features/settings/useSettings'
import { OnboardingWizard } from './features/onboarding/OnboardingWizard'
import { PersonaPicker, type Persona } from './features/onboarding/PersonaPicker'
import { IntelPage } from './features/intel/IntelPage'
import type { LeadRow } from './features/intel/LeadPool'
import { ScheduleFormModal } from './features/schedule/ScheduleFormModal'
import {
  BUILTIN_PREFIX,
  LEAD_UPGRADE_TPL_ID,
  PROFILE_SUBSCRIBE_TPL_ID,
  uniquePlaceholderNames,
} from './features/task/promptTemplate'
import { navAfterOnboarding } from './features/onboarding/finishNav'
import { useResizeHandle } from './hooks/useResizeHandle'
import { useExplicitDeselect } from './hooks/useExplicitDeselect'
import { useToast } from './features/knowledge/Toast'
import { BrowserPanel } from './features/browser/BrowserPanel'
import { ActivityProvider } from './features/activity/ActivityContext'
import { ActivityMonitor, navigateToNotification } from './features/activity/ActivityMonitor'
import { NotificationCenter } from './features/activity/NotificationCenter'
import type { NotificationItem } from './features/activity/types'
import { StatusBar } from './features/usage/StatusBar'
import { useConfirm } from './components/ConfirmDialog'
import { apiErrorText } from './i18n/errorCodes'
import type { Project } from './features/knowledge/types'
import type { ProjectType } from './features/project/types'
import { getInitialStage } from './features/project/types'
import { MODELS_IPC } from './types'
import { summarizeDueByProject, type ReviewTypedCounts } from './features/project/affairs/types'
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
    dismissPendingConfirm: dismissTaskConfirm,
    handleResumeTask: handleTaskResume,
    canResume: taskCanResume,
    resetGeneratingState: resetTaskGeneratingState,
    loadMessages: loadTaskMessages,
  } = useTaskChat(activeTaskId, useCallback(() => loadTasks(), []))

  /** 任务句柄统一获取（0.5 §4.4「发送/上传即建任务」）：有选中任务直接返回 id；
   *  零任务/未选中先创建任务（默认工作区）返回新 id；创建在途 toast 反馈返回 null
   *  （创建失败 useTasks 已 toast，同样 null），聊天发送与文件上传共用 */
  const ensureTask = useCallback(async (): Promise<string | null> => {
    if (activeTaskId) return activeTaskId
    // 创建在途（侧栏「新建」进行中）：handleCreateTask 防重静默返回 undefined，
    // 显式反馈避免欢迎页操作被无声吞掉（review Low-2）
    if (taskCreating) {
      showToast(t('task.createInProgress'), 'info')
      return null
    }
    return (await handleCreateTask()) ?? null
  }, [activeTaskId, taskCreating, showToast, t, handleCreateTask])

  /** TaskPage 聊天发送统一入口（0.5 §4.4「发送即建任务」，2026-10-19 裁决）：
   *  有选中任务直接发送；零任务/未选中先建任务再以新 id 发出首条，
   *  达成 §1.5 #6「2 步发起第一个模板任务」。失败（含配额 409）throw 令
   *  TaskChatView 保留输入并复位发送锁供直接重试；
   *  tasks:chat 的 isGenerating 生命周期归 useTaskChat，此处不 await */
  const handleTaskChatSend = useCallback(async (content: string) => {
    if (activeTaskId) {
      handleTaskSendMessage(content)
      return
    }
    const newId = await ensureTask()
    if (!newId) throw new Error('create task failed')
    handleTaskSendMessage(content, newId)
  }, [activeTaskId, ensureTask, handleTaskSendMessage])

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  /** 设置页锚点直达信号（0.4 §7.4）：section + 时间戳，重复导航也生效；
   *  projectId 可选（2.10 §6.5 线框 5：项目徽章点击 → 统计页自动筛选该项目） */
  const [settingsSectionSignal, setSettingsSectionSignal] = useState<{ name: string; ts: number; projectId?: string } | null>(null)
  const [aboutOpen, setAboutOpen] = useState(false)
  /** 通知中心开合（2.11 §6.1）：TitleBar 铃铛 / 首页「查看全部」→ open */
  const [notifCenterOpen, setNotifCenterOpen] = useState(false)
  const [logViewerOpen, setLogViewerOpen] = useState(false)
  const [activeNav, setActiveNav] = useState('home')
  const [processExitCode, setProcessExitCode] = useState<number | null>(null)
  const [canStartTask, setCanStartTask] = useState(true)
  const [runningTaskIds, setRunningTaskIds] = useState<Set<string>>(new Set())
  /** 通知导航到项目 Tab 的信号（tab + 时间戳，ProjectDetailPage 消费后切换 Tab） */
  // taskId 可选:首页待办行点击携带 → 事务 Tab + 自动打开执行抽屉(3.0 §6.1.8)
  const [projectTabSignal, setProjectTabSignal] = useState<{ tab: string; ts: number; taskId?: string; wikiFocus?: 'lint' | 'compile' } | null>(null)
  // 0.5 E1 T4:首页「新建项目」信号(纯前端):切到项目导航 + ProjectList 自动打开创建模态
  const [projectCreateSignal, setProjectCreateSignal] = useState(0)

  // ── 2.11 E3 T7 → 0.6 V2-E1a → 第二步三态统一抽屉（2026-10-25）：
  // 阅读 / 回放 / 档案为 App 级右侧遮罩抽屉（ScheduleDrawer），宿主页不卸载——
  // 原 from 来路字段与 scheduleId 返回恢复锚随整页 peer 态退役（0.6 §7 删除链）
  const [scheduleView, setScheduleView] = useState<ScheduleView | null>(null)

  // 情报中心（0.6 §3.2 V2-E1a/E2；两 Tab 收敛 2026-10-25）：默认订阅 tab
  // （收件箱即跨订阅期次消费面）；期次抽屉打开期间管理台不卸载，tab 与
  // 订阅选中天然保持（原 intelRestoreId 恢复锚随整页 peer 态退役）
  const [intelTab, setIntelTab] = useState<'admin' | 'leads'>('admin')

  // 订阅预填（0.6 §6.1 线索升级 + 2.11 §8.1 项目画像盯产业共用一条 App 级
  // 弹层链）：非 null 时打开新建订阅弹层（复用 schedules:create，零新通道）；
  // 保存/关闭后清空
  const [schedulePrefill, setSchedulePrefill] = useState<{
    name: string; tplId: string; phValues: Record<string, string>
  } | null>(null)

  // ── First-run onboarding (0.4 §7.3): 一次性判定所需状态 ──
  const [showOnboarding, setShowOnboarding] = useState(false)
  // 首启身份选择层（0.6 §3.8 V2-E1a）：新老用户统一判定（persona_prompted）
  const [showPersona, setShowPersona] = useState(false)
  const personaCheckedRef = useRef(false)
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
  // 模板管理页外部指定初始分区（2.3 §4.2）：通知/首页点击 PPT 模板任务 → 直落 PPT模板
  // Tab；null = 无外部指定（侧栏入口），页内默认报告模板分区
  const [templateManagementSection, setTemplateManagementSection] = useState<'reports' | 'forms' | 'ppt' | null>(null)
  // ── 项目归档（2.1 §归档）：侧栏 有效/归档 双 tab；归档列表懒加载（null = 未拉取）──
  const [projectListTab, setProjectListTab] = useState<'active' | 'archived'>('active')
  const [archivedProjects, setArchivedProjects] = useState<Project[] | null>(null)
  const [archivedCount, setArchivedCount] = useState(0)
  // 列表分页（0.1dev-guide 统一约定）：50/页「加载更多」双游标（active/archived 各自
  // limit 递增全量重拉，useTasks 双 tab 模式）；archived_count 随 active 请求返回（徽章
  // 数据源，与列表分页无关），故不复用 archived total；projectsTotal 供首页概览计数
  const PROJECTS_PAGE_SIZE = 50
  const activeLimitRef = useRef(PROJECTS_PAGE_SIZE)
  const archivedLimitRef = useRef(PROJECTS_PAGE_SIZE)
  const [projectsHasMore, setProjectsHasMore] = useState(false)
  const [projectsLoadingMore, setProjectsLoadingMore] = useState(false)
  const [projectsTotal, setProjectsTotal] = useState<number | null>(null)
  const [archivedHasMore, setArchivedHasMore] = useState(false)
  const [archivedLoadingMore, setArchivedLoadingMore] = useState(false)

  // ── 3.0 三期：首页今日工作台 + 侧栏色点单一数据快照（§7.2）──
  const [dueData, setDueData] = useState<{ tasks: DueTask[]; reviews_counts: ReviewTypedCounts } | null>(null)
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

  // 列表重拉代际守卫（同 dueReqRef 惯例，CodeReview Minor-4）：归档/恢复动作与
  // tab 切换懒加载并发时，乱序 resolve 的陈旧响应不得覆盖新列表
  const projectsReqRef = useRef(0)
  const archivedReqRef = useRef(0)
  const loadProjects = useCallback(async () => {
    const seq = ++projectsReqRef.current
    try {
      const result: any = await window.electronAPI.invoke('knowledge:listProjects', { limit: activeLimitRef.current })
      if (seq !== projectsReqRef.current) return undefined
      const list: Project[] = result.projects || []
      setProjects(list)
      setArchivedCount(result.archived_count ?? 0)
      setProjectsHasMore(!!result.has_more)
      setProjectsTotal(typeof result.total === 'number' ? result.total : null)
      return list   // 调用方校验项目确已入库（导入完成条件选中，Review m2）
    } catch (err) {
      console.error('Failed to load projects:', err)
      return undefined
    }
  }, [])

  useEffect(() => {
    loadProjects()
  }, [loadProjects])

  // 归档列表懒加载：首次切到归档 tab 拉取；归档/恢复/删除后随 handlers 重拉
  const loadArchivedProjects = useCallback(async () => {
    const seq = ++archivedReqRef.current
    try {
      const result: any = await window.electronAPI.invoke('knowledge:listProjects', {
        archived: true, limit: archivedLimitRef.current,
      })
      if (seq !== archivedReqRef.current) return undefined
      const list: Project[] = result.projects || []
      setArchivedProjects(list)
      setArchivedHasMore(!!result.has_more)
      return list   // 调用方校验（线索跳项目补拉后终判，同 loadProjects 模式）
    } catch (err) {
      console.error('Failed to load archived projects:', err)
      return undefined
    }
  }, [])

  // 加载更多（双 tab 各自游标，useTasks 双 tab 模式）：按当前 tab 分派由消费方完成
  const loadMoreProjects = useCallback(async () => {
    if (!projectsHasMore || projectsLoadingMore) return
    setProjectsLoadingMore(true)
    try {
      activeLimitRef.current += PROJECTS_PAGE_SIZE
      await loadProjects()
    } finally {
      setProjectsLoadingMore(false)
    }
  }, [projectsHasMore, projectsLoadingMore, loadProjects])

  const loadMoreArchivedProjects = useCallback(async () => {
    if (!archivedHasMore || archivedLoadingMore) return
    setArchivedLoadingMore(true)
    try {
      archivedLimitRef.current += PROJECTS_PAGE_SIZE
      await loadArchivedProjects()
    } finally {
      setArchivedLoadingMore(false)
    }
  }, [archivedHasMore, archivedLoadingMore, loadArchivedProjects])

  const handleProjectTabChange = useCallback((tab: 'active' | 'archived') => {
    setProjectListTab(tab)
    if (tab === 'archived') void loadArchivedProjects()
  }, [loadArchivedProjects])

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

  // 首启身份选择层（0.6 §3.8 V2-E1a）：settingsLoaded 后 !persona_prompted →
  // 显示，新老用户统一（老用户升级安装补身份层）；仅依赖 settings（比向导
  // 判定数据源少，先弹出；与向导同 z 层，DOM 后序在上盖住，选完自然露出）
  useEffect(() => {
    if (personaCheckedRef.current) return
    if (!settingsLoaded) return
    personaCheckedRef.current = true
    if (!settings.persona_prompted) setShowPersona(true)
  }, [settingsLoaded, settings.persona_prompted])

  // 身份选择持久化：选择/跳过都写 persona + persona_prompted=true（一次呈现）
  const handlePersonaSelect = useCallback(async (persona: Persona) => {
    try {
      await handleSaveSettings({ ...settings, persona, persona_prompted: true })
      setShowPersona(false)
    } catch {
      showToast(t('onboarding.persona.saveFailed'), 'error')
    }
  }, [handleSaveSettings, settings, showToast, t])

  const handlePersonaSkip = useCallback(async () => {
    try {
      await handleSaveSettings({ ...settings, persona: '', persona_prompted: true })
      setShowPersona(false)
    } catch {
      showToast(t('onboarding.persona.saveFailed'), 'error')
    }
  }, [handleSaveSettings, settings, showToast, t])

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
  // 0.5 E1 T5:用户显式取消项目后(blocked)不再自动顶回,项目欢迎页可达
  const deselect = useExplicitDeselect()
  useEffect(() => {
    if (activeNav === 'projects' && projects.length > 0 && !selectedProjectId && !selectedBaseLibId && deselect.shouldAutoSelect()) {
      setSelectedProjectId(projects[0].id)
    }
  }, [activeNav, projects, selectedProjectId, selectedBaseLibId, deselect.shouldAutoSelect])

  // Task status polling（#2，v3.9 顺序 5 收编 0.4 §8.1）：谓词改全局心跳派生 + 轮询本体
  // 在 TaskStatusMonitor（App 自身渲染 Provider，useActivity 不可用）；App 只持有
  // 派生结果供 TaskList 消费，非轮询期重置 / 终态补拉均由组件内处理
  const handleTaskStatus = useCallback((status: { can_start: boolean; running_task_ids?: string[] }) => {
    setCanStartTask(status.can_start)
    setRunningTaskIds(new Set(status.running_task_ids || []))
  }, [])

  // 详情页查找兼容归档列表：归档仅是状态层，详情页照常可操作（2.1 §11.6）。归档列表为懒加载，
  // 能在侧栏归档 tab 点中的项目必然已拉取，find 不到即 null，无需额外请求
  const selectedProject = selectedProjectId
    ? projects.find((p) => p.id === selectedProjectId)
      || archivedProjects?.find((p) => p.id === selectedProjectId)
      || null
    : null

  // 离开设置页统一清导航信号：signal 是一次性导航意图（锚点直达/徽章下钻），
  // 残留会让统计页在设置页内切走再切回时复活已被「×」清除的项目筛选（2.10 §6.5）。
  // 定义须在首个使用点（handleProjectSelect/handleNavChange）之前——useCallback deps
  // 渲染时立即求值，const 声明在后会 TDZ ReferenceError
  const closeSettings = useCallback(() => {
    setSettingsOpen(false)
    setSettingsSectionSignal(null)
  }, [])

  const handleProjectSelect = (projectId: string) => {
    closeSettings()
    setShowTemplateManagement(false)
    setSelectedBaseLibId(null)
    if (selectedProjectId === projectId) {
      // 显式取消(点已选项目 toggle→null):守卫自动选中,欢迎页可达(0.5 E1 T5)
      deselect.markDeselected()
      setSelectedProjectId(null)
    } else {
      deselect.reset()
      setSelectedProjectId(projectId)
    }
  }

  const handleBaseLibSelect = (baseLibId: string) => {
    closeSettings()
    setShowTemplateManagement(false)
    setScheduleView(null)
    setSelectedProjectId(null)
    setSelectedBaseLibId((prev) => (prev === baseLibId ? null : baseLibId))
  }

  const handleDeleteProject = async (projectId: string) => {
    if (!(await confirmDialog({ title: t('knowledge.deleteProject'), message: t('knowledge.confirmDeleteProject') }))) return
    try {
      const res: any = await window.electronAPI.invoke('knowledge:deleteProject', projectId)
      // 结构化返回：409 PROJECT_NOT_ARCHIVED（列表陈旧时）经 res.code 映射文案
      // （IPC 抛错跨进程仅存 message，errorCode 丢失——structuredPythonResponse）
      if (!res?.ok) {
        showToast(apiErrorText(res?.code, res?.error || t('knowledge.deleteProjectFailed'), t), 'error')
        return
      }
      await Promise.all([loadProjects(), loadArchivedProjects()])
      if (selectedProjectId === projectId) setSelectedProjectId(null)
    } catch (err) {
      console.error('Failed to delete project:', err)
      showToast(t('knowledge.deleteProjectFailed'), 'error')
    }
  }

  const handleArchiveProject = async (projectId: string) => {
    const target = projects.find((p) => p.id === projectId)
    if (!(await confirmDialog({
      title: t('projectArchive.confirmTitle'),
      message: t('projectArchive.confirmMessage', { name: target?.name ?? '' }),
    }))) return
    try {
      const res: any = await window.electronAPI.invoke('knowledge:archiveProject', projectId)
      // 409 PROJECT_HAS_ACTIVE_WORK 经 errors.codes 映射给出可行动文案（D1 方案 A；
      // IPC 抛错跨进程仅存 message，code 经 structuredPythonResponse 返回值透传）
      if (!res?.ok) {
        showToast(apiErrorText(res?.code, res?.error || t('projectArchive.archiveFailed'), t), 'error')
        return
      }
      await Promise.all([loadProjects(), loadArchivedProjects()])
      // 归档当前选中项：详情页失去有效上下文，清选中回到空态
      if (selectedProjectId === projectId) setSelectedProjectId(null)
    } catch (err) {
      console.error('Failed to archive project:', err)
      showToast(t('projectArchive.archiveFailed'), 'error')
    }
  }

  // 恢复无破坏性，不设确认层
  const handleUnarchiveProject = async (projectId: string) => {
    try {
      const res: any = await window.electronAPI.invoke('knowledge:unarchiveProject', projectId)
      // 409 PROJECT_ALREADY_ACTIVE（双击竞态/列表陈旧）经 res.code 映射文案
      if (!res?.ok) {
        showToast(apiErrorText(res?.code, res?.error || t('projectArchive.unarchiveFailed'), t), 'error')
        return
      }
      await Promise.all([loadProjects(), loadArchivedProjects()])
    } catch (err) {
      console.error('Failed to unarchive project:', err)
      showToast(t('projectArchive.unarchiveFailed'), 'error')
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
      // 新项目进入有效列表：若用户停留在归档 tab，切回有效 tab 保证可见
      setProjectListTab('active')
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

  // ── 项目导出/导入（2.9 §8）：对话框在 Electron 层，这里只消费结果 ──
  const handleExportProject = useCallback(async (projectId: string, projectName: string) => {
    try {
      const res: any = await window.electronAPI.invoke('projects:exportProject', { projectId, projectName })
      if (res?.canceled) return
      // skipped 摘要（2.9 §8.2）：进行中工作不随包，N>0 时提示用户
      const s = res?.skipped ?? {}
      const skippedTotal = (s.files_processing ?? 0) + (s.reports_generating ?? 0) + (s.forms_processing ?? 0)
      showToast(
        skippedTotal > 0
          ? `${t('knowledge.exportDone')} · ${t('knowledge.exportSkipped', { count: skippedTotal })}`
          : t('knowledge.exportDone'),
        'success',
      )
    } catch (err) {
      console.error('Export project failed:', err)
      showToast(t('knowledge.exportFailed'), 'error')
    }
  }, [showToast, t])

  // 导入是后台活动：记住待选中的新项目 id，完成边沿（ProjectSidebar 上报）后刷新并选中（2.9 §8.2）
  const pendingSelectRef = useRef<string | null>(null)
  const importFallbackTimerRef = useRef<number | null>(null)

  // 导入收尾：刷新列表，仅当新项目确已入库才选中（Review m2：activity 快照只含
  // running 态、无终态区分，导入失败边沿同样触发 completed 上报，无条件选中会
  // 命中幽灵 id）；pid 为空（正常边沿已处理过/无导入）时退化为一次纯刷新
  const finalizeImportSelection = useCallback(async () => {
    const pid = pendingSelectRef.current
    pendingSelectRef.current = null
    const list = await loadProjects()
    if (pid && list?.some((p) => p.id === pid)) setSelectedProjectId(pid)
  }, [loadProjects])

  const handleImportProject = useCallback(async () => {
    try {
      const res: any = await window.electronAPI.invoke('projects:importProject')
      if (res?.canceled) return
      pendingSelectRef.current = res.project_id ?? null
      showToast(t('knowledge.importStarted'), 'success')
      // Review m3：5s 心跳是快照式轮询，<5s 的快速导入可能整段落在两次快照
      // 之间（completed 边沿永不触发），10s（2×心跳周期）后无条件收尾兜底；
      // 正常边沿已清 ref 时 finalize 退化为一次列表刷新，无副作用
      if (importFallbackTimerRef.current !== null) {
        window.clearTimeout(importFallbackTimerRef.current)
      }
      importFallbackTimerRef.current = window.setTimeout(() => {
        importFallbackTimerRef.current = null
        void finalizeImportSelection()
      }, 10_000)
    } catch (err) {
      console.error('Import project failed:', err)
      // 校验拒绝等业务失败：透出后端原因（2.9 §8.2「toast 明确原因」）
      const detail = err instanceof Error && err.message ? `: ${err.message}` : ''
      showToast(`${t('knowledge.importFailed')}${detail}`, 'error')
    }
  }, [showToast, t, finalizeImportSelection])

  // 导入完成边沿 → 刷新项目列表并自动选中新项目（后台导入返回时行尚未入库）
  const handleImportCompleted = useCallback(() => {
    void finalizeImportSelection()
  }, [finalizeImportSelection])

  // 同级页面（设置 / 模板管理 / 基础库详情）打开期间，侧边栏不残留项目 / 任务列表
  // ——与首页同样清空；切回导航时 handleNavChange 清掉各 peer 态后复原。
  // 期次抽屉（scheduleView）保留该因子：遮罩盖全屏期间导航不可交互，
  // 维持 sidebar 空载避免遮罩下无谓挂载
  const peerPageOpen = settingsOpen || showTemplateManagement || !!selectedBaseLibId || !!scheduleView

  const handleNavChange = useCallback((nav: string) => {
    setActiveNav(nav)
    deselect.reset()   // 切导航恢复自动选中(0.5 E1 T5)
    // Peer pages: switching nav exits settings / template management / base lib detail
    closeSettings()
    setShowTemplateManagement(false)
    setSelectedBaseLibId(null)
    setScheduleView(null)
  }, [closeSettings, deselect.reset])

  // 情报中心管理台 tab 直达（0.6 §5 V2-E1a：原 admin peer 态入口全部改道）：
  // 通知点击 / 首页快捷卡共用（原任务侧栏摘要行入口 2026-10-26 退役，§9.1）
  const openIntelAdmin = useCallback(() => {
    setActiveNav('intel')
    setIntelTab('admin')
  }, [])

  // 线索池接线（0.6 §6.1 V2-E2；2026-10-25 模板化预填）：升级订阅 = App 级新建订阅
  // 弹层，预选「企业动态跟踪」模板 + {企业名} 占位符填线索名（零新通道）；
  // 占位符名经 t() 语言无关提取（zh「企业名」/en「company」不同名）；
  // 查看项目 = 切项目导航并选中
  const handleLeadUpgrade = useCallback((lead: LeadRow) => {
    // i18n prompt 键从常量派生（消除 tplId/键名双写，换模板时占位符提取自动跟随）
    const phName = uniquePlaceholderNames(
      t(`task.tpl.${LEAD_UPGRADE_TPL_ID.slice(BUILTIN_PREFIX.length)}.prompt`),
    )[0]
    setSchedulePrefill({
      name: lead.name,
      tplId: LEAD_UPGRADE_TPL_ID,
      phValues: phName ? { [phName]: lead.name } : {},
    })
  }, [t])

  // 项目画像「盯产业」（2.11 §8.1「从项目画像」渠道，2026-10-25）：
  // ProjectProfileLine 每条标签盯产业钮 dispatch 全局事件 → 同一 App 级新建订阅
  // 弹层，预选「产业舆情扫描」+ {产业} 占位符填细分领域（无细分用链条名，
  // detail.phValue 已裁决）；占位符名同上经 i18n 语言无关提取
  useEffect(() => {
    const h = (e: Event) => {
      const { name, phValue } = (e as CustomEvent).detail ?? {}
      const phName = uniquePlaceholderNames(
        t(`task.tpl.${PROFILE_SUBSCRIBE_TPL_ID.slice(BUILTIN_PREFIX.length)}.prompt`),
      )[0]
      setSchedulePrefill({
        name,
        tplId: PROFILE_SUBSCRIBE_TPL_ID,
        phValues: phName && phValue ? { [phName]: phValue } : {},
      })
    }
    window.addEventListener('app:open-schedule-wizard', h)
    return () => window.removeEventListener('app:open-schedule-wizard', h)
  }, [t])

  const handleLeadOpenProject = useCallback(async (projectId: string) => {
    // 悬垂引用兜底（CodeReview Major 2026-10-24）：线索指向的项目可能已删除
    // （存量死链——后端 detach_leads_for_project 只对删除时点生效）或已归档
    // （归档列表懒加载未拉取）。活跃+归档两列表都找不到 → 提示不跳转；归档
    // 列表未拉取 → 先补拉再终判（避免 selectedProject 短暂 null 闪空态页）
    const inList = (list: Project[] | null | undefined) => (list ?? []).some((p) => p.id === projectId)
    if (!inList(projects) && !inList(archivedProjects)) {
      if (archivedProjects !== null) {
        showToast(t('intel.lead.projectGone'), 'error')
        return
      }
      const archived = await loadArchivedProjects()
      if (!inList(projects) && !inList(archived)) {
        showToast(t('intel.lead.projectGone'), 'error')
        return
      }
    }
    setSelectedProjectId(projectId)
    handleNavChange('projects')
  }, [projects, archivedProjects, loadArchivedProjects, showToast, t, handleNavChange])

  // Settings is a peer page too: opening it exits template management / base lib detail
  // section 可选：设置页锚点直达（0.4 §7.4，首页红卡 / 引导弹层 / 向导）
  // projectId 可选：统计页项目筛选（2.10 §6.5 线框 5，项目徽章点击）
  const handleOpenSettings = useCallback((section?: string, projectId?: string) => {
    setShowTemplateManagement(false)
    setSelectedBaseLibId(null)
    setScheduleView(null)
    setSettingsSectionSignal(section ? { name: section, ts: Date.now(), projectId } : null)
    setSettingsOpen(true)
  }, [])

  // 向导流程内步骤「去配置 / 去下载」（0.4 §7.3 ①②③）：挂起向导（不写 onboarding_done）
  // 跳设置锚点，恢复由下方 settingsOpen 关闭 effect 接管；恢复步 = 挂起步的下一步（四步制）
  const ONBOARDING_RESUME_STEP = { externalModel: 2, webSearch: 3, internalModel: 4 } as const
  const handleOnboardingSuspend = useCallback(
    (section: 'externalModel' | 'webSearch' | 'internalModel') => {
      setOnboardingResumeStep(ONBOARDING_RESUME_STEP[section])
      setShowOnboarding(false)
      handleOpenSettings(section)
    },
    [handleOpenSettings],
  )

  // Listen for navigate-project-tab (Activity Hub toast/home feed action) and navigate-tasks
  useEffect(() => {
    const tabHandler = (e: Event) => {
      const { projectId, tab, container, taskId, wikiFocus } = (e as CustomEvent).detail
      closeSettings()
      setShowTemplateManagement(false)
      // 期次抽屉打开中跳转去向：导航切换须同时关抽屉（态不清则抽屉悬浮残留）
      setScheduleView(null)
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
      if (tab) setProjectTabSignal({ tab, ts: Date.now(), taskId, wikiFocus })
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

  // ── Keyboard shortcuts: Ctrl+1~4 for navigation ──
  useEffect(() => {
    const NAV_KEYS: Record<string, string> = {
      '1': 'home', '2': 'tasks', '3': 'projects', '4': 'intel',
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
    // (detail.section = 设置页锚点，0.4 §7.2 action 编码；detail.projectId = 统计页项目筛选，2.10)
    const handleNavSettings = (e: Event) => {
      const { section, projectId } = (e as CustomEvent).detail ?? {}
      handleOpenSettings(section, projectId)
    }
    window.addEventListener('app:navigate-settings', handleNavSettings)
    // 填表模板活动点击 → 打开模板管理页（2.4 §3.4：全局资产无项目实体，导航固定模板管理页）；
    // PPT 模板任务点击携带 detail.section='ppt' 直落 PPT模板 Tab（2.3 §4.2）；
    // 项目产出 Tab 模板管理钮携带 'reports'/'forms' 直落对应页签（0.6 §3.4 V2-E1a T11）
    const handleNavTemplateManagement = (e: Event) => {
      const { section } = (e as CustomEvent).detail ?? {}
      closeSettings()
      setSelectedBaseLibId(null)
      setScheduleView(null)
      setTemplateManagementSection(
        section === 'ppt' || section === 'reports' || section === 'forms' ? section : null,
      )
      setShowTemplateManagement(true)
    }
    window.addEventListener('app:navigate-template-management', handleNavTemplateManagement)
    // 订阅期次通知点击 → 情报中心管理台 tab（2.11 §5.5 + 0.6 §5 V2-E1a：
    // 原 admin peer 态退役，事件名保留兼容）
    const handleNavSchedules = () => {
      closeSettings()
      setSelectedBaseLibId(null)
      setShowTemplateManagement(false)
      // 期次抽屉打开中点订阅类通知：导航切换同时关抽屉（态不清则抽屉残留）
      setScheduleView(null)
      openIntelAdmin()
    }
    window.addEventListener('app:navigate-schedules', handleNavSchedules)
    // 情报中心直达（0.6 §5 V2-E1a 新增：首页情报区「查看全部」等 feed 入口；
    // V2-E2 ReadView/LeadExtract toast action 携 detail.tab 直落线索池）
    const handleNavIntel = (e: Event) => {
      const { tab } = (e as CustomEvent).detail ?? {}
      closeSettings()
      setSelectedBaseLibId(null)
      setShowTemplateManagement(false)
      // 同 handleNavSchedules：导航切换同时关抽屉
      setScheduleView(null)
      setActiveNav('intel')
      // 无 detail 落订阅 tab（默认消费面=收件箱；原落情报流随 Tab 退役改道）
      setIntelTab(tab === 'leads' ? 'leads' : 'admin')
    }
    window.addEventListener('app:navigate-intel', handleNavIntel)
    // 情报速览行点击 → 期次阅读抽屉（2.11 §8.1 动线②，E3 T9；detail.runId）
    const handleNavScheduleRead = (e: Event) => {
      const { runId } = (e as CustomEvent).detail ?? {}
      if (!runId) return
      closeSettings()
      setSelectedBaseLibId(null)
      setShowTemplateManagement(false)
      setScheduleView({ type: 'read', runId })
    }
    window.addEventListener('app:navigate-schedule-read', handleNavScheduleRead)
    // Listen for Python process exit
    const offProcessExit = window.electronAPI.on('python:process-exit', (data: unknown) => {
      const { code } = data as { code: number }
      setProcessExitCode(code)
      // Reset generating state to prevent UI from being stuck
      resetTaskGeneratingState()
    })
    // OS 系统通知点击回流（2.11 §4）：主进程 send 完整 nav，复用通知导航分流
    // （form_template → 模板管理页；项目类 → 项目 Tab，与 Toast action 同一实现）
    const offOsNotifyClick = window.electronAPI.on('app:osNotifyClick', (nav: unknown) => {
      navigateToNotification(nav as NotificationItem)
    })
    // 通知中心开合信号（2.11 §6.1）：TitleBar 铃铛 / 首页「查看全部」dispatch
    const handleOpenNotifications = () => setNotifCenterOpen(true)
    window.addEventListener('app:open-notifications', handleOpenNotifications)
    return () => {
      offSettings()
      offViewLogs()
      offExportLogs()
      offProcessExit?.()
      offOsNotifyClick?.()
      window.removeEventListener('app:open-notifications', handleOpenNotifications)
      window.removeEventListener('app:navigate-settings', handleNavSettings)
      window.removeEventListener('app:navigate-template-management', handleNavTemplateManagement)
      window.removeEventListener('app:navigate-schedules', handleNavSchedules)
      window.removeEventListener('app:navigate-intel', handleNavIntel)
      window.removeEventListener('app:navigate-schedule-read', handleNavScheduleRead)
    }
  }, [handleOpenSettings, closeSettings, openIntelAdmin])

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
        onOpenNotifications={() => setNotifCenterOpen(true)}
      />
      {/* App 级统一告警区（0.5 §4.2 打样修订）：服务异常红条 + 杀软授权橙条（原
          AntivirusBanner 并入），TitleBar 之下全局可见——未验证加白前 Python 侧阻断一切
          依赖安装、服务 down 时全应用降级，均不应只提示某页用户。健康数据自含拉取
          （挂载一次 + app:health-changed / focus 边沿重拉，零新增轮询） */}
      <SystemAlerts onOpenSettings={handleOpenSettings} />
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
                  onBaseLibDeleted={(id) => setSelectedBaseLibId((prev) => (prev === id ? null : prev))}
                  templateManagementActive={showTemplateManagement}
                  onOpenTemplateManagement={() => {
                    closeSettings()
                    setSelectedBaseLibId(null)
                    setTemplateManagementSection(null)
                    setShowTemplateManagement(true)
                  }}
                />
              }
            >
              {!peerPageOpen && activeNav === 'projects' && (
                <ProjectSidebar
                  projects={projectListTab === 'archived' ? (archivedProjects ?? []) : projects}
                  selectedProject={selectedProjectId}
                  onSelectProject={handleProjectSelect}
                  onDeleteProject={handleDeleteProject}
                  onRenameProject={handleRenameProject}
                  onCreateProject={handleCreateProject}
                  healthData={healthByProject}
                  activeTab={projectListTab}
                  onTabChange={handleProjectTabChange}
                  archivedCount={archivedCount}
                  hasMoreProjects={projectListTab === 'archived' ? archivedHasMore : projectsHasMore}
                  loadingMoreProjects={projectListTab === 'archived' ? archivedLoadingMore : projectsLoadingMore}
                  onLoadMoreProjects={projectListTab === 'archived' ? loadMoreArchivedProjects : loadMoreProjects}
                  onArchiveProject={handleArchiveProject}
                  onUnarchiveProject={handleUnarchiveProject}
                  onExportProject={handleExportProject}
                  onImportProject={handleImportProject}
                  onImportCompleted={handleImportCompleted}
                  createSignal={projectCreateSignal}
                  onCreateSignalConsumed={() => setProjectCreateSignal(0)}
                />
              )}
              {!peerPageOpen && activeNav === 'tasks' && (
                <>
                  <TaskList
                    tasks={currentTasks}
                    activeTaskId={activeTaskId}
                    activeTab={activeTab}
                    loading={tasksLoading}
                    creating={taskCreating}
                    onSelect={(id) =>
                      // 0.6 §6.3-16:点已选任务 toggle 取消，回「发送即建任务」欢迎态；
                      // 任务无自动选中 effect，无需 useExplicitDeselect 守卫位
                      setActiveTaskId((prev) => (prev === id ? null : id))
                    }
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
                </>
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
            onSave={async (newSettings) => {
              await handleSaveSettings(newSettings)
              // 设置保存成功 → 广播健康重查边沿（SystemAlerts 唯一消费方，0.5 §4.2）
              window.dispatchEvent(new CustomEvent('app:health-changed'))
            }}
            // settings 表被清空（数据管理面板清理/全清成功）后回灌默认值：
            // 不回灌则旧 form 经自动保存把旧配置写回，清理静默失效（B2）
            onSettingsReset={() => { void reloadSettings() }}
            loadFailed={loadFailed}
            onRetryLoad={reloadSettings}
            sectionSignal={settingsSectionSignal}
          />
        ) : showTemplateManagement ? (
          <TemplateManagement initialSection={templateManagementSection ?? undefined} />
        ) : selectedBaseLibId ? (
          /* Base library is parallel to projects — accessible from any nav */
          <BaseLibDetailPage baseLibId={selectedBaseLibId} />
        ) : activeNav === 'intel' ? (
          /* 情报中心（0.6 §3.2 V2-E1a；两 Tab 收敛 2026-10-25）：订阅管理（默认，
             收件箱即消费面）+ 线索池。期次 read/replay/archive 已抽屉化（第二步
             三态统一 2026-10-25），宿主页不再被 peer 态替换——本分支无遮蔽问题 */
          <IntelPage
            tab={intelTab}
            onTabChange={setIntelTab}
            onOpenRead={(run) => setScheduleView({ type: 'read', runId: run.id })}
            onOpenArchive={(row) => setScheduleView({
              type: 'archive',
              targetId: row.target_id!,
              fileId: row.archive_file_id!,
              scheduleName: row.name,
            })}
            onConvertProject={handleCreateProject}
            onOpenProject={handleLeadOpenProject}
            onUpgrade={handleLeadUpgrade}
          />
        ) : activeNav === 'home' ? (
          <HomePage
            projects={projects}
            projectsTotal={projectsTotal}
            hasMoreProjects={projectsHasMore}
            loadingMoreProjects={projectsLoadingMore}
            onLoadMoreProjects={() => { void loadMoreProjects() }}
            onSelectProject={(id) => {
              setSelectedProjectId(id)
            }}
            onCreateProject={() => {
              handleNavChange('projects')
              setProjectCreateSignal((s) => s + 1)
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
              welcome={<ProjectWelcomeGuide onCreate={handleCreateProject} />}
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
            onSendMessage={handleTaskChatSend}
            onStopGenerate={handleTaskStopGenerate}
            onPauseTask={handleTaskPause}
            onShellConfirm={handleTaskShellConfirm}
            onSkillConfirm={handleTaskSkillConfirm}
            onConfirmExpire={dismissTaskConfirm}
            onResumeTask={handleTaskResume}
            canResume={taskCanResume}
            onFilesUploaded={(uploadedTaskId) => {
              loadTasks()
              // 上传成功后立即刷新消息列表：后端写入的「[系统] 用户上传了文件」马上可见。
              // 必须用回调显式传的 id：零任务态建任务后 state 未反映到本闭包，
              // 读 activeTaskId 会漏刷（陈旧闭包缺陷，review 2026-10-21）
              loadTaskMessages(uploadedTaskId)
            }}
            onEnsureTask={ensureTask}
            onSwitchMode={(mode) => activeTaskId && handleSetTaskMode(activeTaskId, mode)}
          />
        ) : (
          <div />
        )}
      </div>
      {/* 状态栏（2.10 §6.2/线框 1）：App 级常驻底栏，纯全局分段，点击跳统计页/首页/关于 */}
      <StatusBar
        onOpenHome={() => handleNavChange('home')}
        onOpenStats={() => handleOpenSettings('usage')}
        onShowAbout={() => setAboutOpen(true)}
      />
      {/* 期次三态抽屉（0.6 §3.2 第二步 2026-10-25）：read/replay/archive 右侧滑出，
          宿主页保持原位；关闭=头部返回/Esc/点遮罩，全局导航动作经各 handler
          setScheduleView(null) 同步关闭 */}
      {!!scheduleView && (
        <ScheduleDrawer
          view={scheduleView}
          onClose={() => setScheduleView(null)}
          onViewChange={setScheduleView}
        />
      )}
      {/* 内嵌浏览器面板（2.8 §8）：App 级常驻，三态由 store 驱动 */}
      <BrowserPanel />
      <AboutDialog open={aboutOpen} onClose={() => setAboutOpen(false)} />
      {/* 订阅预填弹层（0.6 §6.1 线索升级 + 2.11 §8.1 画像盯产业共用）：
          App 级新建订阅弹层，保存后管理台侧数据由挂载/边沿刷新，此处仅收口 */}
      <ScheduleFormModal
        open={!!schedulePrefill}
        prefill={schedulePrefill ?? undefined}
        onClose={() => setSchedulePrefill(null)}
        onSaved={() => setSchedulePrefill(null)}
      />
      {/* 通知中心全局面板（2.11 §6.1）：铃铛 / 首页「查看全部」打开 */}
      {notifCenterOpen && <NotificationCenter open onClose={() => setNotifCenterOpen(false)} />}
      {showOnboarding && (
        <OnboardingWizard
          onFinish={() => {
            setShowOnboarding(false)
            setOnboardingResumeStep(null)
            // 0.5 §4.5 E2 T8:零项目态完成向导 → 呈现项目欢迎页引导(ProjectWelcomeGuide 已引导建项)
            const nav = navAfterOnboarding(projects.length)
            if (nav) handleNavChange(nav)
          }}
          onSuspend={handleOnboardingSuspend}
          initialStep={onboardingResumeStep ?? 0}
        />
      )}
      {/* 身份层在向导之后渲染（同 z 层 2000，DOM 后序在上）：新用户顺序
          Persona → OnboardingWizard → 首页（0.6 §3.8） */}
      {showPersona && (
        <PersonaPicker onSelect={handlePersonaSelect} onSkip={handlePersonaSkip} />
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
