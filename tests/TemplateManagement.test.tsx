/**
 * TemplateManagement 刷新模板入口（2.5 §2.5）：
 * 筛选栏尾部「刷新」按钮 → reports:refreshTemplates 强制重载预置 + 自定义模板
 * （POST /reports/refresh-templates，无参数）→ 重拉 reports:listTemplates。
 * 成功 toast 报告总数；期间按钮禁用旋转；失败 toast 报错。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const { toastMock, tMock } = vi.hoisted(() => ({
  toastMock: vi.fn(),
  tMock: vi.fn((key: string) => key),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('../src/features/project/tabs/TemplateManagement.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/features/project/tabs/TemplateEditor', () => ({
  TemplateEditor: () => null,
}))

vi.mock('../src/features/project/tabs/FormTemplatePanel', () => ({
  FormTemplatePanel: () => null,
}))

// PPT 模板 Tab 活跃指示（2.3 §4.2）引入的 useActivity：本文件只覆盖刷新链路，给稳定空视图即可
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ items: [], isActive: false }),
}))

import { render, cleanup, act, screen, fireEvent } from '@testing-library/react'
import { TemplateManagement } from '../src/features/project/tabs/TemplateManagement'

const invokeMock = vi.fn()

const TEMPLATES = [
  {
    template_id: 'p1', template_name: 'Preset A', version: '2.0', project_types: ['general'],
    stage: 'before', output_format: 'docx', chapter_count: 3, is_custom: false,
  },
  {
    template_id: 'c1', template_name: 'Custom B', version: '1.0', project_types: ['general'],
    stage: null, output_format: 'docx', chapter_count: 2, is_custom: true,
    updated_at: '2026-10-15T00:00:00Z',
  },
]

/** 排空 microtask（handleRefreshTemplates / loadTemplates 均为 async 链） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const listTemplatesCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'reports:listTemplates').length

beforeEach(() => {
  invokeMock.mockReset()
  toastMock.mockReset()
  tMock.mockClear()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'reports:listTemplates') return { templates: TEMPLATES }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('TemplateManagement refresh templates', () => {
  it('invokes refreshTemplates then re-pulls the list', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listTemplates') return { templates: TEMPLATES }
      if (channel === 'reports:refreshTemplates')
        return { success: true, preset_count: 1, custom_count: 1, total: 2 }
      return {}
    })
    render(<TemplateManagement />)
    await flush()
    expect(listTemplatesCount()).toBe(1)

    fireEvent.click(screen.getByTitle('project.template.refreshHint'))
    await flush()

    expect(invokeMock.mock.calls.filter((c) => c[0] === 'reports:refreshTemplates')).toHaveLength(1)
    expect(listTemplatesCount()).toBe(2)
  })

  it('shows a success toast with the total count', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listTemplates') return { templates: TEMPLATES }
      if (channel === 'reports:refreshTemplates')
        return { success: true, preset_count: 2, custom_count: 1, total: 3 }
      return {}
    })
    render(<TemplateManagement />)
    await flush()
    fireEvent.click(screen.getByTitle('project.template.refreshHint'))
    await flush()

    // mock t 直接返回 key，故分开验证：total 传入 t（插值参数）+ toast 类型
    expect(tMock).toHaveBeenCalledWith('project.template.refreshDone', { total: 3 })
    expect(toastMock).toHaveBeenCalledWith('project.template.refreshDone', 'success')
  })

  it('shows an error toast and keeps the list when refresh fails', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listTemplates') return { templates: TEMPLATES }
      if (channel === 'reports:refreshTemplates') return Promise.reject(new Error('boom'))
      return {}
    })
    render(<TemplateManagement />)
    await flush()
    expect(listTemplatesCount()).toBe(1)

    fireEvent.click(screen.getByTitle('project.template.refreshHint'))
    await flush()

    expect(toastMock).toHaveBeenCalledWith('project.template.refreshFailed', 'error')
    // 失败不触发重拉（listTemplates 仍只有挂载时的 1 次）
    expect(listTemplatesCount()).toBe(1)
  })

  it('disables the button while refreshing and re-enables after completion', async () => {
    let resolveRefresh!: (v: unknown) => void
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listTemplates') return { templates: TEMPLATES }
      if (channel === 'reports:refreshTemplates')
        return new Promise((resolve) => { resolveRefresh = resolve })
      return {}
    })
    render(<TemplateManagement />)
    await flush()

    const btn = screen.getByTitle('project.template.refreshHint') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    await act(async () => { await Promise.resolve() })
    expect(btn.disabled).toBe(true)

    await act(async () => {
      resolveRefresh({ success: true, preset_count: 1, custom_count: 1, total: 2 })
    })
    await flush()
    expect((screen.getByTitle('project.template.refreshHint') as HTMLButtonElement).disabled).toBe(false)
  })
})
