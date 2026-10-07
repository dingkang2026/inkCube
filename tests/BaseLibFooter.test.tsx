/** tests/BaseLibFooter.test.tsx — 基础库侧栏区活跃点用例（0.4 §5.7 / §8.3，顺序 3 补测）
 *  覆盖：baselib 容器活跃 → 呼吸点 + tooltip；
 *  project 容器桶防御性过滤（不渲染点）；无活动 → 无点。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import type { ProjectActivity } from '../src/features/activity/types'

// CSS module 显式 mock（vitest 默认空对象，className 无法断言）
vi.mock('../src/features/project/BaseLibFooter.module.css', () => ({
  default: { activityDot: 'activity-dot' },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}))

vi.mock('../src/components/PromptDialog', () => ({
  usePrompt: () => vi.fn().mockResolvedValue(null),
}))

// useActivity 可控 byProject（hoisted：vi.mock 工厂先于顶层 const 执行）
const { byProjectMock } = vi.hoisted(() => ({
  byProjectMock: new Map<string, ProjectActivity>(),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ byProject: byProjectMock }),
}))

import { BaseLibFooter } from '../src/features/project/BaseLibFooter'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  byProjectMock.clear()
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
    ...over,
  }
}

function renderFooter() {
  return render(
    <BaseLibFooter selectedBaseLib={null} onSelectBaseLib={() => {}} />,
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
})
