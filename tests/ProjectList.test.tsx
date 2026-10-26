/** tests/ProjectList.test.tsx — 项目列表导出/导入入口（2.9project-transfer-design.md §8）
 *  覆盖：菜单导出项渲染/回调、导入按钮渲染/回调、导出中菜单禁用、导入中按钮进度态；
 *  新建项目勾选「同时创建企业动态跟踪订阅」（0.6 §3.3：禁用态/hint 动态切换/
 *  勾选 payload/不勾零调用/订阅失败部分失败语义）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { ProjectList } from '../src/features/knowledge/ProjectList'

// useActivity 双形态（ActivityContext.tsx：无参返回含 byProject 的完整 ctx——ProjectList
// 活跃点用；带 kind 返回 ActivityKindView——导出/导入进度态用）。按 kind 注入视图。
const activityViews = vi.hoisted(() => new Map<string, { items: unknown[]; isActive: boolean }>())

vi.mock('react-i18next', () => ({
  // 勾选创建订阅用例依赖真实形态的 competitor prompt（含 {企业名} 占位符）：
  // 占位符提取 → applyValues 固化断言都建立在真实模板结构上（同 LeadPool.test）
  useTranslation: () => ({
    t: (k: string) =>
      k === 'task.tpl.competitor.prompt' ? '跟踪「{企业名}」近一个月的动态' : k,
  }),
}))

const toastMock = vi.fn()
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

const invokeMock = vi.fn()

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) => {
    if (!kind) {
      return {
        byProject: new Map(),
        activeProjectIds: new Set(),
        status: null,
        feed: [],
        applyStatus: vi.fn(),
        markRead: vi.fn(),
      }
    }
    const v = activityViews.get(kind)
    return { kind, items: v?.items ?? [], isActive: v?.isActive ?? false }
  },
}))

vi.mock('../src/features/project/affairs/HealthDot', () => ({
  HealthDot: () => null,
}))

const baseProject = {
  id: 'p1',
  name: 'Alpha',
  description: '',
  file_count: 0,
  chunk_count: 0,
  created_at: '2026-10-16T00:00:00Z',
} as ComponentProps<typeof ProjectList>['projects'][number]

function renderList(props: Partial<ComponentProps<typeof ProjectList>> = {}) {
  const base: ComponentProps<typeof ProjectList> = {
    projects: [baseProject],
    selectedProject: null,
    onSelect: () => {},
    onDelete: () => {},
    activeTab: 'active',
    onTabChange: () => {},
    archivedCount: 0,
    onArchiveProject: () => {},
    onUnarchiveProject: () => {},
  }
  return render(<ProjectList {...base} {...props} />)
}

beforeEach(() => {
  activityViews.clear()
  toastMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockResolvedValue({})
  ;(window as unknown as { electronAPI: { invoke: typeof invokeMock } }).electronAPI = {
    invoke: invokeMock,
  }
})

describe('ProjectList 导出/导入入口（2.9 §8）', () => {

  it('提供 onExportProject 时菜单含导出项，点击回调携带项目 id 与名称', () => {
    const onExportProject = vi.fn()
    renderList({ onExportProject })
    fireEvent.click(screen.getByLabelText('knowledge.projectActions'))
    fireEvent.click(screen.getByText('knowledge.exportProject'))
    expect(onExportProject).toHaveBeenCalledWith('p1', 'Alpha')
  })

  it('未提供 onExportProject 时不渲染导出项', () => {
    renderList()
    fireEvent.click(screen.getByLabelText('knowledge.projectActions'))
    expect(screen.queryByText('knowledge.exportProject')).toBeNull()
  })

  it('提供 onImportProject 时渲染导入图标钮，点击触发回调', () => {
    const onImportProject = vi.fn()
    renderList({ onImportProject })
    fireEvent.click(screen.getByTitle('knowledge.importProject'))
    expect(onImportProject).toHaveBeenCalledTimes(1)
  })

  it('未提供 onImportProject 时不渲染导入图标钮', () => {
    renderList()
    expect(screen.queryByTitle('knowledge.importProject')).toBeNull()
  })

  it('导出中：菜单项禁用（2.9 §8.2 幂等防重）', () => {
    activityViews.set('project_export', {
      items: [{ entity_type: 'project_export', project_id: 'p1', progress: 40 }],
      isActive: true,
    })
    renderList({ onExportProject: vi.fn() })
    fireEvent.click(screen.getByLabelText('knowledge.projectActions'))
    expect(screen.getByText('knowledge.exportProject')).toHaveProperty('disabled', true)
  })

  it('导入中：图标钮禁用且 title 回显进度（2.9 §8.2）', () => {
    activityViews.set('project_import', {
      items: [{ entity_type: 'project_import', project_id: null, progress: 45 }],
      isActive: true,
    })
    renderList({ onImportProject: vi.fn() })
    expect(screen.getByTitle('knowledge.importing 45%')).toHaveProperty('disabled', true)
  })

  it('createSignal 递增自动打开创建模态（0.5 E1 T4 首页「新建项目」信号）', () => {
    const full: ComponentProps<typeof ProjectList> = {
      projects: [baseProject], selectedProject: null, onSelect: () => {}, onDelete: () => {},
      activeTab: 'active', onTabChange: () => {}, archivedCount: 0,
      onArchiveProject: () => {}, onUnarchiveProject: () => {}, createSignal: 0,
    }
    const view = render(<ProjectList {...full} />)
    expect(screen.queryByText('knowledge.createProject')).toBeNull()
    view.rerender(<ProjectList {...full} createSignal={1} />)
    expect(screen.getByText('knowledge.createProject')).toBeTruthy()
  })

  it('createSignal 消费即复位：回调被调用且重挂载后不再重放（E1 review M1）', () => {
    const full: ComponentProps<typeof ProjectList> = {
      projects: [baseProject], selectedProject: null, onSelect: () => {}, onDelete: () => {},
      activeTab: 'active', onTabChange: () => {}, archivedCount: 0,
      onArchiveProject: () => {}, onUnarchiveProject: () => {}, createSignal: 1,
    }
    const onConsumed = vi.fn()
    const view = render(<ProjectList {...full} onCreateSignalConsumed={onConsumed} />)
    expect(screen.getByText('knowledge.createProject')).toBeTruthy()
    expect(onConsumed).toHaveBeenCalledTimes(1)
    // 模拟 ProjectSidebar 条件渲染卸载重挂：父已把 signal 复位为 0，重挂后模态不得无故弹出
    view.unmount()
    render(<ProjectList {...full} createSignal={0} onCreateSignalConsumed={onConsumed} />)
    expect(screen.queryByText('knowledge.createProject')).toBeNull()
  })
})

describe('新建项目勾选创建订阅（0.6 §3.3）', () => {
  function openCreateModal(onCreate: ComponentProps<typeof ProjectList>['onCreate']) {
    renderList({ onCreate })
    fireEvent.click(screen.getByText('knowledge.newProject'))
  }

  it('企业名空时勾选禁用，hint 解释禁用原因；填写后 hint 切换为产出说明（免培训）', () => {
    openCreateModal(vi.fn())
    const box = screen.getByTestId('project-create-sub') as HTMLInputElement
    expect(box.disabled).toBe(true)
    expect(screen.getByTestId('project-create-sub-hint').textContent).toBe('schedule.createSub.needCompany')
    fireEvent.change(screen.getByPlaceholderText('knowledge.companyName'), { target: { value: '华润置地有限公司' } })
    expect(box.disabled).toBe(false)
    expect(screen.getByTestId('project-create-sub-hint').textContent).toBe('schedule.createSub.hint')
  })

  it('勾选后创建：schedules:create payload 模板固化 + 绑项目去向；主链确认态不受影响', async () => {
    openCreateModal(vi.fn().mockResolvedValue({ id: 'proj_new', seeded_tasks_count: 3 }))
    fireEvent.change(screen.getByPlaceholderText('knowledge.projectName'), { target: { value: '华润置地专项' } })
    fireEvent.change(screen.getByPlaceholderText('knowledge.companyName'), { target: { value: '华润置地有限公司' } })
    fireEvent.click(screen.getByTestId('project-create-sub'))
    fireEvent.click(screen.getByText('knowledge.create'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('schedules:create', {
        name: 'schedule.createSub.name',
        prompt: '跟踪「华润置地有限公司」近一个月的动态',
        category: 'corporate',
        intervalType: 'daily',
        intervalValue: '09:00',
        targetType: 'project',
        targetId: 'proj_new',
        autoImport: true,
        scope: null,
        digestScheduleIds: null,
      })
    })
    expect(screen.getByText('knowledge.wizardConfirmTitle')).toBeTruthy()
  })

  it('不勾选零调用（订阅创建不静默代做）', async () => {
    openCreateModal(vi.fn().mockResolvedValue({ id: 'proj_new' }))
    fireEvent.change(screen.getByPlaceholderText('knowledge.projectName'), { target: { value: '华润置地专项' } })
    fireEvent.change(screen.getByPlaceholderText('knowledge.companyName'), { target: { value: '华润置地有限公司' } })
    fireEvent.click(screen.getByText('knowledge.create'))
    await waitFor(() => expect(screen.getByText('knowledge.wizardConfirmTitle')).toBeTruthy())
    expect(invokeMock).not.toHaveBeenCalledWith('schedules:create', expect.anything())
  })

  it('订阅创建失败：toast 提示不阻断，确认态照常呈现（部分失败语义）', async () => {
    invokeMock.mockRejectedValue(new Error('boom'))
    openCreateModal(vi.fn().mockResolvedValue({ id: 'proj_new' }))
    fireEvent.change(screen.getByPlaceholderText('knowledge.projectName'), { target: { value: '华润置地专项' } })
    fireEvent.change(screen.getByPlaceholderText('knowledge.companyName'), { target: { value: '华润置地有限公司' } })
    fireEvent.click(screen.getByTestId('project-create-sub'))
    fireEvent.click(screen.getByText('knowledge.create'))
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith('schedule.createSub.failed', 'error'))
    expect(screen.getByText('knowledge.wizardConfirmTitle')).toBeTruthy()
  })
})
