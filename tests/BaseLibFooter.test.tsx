/** tests/BaseLibFooter.test.tsx — 基础库侧栏区用例（0.4 §5.7 / §8.3 + 2.1 §7 自定义库 CRUD）
 *  活跃点：baselib 容器活跃 → 呼吸点 + tooltip；project 容器桶防御性过滤
 *  （不渲染点）；无活动 → 无点。
 *  自定义库 CRUD：预置库行无操作按钮 / 自定义库行行内编辑/删除；新建/编辑
 *  表单弹窗提交（baseLib:create/update）与删除确认（baseLib:delete，含
 *  选中态清理回调）；守门 code 经 structuredPythonResponse 返回值透传
 *  （判 res.ok，409 错误码 toast / reject 通用文案两分支），弹窗保留可重试。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import type { ProjectActivity } from '../src/features/activity/types'

// CSS module 显式 mock（vitest 默认空对象，className 无法断言）
vi.mock('../src/features/project/BaseLibFooter.module.css', () => ({
  default: { activityDot: 'activity-dot', dotAlert: 'dot-alert' },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

// toast 捕获（CRUD 成功/失败提示断言；hoisted：mock 工厂先于顶层 const 执行）
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
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
  toastMock.mockClear()
  byProjectMock.clear()
  for (const k of Object.keys(kindItemsMock)) delete kindItemsMock[k]
  invokeMock.mockImplementation(async (channel: string) => {
    // CRUD 通道 structuredPythonResponse 成功形态（组件判 res.ok 才算成功）
    if (channel === 'baseLib:create' || channel === 'baseLib:update' || channel === 'baseLib:delete') {
      return { ok: true }
    }
    return {
      base_libs: [
        { id: 'lib1', name: '共享资料库', description: '', lib_type: 'preset_recruitment', file_count: 0 },
        { id: 'lib2', name: '自定义库', description: '', lib_type: null, file_count: 0 },
      ],
    }
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

function renderFooter(over: Partial<Parameters<typeof BaseLibFooter>[0]> = {}) {
  return render(
    <ToastProvider>
      {/* 传入 onOpenTemplateManagement：模板工坊徽标用例（2.3 §4.2）断言入口区块内点位 */}
      <BaseLibFooter
        selectedBaseLib={null}
        onSelectBaseLib={() => {}}
        onOpenTemplateManagement={() => {}}
        {...over}
      />
    </ToastProvider>,
  )
}

/** 泡微任务：async 提交链（invoke → toast/reload）落定 */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
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

