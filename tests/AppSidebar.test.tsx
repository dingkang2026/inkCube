/** tests/AppSidebar.test.tsx — 同级页面侧边栏列表清空回归
 *
 * 背景：设置 / 模板管理 / 基础库详情与首页同为「同级页面」（peer page），此前
 * 打开它们只切主内容区、不改 activeNav，导致侧边栏残留项目 / 任务列表（首页
 * 行为是清空）。修复后 App 以 peerPageOpen 统一判定侧边栏列表与导航高亮。
 *
 * 集成范围：重型页面 / 轮询组件 / 数据源 hook 打桩，保留真实的
 * Sidebar → NavTabs / UserBar / ProjectList / TaskList / BaseLibFooter 链路，
 * 纵向验证「点击入口 → 侧边栏列表卸载 → 切回导航 → 列表复原」全链路。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import App from '../src/App'
import { ConfirmProvider } from '../src/components/ConfirmDialog'
import { PromptProvider } from '../src/components/PromptDialog'
import { ToastProvider } from '../src/features/knowledge/Toast'

// i18n：t 透传 key（断言用 i18n key 文本），i18n.language 供 TaskItem 时间格式化
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k, i18n: { language: 'en-US' } }),
}))

// ── 主内容区页面桩：只断言「是否切到该页」──
vi.mock('../src/features/settings/SettingsPage', () => ({
  SettingsPage: () => <div data-testid="settings-page" />,
}))
vi.mock('../src/features/project/tabs/TemplateManagement', () => ({
  TemplateManagement: () => <div data-testid="template-management" />,
}))
vi.mock('../src/features/project/BaseLibDetailPage', () => ({
  BaseLibDetailPage: () => <div data-testid="baselib-detail" />,
}))
vi.mock('../src/features/home/HomePage', () => ({
  HomePage: () => <div data-testid="home-page" />,
}))
vi.mock('../src/features/knowledge/KnowledgePage', () => ({
  KnowledgePage: () => <div data-testid="knowledge-page" />,
}))
vi.mock('../src/features/project/ProjectDetailPage', () => ({
  ProjectDetailPage: () => <div data-testid="project-detail" />,
}))
vi.mock('../src/features/task/TaskPage', () => ({
  // 捕获 props：任务行 toggle 用例断言 App 层 taskId/hasActiveTask 流转
  TaskPage: (props: Record<string, unknown>) => {
    taskPageState.last = props
    return <div data-testid="task-page" />
  },
}))

// ── 弹层 / 轮询组件桩：本测试不涉向导 / 关于框 / 活动数据 ──
vi.mock('../src/features/onboarding/OnboardingWizard', () => ({ OnboardingWizard: () => null }))
vi.mock('../src/components/layout/AboutDialog', () => ({ AboutDialog: () => null }))
vi.mock('../src/components/layout/TitleBar', () => ({ TitleBar: () => null }))
vi.mock('../src/features/activity/ActivityMonitor', () => ({ ActivityMonitor: () => null }))
vi.mock('../src/features/task/TaskStatusMonitor', () => ({ TaskStatusMonitor: () => null }))

// ── 数据源 hook 桩：可控的项目 / 任务数据 ──
const tasksState = vi.hoisted(() => ({
  tasks: [
    { id: 't1', title: '任务一', status: 'active', mode: 'coding', updated_at: '2026-01-01T08:00:00Z', message_count: 0 },
  ],
}))
const taskPageState = vi.hoisted(() => ({ last: null as Record<string, unknown> | null }))
// activeTaskId 用真实 useState 流转（非固定 null 桩）：任务行 toggle 用例需验证
// 「点已选任务 → 取消选中」全链（0.6 §6.3-16），其余字段保持静态桩
vi.mock('../src/features/task/useTasks', async () => {
  const { useState } = await import('react')
  return {
    useTasks: () => {
      const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
      return {
        tasks: tasksState.tasks,
        archivedTasks: [],
        currentTasks: tasksState.tasks,
        activeTab: 'active',
        setActiveTab: vi.fn(),
        activeTaskId,
        setActiveTaskId,
        loadTasks: vi.fn(),
        loadMore: vi.fn(),
        hasMore: { active: false, archived: false },
        loading: false,
        creating: false,
        handleCreateTask: vi.fn(),
        handleCreateTaskWithDir: vi.fn(),
        handleDeleteTask: vi.fn(),
        handleRenameTask: vi.fn(),
        handleArchiveTask: vi.fn(),
        handleUnarchiveTask: vi.fn(),
        handleExportTask: vi.fn(),
        handleSetTaskMode: vi.fn(),
      }
    },
  }
})
vi.mock('../src/features/task/useTaskChat', () => ({
  useTaskChat: () => ({
    messages: [], isGenerating: false, streamContent: '', streamSteps: [],
    streamReasoning: '', streamIteration: 0, streamIterationThoughts: [],
    streamIterationReasoning: [], planData: null,
    pendingShellConfirm: null, pendingSkillConfirm: null,
    handleSendMessage: vi.fn(), handleStopGenerate: vi.fn(), handlePauseTask: vi.fn(),
    handleShellConfirm: vi.fn(), handleSkillConfirm: vi.fn(), handleResumeTask: vi.fn(),
    canResume: false, resetGeneratingState: vi.fn(), loadMessages: vi.fn(),
  }),
}))
vi.mock('../src/features/settings/useSettings', () => ({
  useSettings: () => ({
    settings: { api_key: 'configured' },
    settingsLoaded: true,
    handleSaveSettings: vi.fn(),
  }),
}))

const PROJECTS = [
  { id: 'p1', name: '项目甲', description: '', type: 'general', file_count: 0, chunk_count: 0, created_at: '2026-01-01T00:00:00Z' },
  { id: 'p2', name: '项目乙', description: '', type: 'general', file_count: 0, chunk_count: 0, created_at: '2026-01-02T00:00:00Z' },
]
const BASE_LIBS = [
  { id: 'bl-custom-1', name: '自定义基础库', description: '', lib_type: 'custom', file_count: 0 },
]

const wireWindow = () => {
  const invoke = vi.fn(async (channel: string) => {
    switch (channel) {
      case 'knowledge:listProjects': return { projects: PROJECTS }
      case 'baseLib:list': return { base_libs: BASE_LIBS }
      case 'system:getOnboarding': return { done: true }
      default: return {}
    }
  })
  ;(window as { electronAPI?: unknown }).electronAPI = {
    invoke,
    on: vi.fn(() => vi.fn()),
    send: vi.fn(),
  }
  return invoke
}

/** 排空 microtask（App 挂载 / 导航后的 invoke 异步回填） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  wireWindow()
})

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

describe('App 同级页面侧边栏清空', () => {
  it('项目列表：打开设置清空，切回 projects 复原', async () => {
    render(
      <ConfirmProvider>
        <PromptProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </PromptProvider>
      </ConfirmProvider>,
    )
    await flush()
    fireEvent.click(screen.getByText('nav.projects'))
    await flush()
    // 进入项目页：侧边栏项目列表 + 主内容区自动选中首个项目
    expect(screen.getByText('项目甲')).toBeTruthy()
    expect(screen.getByTestId('project-detail')).toBeTruthy()

    // UserBar 齿轮打开设置：项目列表卸载（与首页行为一致），不再残留
    fireEvent.click(screen.getByTitle('userBar.settings'))
    await flush()
    expect(screen.getByTestId('settings-page')).toBeTruthy()
    expect(screen.queryByText('项目甲')).toBeNull()

    // NavTabs 切回 projects：列表复原、设置页退出
    fireEvent.click(screen.getByText('nav.projects'))
    await flush()
    expect(screen.getByText('项目甲')).toBeTruthy()
    expect(screen.queryByTestId('settings-page')).toBeNull()
  })

  it('任务列表：打开模板管理清空，切回 tasks 复原', async () => {
    render(
      <ConfirmProvider>
        <PromptProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </PromptProvider>
      </ConfirmProvider>,
    )
    await flush()
    fireEvent.click(screen.getByText('nav.tasks'))
    await flush()
    expect(screen.getByText('任务一')).toBeTruthy()

    // footer「模板管理」入口：任务列表卸载
    fireEvent.click(screen.getByText('project.report.templateManagement'))
    await flush()
    expect(screen.getByTestId('template-management')).toBeTruthy()
    expect(screen.queryByText('任务一')).toBeNull()
    expect(screen.queryByText('task.newTask')).toBeNull()

    fireEvent.click(screen.getByText('nav.tasks'))
    await flush()
    expect(screen.getByText('任务一')).toBeTruthy()
    expect(screen.queryByTestId('template-management')).toBeNull()
  })

  it('任务行 toggle 选中（0.6 §6.3-16）：点未选行选中，再点同行取消回欢迎态', async () => {
    render(
      <ConfirmProvider>
        <PromptProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </PromptProvider>
      </ConfirmProvider>,
    )
    await flush()
    fireEvent.click(screen.getByText('nav.tasks'))
    await flush()
    // item 根节点 = title 的祖父（title → itemContent → item）；高亮类 active 由 isActive 派生
    const row = screen.getByText('任务一').closest('div')!.parentElement!.parentElement as HTMLElement
    expect(row.className).not.toContain('active')
    expect(taskPageState.last?.hasActiveTask).toBe(false)

    // 首击：选中
    fireEvent.click(screen.getByText('任务一'))
    expect(row.className).toContain('active')
    expect(taskPageState.last?.taskId).toBe('t1')
    expect(taskPageState.last?.hasActiveTask).toBe(true)

    // 再击同行：取消选中，回「发送即建任务」欢迎态
    fireEvent.click(screen.getByText('任务一'))
    expect(row.className).not.toContain('active')
    expect(taskPageState.last?.taskId).toBeNull()
    expect(taskPageState.last?.hasActiveTask).toBe(false)
  })

  it('项目列表：打开基础库详情清空，切回 projects 复原', async () => {
    render(
      <ConfirmProvider>
        <PromptProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </PromptProvider>
      </ConfirmProvider>,
    )
    await flush()
    fireEvent.click(screen.getByText('nav.projects'))
    await flush()
    expect(screen.getByText('项目甲')).toBeTruthy()

    // footer 基础库条目：详情页打开，项目列表卸载
    fireEvent.click(screen.getByText('自定义基础库'))
    await flush()
    expect(screen.getByTestId('baselib-detail')).toBeTruthy()
    expect(screen.queryByText('项目甲')).toBeNull()

    fireEvent.click(screen.getByText('nav.projects'))
    await flush()
    expect(screen.getByText('项目甲')).toBeTruthy()
    expect(screen.queryByTestId('baselib-detail')).toBeNull()
  })
})
