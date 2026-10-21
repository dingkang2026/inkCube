/** tests/FormTemplatePanel.test.tsx — 全局「填表模板」分区回归（2.4 §5.1 全局页）
 *  心跳驱动轮询（useActivity('form_template') 派生，§5.4.8）+ 边沿补拉；
 *  上传前置校验（非 .docx 拒绝）；重解析补充说明必填（§2.3，usePrompt）；
 *  删除成功 → 成功提示 + 列表刷新（FK 已摘除，2.4 不变量 11）；查看抽屉按需拉取详情渲染字段清单。
 *  PromptDialog/Toast/ConfirmDialog 打桩（同 ReportsTab.test 约定）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const activityState = { items: [] as Array<{ id?: string }> }
const toastMock = vi.fn()
const promptMock = vi.fn()

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

vi.mock('../src/components/PromptDialog', () => ({
  usePrompt: () => promptMock,
}))

vi.mock('../src/features/project/tabs/FormTemplatePanel.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { FormTemplatePanel } from '../src/features/project/tabs/FormTemplatePanel'

const invokeMock = vi.fn()

const listInvokeCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'form-template:list').length

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const presetTpl = {
  id: 'form_tpl_investment_recommend',
  name: '投资推荐项目情况表',
  source: 'preset',
  status: 'success',
  error: null,
  field_count: 38,
  user_hint: null,
  created_at: '2026-10-13T08:00:00Z',
  updated_at: '2026-10-13T08:01:00Z',
}

const uploadingTpl = {
  id: 'tplU',
  name: 'XX尽调清单',
  source: 'upload',
  status: 'analyzing',
  error: null,
  field_count: null,
  user_hint: '融资情况是单选',
  created_at: '2026-10-14T09:00:00Z',
  updated_at: '2026-10-14T09:00:30Z',
}

beforeEach(() => {
  vi.useFakeTimers()
  activityState.items = []
  toastMock.mockReset()
  promptMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'form-template:list') return { templates: [presetTpl, uploadingTpl] }
    if (channel === 'form-template:detail') {
      return {
        template: {
          ...presetTpl,
          template_schema: {
            fields: [
              { field_key: 'k1', label: '项目名称', fill_type: 'text', target: 'A1', confidence: 'high', kb_query: ['项目名称'] },
              { field_key: 'k2', label: '融资情况', fill_type: 'choice', target: 'B2', fill_mode: 'single', confidence: 'medium' },
            ],
            unmapped_cells: [],
          },
          llm_mapping: {},
        },
      }
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

describe('FormTemplatePanel 心跳驱动轮询', () => {
  it('挂载拉取模板列表，渲染行（名称/字段数/状态标签）', async () => {
    render(<FormTemplatePanel />)
    await flush()
    expect(listInvokeCount()).toBe(1)
    expect(screen.getByText('投资推荐项目情况表')).toBeTruthy()
    expect(screen.getByText('XX尽调清单')).toBeTruthy()
    expect(screen.getByText('38')).toBeTruthy()
    expect(screen.getAllByText('project.forms.tpl.statusSuccess').length).toBeGreaterThan(0)
    expect(screen.getAllByText('project.forms.tpl.statusAnalyzing').length).toBeGreaterThan(0)
  })

  it('form_template 活动条目驱动轮询；清空后边沿补拉恰一次', async () => {
    const { rerender } = render(<FormTemplatePanel />)
    await flush()
    expect(listInvokeCount()).toBe(1)

    activityState.items = [{ id: 'tplU' }]
    rerender(<FormTemplatePanel />)
    await flush()
    expect(listInvokeCount()).toBe(2)

    activityState.items = []
    rerender(<FormTemplatePanel />)
    expect(listInvokeCount()).toBe(3)

    await flush()
    expect(listInvokeCount()).toBe(3)
  })
})

describe('FormTemplatePanel 上传', () => {
  it('非 .docx 文件前置拒绝，不发起 upload', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-template:list') return { templates: [] }
      if (channel === 'dialog:openFile') return { canceled: false, filePaths: ['D:/notes.txt'] }
      return {}
    })
    render(<FormTemplatePanel />)
    await flush()
    fireEvent.click(screen.getByText('project.forms.tpl.upload'))
    await flush()
    expect(toastMock).toHaveBeenCalledWith('project.forms.tpl.uploadInvalid', 'error')
    expect(invokeMock).not.toHaveBeenCalledWith('form-template:upload', expect.anything())
  })

  it('.docx 上传成功 → upload 调用 + 入队提示 + 刷新', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-template:list') return { templates: [] }
      if (channel === 'dialog:openFile') return { canceled: false, filePaths: ['D:/表单.docx'] }
      if (channel === 'form-template:upload') return { template_id: 'tnew', status: 'pending' }
      return {}
    })
    render(<FormTemplatePanel />)
    await flush()
    fireEvent.click(screen.getByText('project.forms.tpl.upload'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('form-template:upload', { filePath: 'D:/表单.docx' })
    expect(toastMock).toHaveBeenCalledWith('project.forms.tpl.uploadQueued', 'success')
    // 上传后刷新列表
    expect(listInvokeCount()).toBe(2)
  })
})

describe('FormTemplatePanel 重解析与删除', () => {
  it('重解析补充说明必填：空输入提示且不发起', async () => {
    promptMock.mockResolvedValue('   ')
    render(<FormTemplatePanel />)
    await flush()
    fireEvent.click(screen.getAllByTitle('project.forms.tpl.reanalyze')[0])
    await flush()
    expect(promptMock).toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith('project.forms.tpl.reanalyzeRequired', 'error')
    expect(invokeMock).not.toHaveBeenCalledWith('form-template:reanalyze', expect.anything())
  })

  it('重解析：携带 userHint 上抛，成功后刷新列表', async () => {
    promptMock.mockResolvedValue('融资情况是单选')
    render(<FormTemplatePanel />)
    await flush()
    fireEvent.click(screen.getAllByTitle('project.forms.tpl.reanalyze')[0])
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('form-template:reanalyze', {
      templateId: 'form_tpl_investment_recommend',
      userHint: '融资情况是单选',
    })
    expect(listInvokeCount()).toBe(2)
  })

  it('删除成功 → 成功提示 + 列表刷新（FK 已摘除，被引用亦可删）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-template:list') return { templates: [presetTpl] }
      if (channel === 'form-template:delete') return { success: true }
      return {}
    })
    render(<FormTemplatePanel />)
    await flush()
    fireEvent.click(screen.getByTitle('project.forms.tpl.delete'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('form-template:delete', 'form_tpl_investment_recommend')
    expect(toastMock).toHaveBeenCalledWith('project.forms.tpl.deleteSuccess', 'success')
  })
})

describe('FormTemplatePanel 查看抽屉', () => {
  it('点击行 → form-template:detail → 字段清单渲染（label/类型/置信度/检索词）', async () => {
    render(<FormTemplatePanel />)
    await flush()
    fireEvent.click(screen.getByText('投资推荐项目情况表'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('form-template:detail', 'form_tpl_investment_recommend')
    expect(screen.getByText('项目名称')).toBeTruthy()
    expect(screen.getByText('text')).toBeTruthy()
    expect(screen.getByText('choice · single')).toBeTruthy()
    expect(screen.getAllByText('project.forms.tpl.confHigh').length).toBeGreaterThan(0)
    expect(screen.getAllByText('project.forms.tpl.confMedium').length).toBeGreaterThan(0)
    // 全部表格区域已识别 → unmapped 空态
    expect(screen.getByText('project.forms.tpl.unmappedEmpty')).toBeTruthy()
    // user_hint 有值才显示补充说明块（预置模板无 → 不出现标题）
    expect(screen.queryByText('project.forms.tpl.hintLabel')).toBeNull()
  })

  it('json Tab 展示 TemplateSchema 原文，切回字段清单恢复', async () => {
    render(<FormTemplatePanel />)
    await flush()
    fireEvent.click(screen.getByText('投资推荐项目情况表'))
    await flush()

    fireEvent.click(screen.getByText('project.forms.tpl.jsonTab'))
    expect(screen.getByText(/"field_key"/)).toBeTruthy()
    expect(screen.queryByText('项目名称')).toBeNull()

    fireEvent.click(screen.getByText('project.forms.tpl.fieldsTab'))
    expect(screen.getByText('项目名称')).toBeTruthy()
  })
})

describe('FormTemplatePanel 文件变更提示', () => {
  it('文件已变更的 success 行显示橙字提示（引导重解析）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'form-template:list') {
        return { templates: [{ ...presetTpl, file_changed: true }] }
      }
      return {}
    })
    render(<FormTemplatePanel />)
    await flush()
    expect(screen.getByText('project.forms.tpl.changedHint')).toBeTruthy()
    expect(screen.getByTitle('project.forms.tpl.changedTitle')).toBeTruthy()
  })

  it('未变更模板不出现变更提示', async () => {
    render(<FormTemplatePanel />)
    await flush()
    expect(screen.queryByText('project.forms.tpl.changedHint')).toBeNull()
  })
})