describe('自定义库 CRUD（2.1 §7）', () => {
  it('预置库行无操作按钮；自定义库行行内编辑/删除', async () => {
    renderFooter()
    await screen.findByText('共享资料库')
    await screen.findByText('自定义库')

    const presetRow = screen.getByText('共享资料库').closest('div')!
    expect(presetRow.querySelectorAll('button')).toHaveLength(0)

    const customRow = screen.getByText('自定义库').closest('div')!
    expect(customRow.querySelectorAll('button')).toHaveLength(2)   // 编辑 + 删除
  })

  it('新建：+ 打开表单弹窗，提交调 baseLib:create + 成功 toast + 重拉列表', async () => {
    renderFooter()
    await screen.findByText('自定义库')

    fireEvent.click(screen.getByTestId('baselib-create-btn'))
    const dialog = await screen.findByTestId('baselib-form-modal')
    // 空名时提交禁用（必填校验）
    expect((screen.getByTestId('baselib-form-submit') as HTMLButtonElement).disabled).toBe(true)

    fireEvent.change(dialog.querySelector('#baselib-name')!, { target: { value: '产业情报库' } })
    fireEvent.change(dialog.querySelector('#baselib-desc')!, { target: { value: '承载情报沉淀' } })
    expect((screen.getByTestId('baselib-form-submit') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByTestId('baselib-form-submit'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('baseLib:create', {
      name: '产业情报库', description: '承载情报沉淀',
    })
    expect(toastMock).toHaveBeenCalledWith('baseLib.createSuccess', 'success')
    // reload：create 后重拉列表（invoke 至少 2 次 baseLib:list）
    expect(invokeMock.mock.calls.filter(([c]) => c === 'baseLib:list').length).toBeGreaterThanOrEqual(2)
  })

  it('编辑：行内铅笔预填表单，提交调 baseLib:update（带 libId）', async () => {
    renderFooter()
    await screen.findByText('自定义库')

    fireEvent.click(screen.getByTestId('baselib-edit-lib2'))
    const dialog = await screen.findByTestId('baselib-form-modal')
    // 预填现名/描述
    expect((dialog.querySelector('#baselib-name') as HTMLInputElement).value).toBe('自定义库')

    fireEvent.change(dialog.querySelector('#baselib-name')!, { target: { value: '新名库' } })
    fireEvent.click(screen.getByTestId('baselib-form-submit'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('baseLib:update', {
      libId: 'lib2', name: '新名库', description: '',
    })
    expect(toastMock).toHaveBeenCalledWith('baseLib.updateSuccess', 'success')
  })

  it('删除确认：确认调 baseLib:delete，删除当前选中库时回调 onBaseLibDeleted', async () => {
    const onDeleted = vi.fn()
    renderFooter({ selectedBaseLib: 'lib2', onBaseLibDeleted: onDeleted })
    await screen.findByText('自定义库')

    fireEvent.click(screen.getByTestId('baselib-delete-lib2'))
    await screen.findByTestId('baselib-delete-modal')
    fireEvent.click(screen.getByTestId('baselib-delete-confirm'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('baseLib:delete', { libId: 'lib2' })
    expect(toastMock).toHaveBeenCalledWith('baseLib.deleteSuccess', 'success')
    expect(onDeleted).toHaveBeenCalledWith('lib2')   // 删的是选中库 → App 清选中态
  })

  it('删除非选中库：不触发 onBaseLibDeleted', async () => {
    const onDeleted = vi.fn()
    renderFooter({ selectedBaseLib: 'lib1', onBaseLibDeleted: onDeleted })
    await screen.findByText('自定义库')

    fireEvent.click(screen.getByTestId('baselib-delete-lib2'))
    await screen.findByTestId('baselib-delete-modal')
    fireEvent.click(screen.getByTestId('baselib-delete-confirm'))
    await flush()

    expect(onDeleted).not.toHaveBeenCalled()
  })

  it('删除被拒（409 有在途活动）→ 错误码映射 toast，弹窗保留可重试', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'baseLib:delete') {
        // structuredPythonResponse 返回值形态：409 code 经返回值透传（不再 throw）
        return { ok: false, code: 'BASE_LIB_HAS_ACTIVE_WORK', error: '基础库尚有进行中的活动' }
      }
      return {
        base_libs: [
          { id: 'lib1', name: '共享资料库', description: '', lib_type: 'preset_recruitment', file_count: 0 },
          { id: 'lib2', name: '自定义库', description: '', lib_type: null, file_count: 0 },
        ],
      }
    })
    renderFooter()
    await screen.findByText('自定义库')

    fireEvent.click(screen.getByTestId('baselib-delete-lib2'))
    await screen.findByTestId('baselib-delete-modal')
    fireEvent.click(screen.getByTestId('baselib-delete-confirm'))
    await flush()

    // res.code → apiErrorText → errors.codes 键（i18n mock 直出 key）
    expect(toastMock).toHaveBeenCalledWith('errors.codes.baseLibHasActiveWork', 'error')
    // 弹窗保留供重试
    expect(screen.getByTestId('baselib-delete-modal')).toBeTruthy()
  })

  it('删除网络失败（reject）→ 通用错误 toast，弹窗保留可重试', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'baseLib:delete') throw new Error('network down')
      return {
        base_libs: [
          { id: 'lib1', name: '共享资料库', description: '', lib_type: 'preset_recruitment', file_count: 0 },
          { id: 'lib2', name: '自定义库', description: '', lib_type: null, file_count: 0 },
        ],
      }
    })
    renderFooter()
    await screen.findByText('自定义库')

    fireEvent.click(screen.getByTestId('baselib-delete-lib2'))
    await screen.findByTestId('baselib-delete-modal')
    fireEvent.click(screen.getByTestId('baselib-delete-confirm'))
    await flush()

    // 非结构化失败无 code：走 baseLib.deleteFailed 通用文案
    expect(toastMock).toHaveBeenCalledWith('baseLib.deleteFailed', 'error')
    expect(screen.getByTestId('baselib-delete-modal')).toBeTruthy()
  })
})
