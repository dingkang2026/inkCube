/** tests/PptWizard.test.tsx — PPT 创建向导（2.3 §7.1 PptCreateDialog）
 *  覆盖：挂载拉取（报告仅 done 作内容源 / 图片 status=done 素材池 + 分类计数 /
 *  模板双源列表 ppt-template:list，内置徽标拼 option）、空标题禁提交、
 *  载荷归一（trim / 空转 null / 复合选择 imageSelection / 模板选择 templateRefs
 *  串 kind:id，全不选 = null）上抛 onCreated、
 *  服务端 error（含 INVALID_TEMPLATE_REF 错误码映射）与网络异常双失败路径。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const toastMock = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // 带插值形态：外层 toast 文案保留 error 槽内容，断言可验证错误码映射接线
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && typeof opts.error === 'string' ? `${key}[${opts.error}]` : key,
  }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

vi.mock('../src/features/project/tabs/ppt.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { PptWizard } from '../src/features/project/tabs/PptWizard'

const invokeMock = vi.fn()

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const fillTitle = (value: string) =>
  fireEvent.change(screen.getByPlaceholderText('project.ppt.wizard.titlePlaceholder'), {
    target: { value },
  })

beforeEach(() => {
  toastMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'reports:listReports') {
      return {
        reports: [
          { id: 'r1', title: '已完成报告', status: 'done' },
          { id: 'r2', title: '生成中报告', status: 'generating' },
        ],
      }
    }
    if (channel === 'knowledge:getProjectImages') {
      return {
        images: [
          { id: 'img1', local_path: 'D:/pics/a.png', vlm_description: '产品图', category: 'chart' },
          { id: 'img2', local_path: null, vlm_description: null, category: 'photo' },
        ],
        category_counts: { chart: 1, photo: 1 },
      }
    }
    if (channel === 'ppt-template:list') {
      return {
        templates: {
          brand: [{ id: 'b1', summary: '品牌蓝', builtin: true }],
          style: [
            { id: 'builtin_s', summary: '内置风', builtin: true },
            { id: 'custom_s', summary: '自建风', builtin: false },
          ],
          layout: [{ id: 'l1', summary: '版式', builtin: true }],
          deck: [],
        },
      }
    }
    if (channel === 'ppt:create') return { ppt: { id: 'pnew' } }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('PptWizard', () => {
  it('挂载拉取报告/图片/模板双源列表；报告仅 done 进内容源下拉', async () => {
    render(<PptWizard projectId="p1" onCreated={() => {}} onBack={() => {}} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith(
      'reports:listReports', { projectId: 'p1', limit: 0 })
    expect(invokeMock).toHaveBeenCalledWith('knowledge:getProjectImages', {
      projectId: 'p1',
      limit: 200,
      status: 'done',
    })
    expect(invokeMock).toHaveBeenCalledWith('ppt-template:list')
    // 下拉序 = DOM 序：内容源 + 四类模板（每类恒渲染，空类只剩占位项）
    const combos = screen.getAllByRole('combobox') as HTMLSelectElement[]
    expect(combos).toHaveLength(5)
    expect(Array.from(combos[0].options).map((o) => o.textContent))
      .toEqual(['project.ppt.wizard.sourceNone', '已完成报告'])
    // 双源条目：内置带徽标、用户库不带（E2 库外置列表形态）
    expect(Array.from(combos[1].options).map((o) => o.textContent)).toEqual([
      'project.ppt.wizard.templateNone',
      'b1 · project.ppt.wizard.builtinBadge · 品牌蓝',
    ])
    expect(Array.from(combos[2].options).map((o) => o.textContent)).toEqual([
      'project.ppt.wizard.templateNone',
      'builtin_s · project.ppt.wizard.builtinBadge · 内置风',
      'custom_s · 自建风',
    ])
  })

  it('空标题时提交禁用；填标题后可用', async () => {
    render(<PptWizard projectId="p1" onCreated={() => {}} onBack={() => {}} />)
    await flush()
    const submit = screen.getByText('project.ppt.wizard.submit')
      .closest('button') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    fillTitle('季度汇报')
    expect(submit.disabled).toBe(false)
  })

  it('载荷归一（trim/空转 null/钉选/模板选择 → templateRefs 串）并上抛 onCreated', async () => {
    const onCreated = vi.fn()
    render(<PptWizard projectId="p1" onCreated={onCreated} onBack={() => {}} />)
    await flush()
    fillTitle('  季度汇报  ')
    const combos = screen.getAllByRole('combobox') as HTMLSelectElement[]
    fireEvent.change(combos[0], { target: { value: 'r1' } })
    fireEvent.change(combos[2], { target: { value: 'custom_s' } }) // 风格行选用户库模板
    fireEvent.click(screen.getByTitle('产品图'))
    fireEvent.change(screen.getByPlaceholderText('project.ppt.wizard.stylePlaceholder'), {
      target: { value: '深蓝商务' },
    })
    fireEvent.change(screen.getByPlaceholderText('project.ppt.wizard.notesPlaceholder'), {
      target: { value: '重点突出成本' },
    })
    fireEvent.click(screen.getByText('project.ppt.wizard.submit'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('ppt:create', {
      projectId: 'p1',
      title: '季度汇报',
      sourceReportId: 'r1',
      imageSelection: { categories: [], minWidth: null, pinnedIds: ['img1'] },
      templateRefs: 'style:custom_s',
      styleRef: '深蓝商务',
      notes: '重点突出成本',
    })
    expect(onCreated).toHaveBeenCalledWith('pnew')
    expect(toastMock).toHaveBeenCalledWith('project.ppt.toast.created', 'success')
  })

  it('全不选模板 → templateRefs null（默认 Stage 1 内置候选自主决定）', async () => {
    render(<PptWizard projectId="p1" onCreated={() => {}} onBack={() => {}} />)
    await flush()
    fillTitle('X')
    fireEvent.click(screen.getByText('project.ppt.wizard.submit'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('ppt:create', expect.objectContaining({
      templateRefs: null,
    }))
  })

  it('复合选择：分类圈池 + 高清阈值 + 钉选三者合成 imageSelection；再点钉选取消后仍非空', async () => {
    render(<PptWizard projectId="p1" onCreated={() => {}} onBack={() => {}} />)
    await flush()
    fillTitle('复合选择')
    // 分类 chip（i18n mock 返回键名）+ 高清开关 + 钉选 img1、img2
    fireEvent.click(screen.getByText('knowledge.category_chart (1)'))
    fireEvent.click(screen.getByText('knowledge.category_photo (1)'))
    const hd = screen
      .getByText('project.ppt.wizard.hdOnly')
      .closest('label')!
      .querySelector('input') as HTMLInputElement
    fireEvent.click(hd)
    fireEvent.click(screen.getByTitle('产品图'))
    fireEvent.click(screen.getByTitle('产品图')) // 再点取消钉选
    fireEvent.click(screen.getByText('project.ppt.wizard.submit'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('ppt:create', expect.objectContaining({
      imageSelection: {
        categories: ['chart', 'photo'],
        minWidth: 800,
        pinnedIds: [],
      },
    }))
  })

  it('INVALID_TEMPLATE_REF → 错误码映射文案上 toast（列表过期兜底链路）', async () => {
    // 模板在列表加载后被删：提交仍带旧选择，后端 400 拦截 → 前端按 code 映射
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listReports') return { reports: [] }
      if (channel === 'knowledge:getProjectImages') return { images: [] }
      if (channel === 'ppt-template:list') {
        return { templates: { brand: [{ id: 'gone', summary: 'x', builtin: false }] } }
      }
      if (channel === 'ppt:create') {
        return { error: '模板不存在或已删除：brand:gone', code: 'INVALID_TEMPLATE_REF' }
      }
      return {}
    })
    render(<PptWizard projectId="p1" onCreated={() => {}} onBack={() => {}} />)
    await flush()
    fillTitle('X')
    const combos = screen.getAllByRole('combobox') as HTMLSelectElement[]
    fireEvent.change(combos[1], { target: { value: 'gone' } })
    fireEvent.click(screen.getByText('project.ppt.wizard.submit'))
    await flush()

    // toast 文案经 apiErrorText → errors.codes.pptInvalidTemplateRef 映射
    expect(toastMock.mock.calls[0][0]).toContain('errors.codes.pptInvalidTemplateRef')
    expect(toastMock.mock.calls[0][1]).toBe('error')
  })

  it('服务端 error → toast 失败且不上抛 onCreated', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listReports') return { reports: [] }
      if (channel === 'knowledge:getProjectImages') return { images: [] }
      if (channel === 'ppt:create') return { error: '已达并发上限', code: 'TASK_LIMIT_REACHED' }
      return {}
    })
    const onCreated = vi.fn()
    render(<PptWizard projectId="p1" onCreated={onCreated} onBack={() => {}} />)
    await flush()
    fillTitle('X')
    fireEvent.click(screen.getByText('project.ppt.wizard.submit'))
    await flush()

    expect(onCreated).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledTimes(1)
    expect(toastMock.mock.calls[0][0]).toContain('project.ppt.toast.createFailed')
    expect(toastMock.mock.calls[0][1]).toBe('error')
  })

  it('网络异常 → toast 失败且提交态复位（按钮恢复可点）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listReports') return { reports: [] }
      if (channel === 'knowledge:getProjectImages') return { images: [] }
      if (channel === 'ppt:create') throw new Error('ipc down')
      return {}
    })
    render(<PptWizard projectId="p1" onCreated={() => {}} onBack={() => {}} />)
    await flush()
    fillTitle('X')
    fireEvent.click(screen.getByText('project.ppt.wizard.submit'))
    await flush()

    expect(toastMock).toHaveBeenCalledTimes(1)
    expect(toastMock.mock.calls[0][1]).toBe('error')
    const submit = screen.getByText('project.ppt.wizard.submit')
      .closest('button') as HTMLButtonElement
    expect(submit.disabled).toBe(false)
  })
})
