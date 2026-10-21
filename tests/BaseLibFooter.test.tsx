/** tests/BaseLibFooter.test.tsx — 基础库侧栏区活跃点用例（0.4 §5.7 / §8.3，顺序 3 补测）
 *  覆盖：baselib 容器活跃 → 呼吸点 + tooltip；
 *  project 容器桶防御性过滤（不渲染点）；无活动 → 无点。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import type { ProjectActivity } from '../src/features/activity/types'

// CSS module 显式 mock（vitest 默认空对象，className 无法断言）
vi.mock('../src/features/project/BaseLibFooter.module.css', () => ({
  default: { activityDot: 'activity-dot', dotAlert: 'dot-alert' },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
  // 列表加载失败提示用例引入 provider 包裹；mock 透传 children 即可（组件内 useToast 已被本工厂接管）
  ToastProvider: ({ children }: { children: React.ReactNode }) => children,
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}))

vi.mock('../src/components/PromptDialog', () => ({
  usePrompt: () => vi.fn().mockResolvedValue(null),
}))

// useActivity 可控 byProject / kind 视图（hoisted：vi.mock 工厂先于顶层 const 执行）；
// kind 分派供模板工坊徽标（useActivity('form_template'/'ppt')，2.3 §4.2）与既有点位共存
const { byProjectMock, kindItemsMock } = vi.hoisted(() => ({
  byProjectMock: new Map<string, ProjectActivity>(),
  kindItemsMock: {} as Record<string, { status?: string; container?: string | null }[]>,
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) => kind
    ? {
        kind,
        items: kindItemsMock[kind] ?? [],
        isActive: (kindItemsMock[kind] ?? []).length > 0,
      }
    : { byProject: byProjectMock },
}))

import { BaseLibFooter } from '../src/features/project/BaseLibFooter'
import { ToastProvider } from '../src/features/knowledge/Toast'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  byProjectMock.clear()
  for (const k of Object.keys(kindItemsMock)) delete kindItemsMock[k]
  invokeMock.mockResolvedValue({
    base_libs: [
      { id: 'lib1', name: '共享资料库', description: '', lib_type: 'preset_recruitment', file_count: 0 },
      { id: 'lib2', name: '自定义库', description: '', lib_type: null, file_count: 0 },
    ],
  })
  ;(window as any).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  // vitest 未开 globals，RTL auto-cleanup 不生效，DOM 会跨用例累积导致多元素匹配
  cleanup()
  delete (window as any).electronAPI
})

function makePA(projectId: string, over: Partial<ProjectActivity> = {}): ProjectActivity {
  return {
    projectId,
    projectName: projectId,
    container: 'baselib',
    files: 0,
    imageDescribing: 0,
    compiles: 0,
    reports: 0,
    forms: 0,
    ppts: 0,
    ...over,
  }
}

function renderFooter() {
  return render(
    <ToastProvider>
      {/* 传入 onOpenTemplateManagement：模板工坊徽标用例（2.3 §4.2）断言入口区块内点位 */}
      <BaseLibFooter
        selectedBaseLib={null}
        onSelectBaseLib={() => {}}
        onOpenTemplateManagement={() => {}}
      />
    </ToastProvider>,
  )
}

describe('BaseLibFooter 活跃点', () => {
  it('baselib 容器有进行中活动 → 呼吸点 + tooltip 含文件概要', async () => {
    byProjectMock.set('lib1', makePA('lib1', { files: 2, compiles: 1 }))
    const { container } = renderFooter()

    await screen.findByText('共享资料库')
    const dots = container.querySelectorAll('.activity-dot')
    expect(dots).toHaveLength(1)
    // tooltip 与项目行对齐（i18n mock 直出 key，拼接段顺序：文件→编译）
    expect(dots[0].getAttribute('title')).toBe('activity.tipFiles / activity.tipCompiles')
  })

  it('project 容器桶防御性过滤：同名 id 桶不带 baselib 标记时不渲染点', async () => {
    // 异常数据防御：活动桶 container='project'（如后端遗漏归一化）不得点亮基础库行
    byProjectMock.set('lib1', makePA('lib1', { container: 'project', files: 5 }))
    const { container } = renderFooter()

    await screen.findByText('共享资料库')
    expect(container.querySelectorAll('.activity-dot')).toHaveLength(0)
  })

  it('无活动 → 所有行无点', async () => {
    const { container } = renderFooter()

    await screen.findByText('共享资料库')
    expect(container.querySelectorAll('.activity-dot')).toHaveLength(0)
  })

  it('PPT 模板任务 running → 中性活跃点（2.3 §4.2 徽标）', async () => {
    kindItemsMock.ppt = [
      { entity_type: 'ppt', container: 'templates', status: 'running', entity_id: 'p1' },
    ]
    const { container } = renderFooter()

    await screen.findByText('共享资料库')
    const dots = container.querySelectorAll('.activity-dot')
    expect(dots).toHaveLength(1)
    expect(dots[0].getAttribute('title')).toBe('activity.tipPptTemplates')
    expect(dots[0].className).not.toContain('dot-alert')
  })

  it('PPT 模板任务 awaiting_input → 醒目点优先（2.3 §4.2 门禁待确认）', async () => {
    kindItemsMock.ppt = [
      { entity_type: 'ppt', container: 'templates', status: 'awaiting_input', entity_id: 'p1' },
      { entity_type: 'ppt', container: 'templates', status: 'running', entity_id: 'p2' },
    ]
    const { container } = renderFooter()

    await screen.findByText('共享资料库')
    const dots = container.querySelectorAll('.activity-dot')
    expect(dots).toHaveLength(1)
    expect(dots[0].className).toContain('dot-alert')
    expect(dots[0].getAttribute('title')).toBe('activity.tipPptTemplatesAwaiting')
  })

  it('生成类 ppt（container=project）不点亮模板管理入口', async () => {
    kindItemsMock.ppt = [
      { entity_type: 'ppt', container: 'project', status: 'running', entity_id: 'p1' },
    ]
    const { container } = renderFooter()

    await screen.findByText('共享资料库')
    expect(container.querySelectorAll('.activity-dot')).toHaveLength(0)
  })
})
