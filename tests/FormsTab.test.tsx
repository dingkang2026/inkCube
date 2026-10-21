/** tests/FormsTab.test.tsx — 填表实例列表 + 心跳轮询 + preview 修订链路（2.4 §5.1/§5.2）
 *  轮询谓词从 useActivity('form_fill') 派生（§5.4.8 纪律），终态边沿补拉恰一次
 *  （0.4 §6.3，同 ReportsTab.test 约定）；preview 打开 → form-fill:detail →
 *  FormSchemaForm 桩接线（schema/values/report）；编辑置 dirty → 保存上抛
 *  form-fill:update（值集原样）；failed 实例打开显示 error + retryFill 重发起
 *  （form-fill:create 同模板同标题）；发起弹窗仅 success 模板可选。
 *  子组件打桩隔离（同 ReportsTab.test 约定）；fake timers + flush 排空 microtask。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const activityState = { items: [] as Array<{ project_id?: string }> }
const toastMock = vi.fn()

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => activityState,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('../src/features/project/tabs/FormsTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

// FormSchemaForm 打桩：暴露 props 接线 + 一枚编辑按钮驱动 dirty
vi.mock('../src/features/project/tabs/FormSchemaForm', async () => {
  const { createElement } = await import('react')
  return {
    FormSchemaForm: (props: any) =>
      createElement(
        'div',
        {
          'data-testid': 'schema-form',
          'data-schema-fields': props.schema?.fields?.length ?? 0,
          'data-values': JSON.stringify(props.values),
          'data-report': JSON.stringify(props.report ?? []),
        },
        createElement(
          'button',
          { 'data-testid': 'schema-edit', onClick: () => props.onChange('proj_name', 'EDITED') },
          'edit',
        ),
      ),
  }
})

import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { FormsTab } from '../src/features/project/tabs/FormsTab'

const invokeMock = vi.fn()

const listInvokeCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'form-fill:list').length

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const successFill = {
  id: 'f1',
  project_id: 'p1',
  template_id: 'tpl1',
  title: '投资推荐项目情况表 2026-10-14',
  status: 'success',
  progress: 100,
  error: null,
  created_at: '2026-10-14T10:00:00Z',
  updated_at: '2026-10-14T10:30:00Z',
  template_name: '投资推荐项目情况表',
}

const failedFill = {
  ...successFill,
  id: 'f2',
  template_id: 'tpl1',
  title: '投资推荐项目情况表 2026-10-13',
  status: 'failed',
  progress: 40,
  error: 'LLM 调用超时',
}

const fillSchema = {
  fields: [{ field_key: 'proj_name', label: '项目名称', fill_type: 'text', target: 'A1' }],
  batches: [],
}

beforeEach(() => {
  vi.useFakeTimers()
  activityState.items = []
  toastMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'form-fill:list') {
      return { fills: [successFill, failedFill] }
    }
    if (channel === 'form-fill:detail') {
      return {
        fill: { ...successFill, report: [{ field_key: 'proj_name', status: 'filled' }], filled_values: { proj_name: '墨方' } },
        schema: fillSchema,
      }
    }
    if (channel === 'form-fill:download') return { canceled: true }
    if (channel === 'form-template:list') {
      return {
        templates: [
          { id: 'tplA', name: '可用模板', status: 'success', error: null, field_count: 38 },
          { id: 'tplB', name: '分析中模板', status: 'analyzing', error: null, field_count: null },
        ],
      }
    }
    if (channel === 'form-fill:create') return { fill_id: 'fnew', status: 'pending' }
    if (channel === 'form-fill:update') {
      return { fill: { ...successFill, filled_values: { proj_name: 'EDITED' } }, report: [] }
    }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('FormsTab 心跳驱动轮询', () => {
  it('挂载拉取列表一次，心跳无活跃条目时不轮询', async () => {
    render(<FormsTab projectId="p1" />)
    await flush()
    expect(listInvokeCount()).toBe(1)
    expect(screen.getByText('投资推荐项目情况表 2026-10-14')).toBeTruthy()

    await flush()
    expect(listInvokeCount()).toBe(1)
  })

  it('本项目 form_fill 活动条目驱动轮询，其他项目条目不驱动', async () => {
    const { rerender } = render(<FormsTab projectId="p1" />)
    await flush()
    expect(listInvokeCount()).toBe(1)

    // 其他项目 → 不轮询
    activityState.items = [{ project_id: 'other' }]
    rerender(<FormsTab projectId="p1" />)
    await flush()
    expect(listInvokeCount()).toBe(1)

    // 本项目 → 立即首跑
    activityState.items = [{ project_id: 'p1' }]
    rerender(<FormsTab projectId="p1" />)
    await flush()
    expect(listInvokeCount()).toBe(2)
  })

  it('true→false 边沿补拉恰一次（终态先于快照同步）', async () => {
    const { rerender } = render(<FormsTab projectId="p1" />)
    await flush()
    activityState.items = [{ project_id: 'p1' }]
    rerender(<FormsTab projectId="p1" />)
    await flush()
    expect(listInvokeCount()).toBe(2)

    activityState.items = []
    rerender(<FormsTab projectId="p1" />)
    expect(listInvokeCount()).toBe(3)

    await flush()
    expect(listInvokeCount()).toBe(3)
  })
})

describe('FormsTab 实例卡状态', () => {
  it('filling 卡显示进度常显且打开/删除禁用；failed 卡红字角标', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-fill:list') {
        return { fills: [{ ...successFill, status: 'filling', progress: 62 }] }
      }
      return {}
    })
    render(<FormsTab projectId="p1" />)
    await flush()
    expect(screen.getByText(/project\.forms\.filling/)).toBeTruthy()
    const openBtn = screen.getByText('project.forms.open').closest('button') as HTMLButtonElement
    expect(openBtn.disabled).toBe(true)
    const delBtn = screen.getByText('project.forms.delete').closest('button') as HTMLButtonElement
    expect(delBtn.disabled).toBe(true)
  })
})

describe('FormsTab preview 修订链路', () => {
  it('success 实例打开 → detail 接线 FormSchemaForm（schema/values/report）→ 编辑置 dirty → 保存上抛 update', async () => {
    render(<FormsTab projectId="p1" />)
    await flush()
    fireEvent.click(screen.getAllByText('project.forms.open')[0])
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('form-fill:detail', 'f1')
    const form = screen.getByTestId('schema-form')
    expect(form.getAttribute('data-schema-fields')).toBe('1')
    expect(JSON.parse(form.getAttribute('data-values')!)).toEqual({ proj_name: '墨方' })
    expect(JSON.parse(form.getAttribute('data-report')!)).toEqual([{ field_key: 'proj_name', status: 'filled' }])

    // 编辑置 dirty → 保存可用
    const saveBtn = screen.getByText('project.forms.saveAndRegenerate').closest('button') as HTMLButtonElement
    expect(saveBtn.disabled).toBe(true)
    fireEvent.click(screen.getByTestId('schema-edit'))
    fireEvent.click(screen.getByText('project.forms.saveAndRegenerate'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('form-fill:update', { fillId: 'f1', values: { proj_name: 'EDITED' } })
    expect(toastMock).toHaveBeenCalledWith('project.forms.saveSuccess', 'success')
    // 保存后重拉列表（状态推进上屏）
    expect(listInvokeCount()).toBe(2)
  })

  it('failed 实例打开 → error 横幅 + retryFill 以同模板同标题重发起', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-fill:list') return { fills: [successFill, failedFill] }
      if (channel === 'form-fill:detail') {
        return {
          fill: { ...failedFill, report: null, filled_values: null },
          schema: null,
        }
      }
      return {}
    })
    render(<FormsTab projectId="p1" />)
    await flush()
    fireEvent.click(screen.getAllByText('project.forms.open')[1])
    await flush()

    expect(screen.getByText('project.forms.errorLabel')).toBeTruthy()
    expect(screen.getByText('LLM 调用超时')).toBeTruthy()
    fireEvent.click(screen.getByText('project.forms.retryFill'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('form-fill:create', {
      projectId: 'p1',
      templateId: 'tpl1',
      title: failedFill.title,
    })
    expect(toastMock).toHaveBeenCalledWith('project.forms.statusPending', 'info')
  })

  it('下载走 saveDialog 通道；取消静默返回', async () => {
    render(<FormsTab projectId="p1" />)
    await flush()
    fireEvent.click(screen.getAllByText('project.forms.download')[0])
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('form-fill:download', {
      fillId: 'f1',
      title: successFill.title,
    })
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('删除经确认后调用 form-fill:delete 并刷新', async () => {
    render(<FormsTab projectId="p1" />)
    await flush()
    fireEvent.click(screen.getAllByText('project.forms.delete')[0])
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('form-fill:delete', 'f1')
    expect(toastMock).toHaveBeenCalledWith('project.forms.deleteSuccess', 'success')
  })
})

describe('FormsTab 发起填表弹窗', () => {
  it('仅 success 模板可选；开始填写上抛 create（空标题传 null 走后端默认）', async () => {
    render(<FormsTab projectId="p1" />)
    await flush()
    fireEvent.click(screen.getByText('project.forms.newForm'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('form-template:list', { limit: 0 })
    // 分析中模板卡置灰
    const disabledCard = screen.getByText('分析中模板').closest('div') as HTMLElement
    expect(disabledCard.className).toContain('tplDisabled')
    // 可用模板卡选中（默认选中第一个 success）
    fireEvent.click(screen.getByText('可用模板'))
    fireEvent.click(screen.getByText('project.forms.createStart'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('form-fill:create', {
      projectId: 'p1',
      templateId: 'tplA',
      title: null,
    })
    expect(toastMock).not.toHaveBeenCalledWith('project.forms.createFailed', 'error')
  })

  it('文件已变更（file_changed）的 success 模板置灰且默认选中跳过', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-fill:list') return { fills: [] }
      if (channel === 'form-template:list') {
        return {
          templates: [
            { id: 'tplA', name: '可用模板', status: 'success', error: null, field_count: 38 },
            { id: 'tplC', name: '已变更模板', status: 'success', error: null, field_count: 29, file_changed: true },
          ],
        }
      }
      return {}
    })
    render(<FormsTab projectId="p1" />)
    await flush()
    fireEvent.click(screen.getByText('project.forms.newForm'))
    await flush()

    // 变更模板置灰，提示先重解析（旧 schema 填新文件会错位）
    const changedCard = screen.getByText('已变更模板').closest('div') as HTMLElement
    expect(changedCard.className).toContain('tplDisabled')
    expect(screen.getByText('project.forms.createChanged')).toBeTruthy()
    // 默认选中跳过变更模板，落在未变更的 success 上
    fireEvent.click(screen.getByText('project.forms.createStart'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('form-fill:create', {
      projectId: 'p1',
      templateId: 'tplA',
      title: null,
    })
  })

  it('无可用模板时空态文案引导', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-fill:list') return { fills: [] }
      if (channel === 'form-template:list') {
        return { templates: [{ id: 'tplB', name: 'X', status: 'analyzing', error: null, field_count: null }] }
      }
      return {}
    })
    render(<FormsTab projectId="p1" />)
    await flush()
    fireEvent.click(screen.getByText('project.forms.newForm'))
    await flush()
    expect(screen.getByText('project.forms.createNoTemplates')).toBeTruthy()
  })
})
