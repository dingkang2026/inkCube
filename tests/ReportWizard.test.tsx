/** tests/ReportWizard.test.tsx — 向导大纲分层渲染回归（2.5 §前端交互）
 *  修复前：大纲被拍平为跨章连续序号（1. 2. 3...），章标题被丢弃，
 *  与模板管理的树形结构不一致；post_body 节被隐藏（设计语义是
 *  「不检索、无开关」而非「不显示」）。
 *  修复后：与后端 get_execution_sections 镜像——有 children 的章显示
 *  组头 + 章内编号节行；无 children 的章自身即节；post_body 节展示
 *  标签替代互联网开关。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/settings/useSettings', () => ({
  // 网络搜索已选 tavily 且 Key 非空 → 互联网总闸可用（供应商化判定，2026-10-19）
  useSettings: () => ({ settings: { web_search_provider: 'tavily', tavily_key: 'tv-key', zhipu_search_key: '' } }),
}))

// ReportWizard 消费 useToast（必填变量校验提示）；无 Provider 会 throw，统一 mock 捕获调用
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

vi.mock('../src/features/readiness/useReadiness', () => {
  // guard 必须跨渲染引用稳定（真实实现是 useCallback 包装），
  // 否则 handleStartGenerate 每次重建，stale closure 无法复现
  const guard = (_feature: string, fn: () => void) => fn()
  return { useFeatureGuard: () => guard }
})

vi.mock('../src/features/project/tabs/ReportWizard.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { ReportWizard } from '../src/features/project/tabs/ReportWizard'

const invokeMock = vi.fn()

/** 模拟尽职调查类结构：ch1 无 children（自身即节）；ch2 有两个 children
 *  （ch2.2 为 post_body）。 */
const OUTLINE_MIXED = [
  { section_id: 'ch1', title: '第一章 执行摘要' },
  {
    section_id: 'ch2',
    title: '第二章 企业基本情况',
    children: [
      { section_id: 'ch2.1', title: '基本信息核查' },
      { section_id: 'ch2.2', title: '综述与建议', compose_mode: 'post_body' },
    ],
  },
]

const OUTLINE_FLAT = [
  { section_id: 'chA', title: '章甲' },
  { section_id: 'chB', title: '章乙' },
]

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

async function openWizardWithOutline(outline: unknown[]) {
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'reports:listTemplates') {
      return {
        templates: [{
          template_id: 'dd',
          template_name: '尽职调查报告',
          version: '2.0',
          project_types: ['investment'],
          stage: 'during',
          output_format: 'docx',
          report_depth: 'deep',
          chapter_count: outline.length,
          is_custom: false,
        }],
      }
    }
    if (channel === 'reports:getTemplate') {
      // template_id/template_name 供 doStartGenerate 提交 payload 使用
      return { template: { outline, variables: [], template_id: 'dd', template_name: '尽职调查报告' } }
    }
    if (channel === 'baseLib:list') return { base_libs: [] }
    if (channel === 'project:getBaseLibs') return { base_libs: [] }
    return {}
  })
  render(
    <ReportWizard projectId="p1" projectType="investment" onComplete={() => {}} onCancel={() => {}} />,
  )
  await flush()
  fireEvent.click(screen.getByText('尽职调查报告'))
  await flush()
}

beforeEach(() => {
  invokeMock.mockReset()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('ReportWizard outline rendering (chapter-grouped)', () => {
  it('renders chapter header with chapter-scoped numbering for chapters with children', async () => {
    await openWizardWithOutline(OUTLINE_MIXED)
    // 有 children 的章：组头（章标题）+ 章内编号节行
    expect(screen.getByText('第二章 企业基本情况')).toBeTruthy()
    expect(screen.getByText('2.1')).toBeTruthy()
    expect(screen.getByText('2.2')).toBeTruthy()
    // 无 children 的章：章自身即节行（章序号）
    expect(screen.getByText('第一章 执行摘要')).toBeTruthy()
    expect(screen.getByText('1.')).toBeTruthy()
  })

  it('no longer flattens child sections into cross-chapter sequential numbers', async () => {
    await openWizardWithOutline(OUTLINE_MIXED)
    // 旧实现：ch2.1/ch2.2 平铺为跨章连续序号 2. / 3.——修复后必须不存在
    expect(screen.queryByText('2.')).toBeNull()
    expect(screen.queryByText('3.')).toBeNull()
  })

  it('shows post_body sections with a tag instead of an internet toggle', async () => {
    await openWizardWithOutline(OUTLINE_MIXED)
    expect(screen.getByText('project.report.wizard.postBody')).toBeTruthy()
    // post_body 节行内无互联网开关；standard 节行内才有
    const row = screen.getByText('project.report.wizard.postBody').closest('div')
    expect(row?.querySelector('input[type="checkbox"]')).toBeNull()
    // checkbox 总数：知识源 3 + master 总开关 1 + standard 节开关 2（ch1、ch2.1）
    expect(screen.getAllByRole('checkbox')).toHaveLength(6)
  })

  it('renders chapters without children as flat single-line sections', async () => {
    await openWizardWithOutline(OUTLINE_FLAT)
    expect(screen.getByText('章甲')).toBeTruthy()
    expect(screen.getByText('章乙')).toBeTruthy()
    expect(screen.getByText('1.')).toBeTruthy()
    expect(screen.getByText('2.')).toBeTruthy()
    expect(screen.queryByText('1.1')).toBeNull()
  })
})

describe('ReportWizard internet toggle submission (stale closure regression)', () => {
  it('submits internet toggles set AFTER mount, not values captured at mount', async () => {
    await openWizardWithOutline(OUTLINE_MIXED)

    // 用户操作：勾选总闸 + 仅勾选 ch2.1 的逐节开关（ch1 不勾，验证逐节选择保真）
    fireEvent.click(
      screen.getByText('project.report.wizard.masterInternet').closest('label')!
        .querySelector('input[type="checkbox"]')!,
    )
    fireEvent.click(
      screen.getByText('2.1').closest('div')!.querySelector('input[type="checkbox"]')!,
    )

    // 生成请求 mock：记录提交的 payload
    let submitted: Record<string, unknown> | undefined
    invokeMock.mockImplementation(async (channel: string, args?: unknown) => {
      if (channel === 'reports:generateReport') {
        submitted = args as Record<string, unknown>
        return { report_id: 'r-new' }
      }
      if (channel === 'reports:listTemplates') {
        return { templates: [{
          template_id: 'dd', template_name: '尽职调查报告', version: '2.0',
          project_types: ['investment'], stage: 'during', output_format: 'docx',
          report_depth: 'deep', chapter_count: OUTLINE_MIXED.length, is_custom: false,
        }] }
      }
      if (channel === 'reports:getTemplate') {
        return { template: { outline: OUTLINE_MIXED, variables: [], template_id: 'dd', template_name: '尽职调查报告' } }
      }
      if (channel === 'baseLib:list') return { base_libs: [] }
      if (channel === 'project:getBaseLibs') return { base_libs: [] }
      return {}
    })

    fireEvent.click(screen.getByText('project.report.wizard.startGenerate').closest('button')!)
    await flush()

    // 修复前：handleStartGenerate 的 useCallback 依赖缺 doStartGenerate，
    // 勾选后 doStartGenerate 重建（捕获新值），但 handleStartGenerate 保留
    // 旧闭包 → 提交 internetEnabled=false / sectionInternet={}（UI 显示与
    // 提交不一致，DB 落库 0）。修复后必须提交当前勾选值。
    expect(submitted).toBeTruthy()
    expect(submitted!.internetEnabled).toBe(true)
    expect(submitted!.sectionInternet).toEqual({ 'ch2.1': true })
    expect(submitted!.templateId).toBe('dd')
  })

  it('submits empty internet config when master toggle stays off', async () => {
    await openWizardWithOutline(OUTLINE_MIXED)
    let submitted: Record<string, unknown> | undefined
    invokeMock.mockImplementation(async (channel: string, args?: unknown) => {
      if (channel === 'reports:generateReport') {
        submitted = args as Record<string, unknown>
        return { report_id: 'r-new' }
      }
      if (channel === 'reports:listTemplates') {
        return { templates: [{
          template_id: 'dd', template_name: '尽职调查报告', version: '2.0',
          project_types: ['investment'], stage: 'during', output_format: 'docx',
          report_depth: 'deep', chapter_count: OUTLINE_MIXED.length, is_custom: false,
        }] }
      }
      if (channel === 'reports:getTemplate') {
        return { template: { outline: OUTLINE_MIXED, variables: [], template_id: 'dd', template_name: '尽职调查报告' } }
      }
      if (channel === 'baseLib:list') return { base_libs: [] }
      if (channel === 'project:getBaseLibs') return { base_libs: [] }
      return {}
    })

    fireEvent.click(screen.getByText('project.report.wizard.startGenerate').closest('button')!)
    await flush()

    expect(submitted).toBeTruthy()
    expect(submitted!.internetEnabled).toBe(false)
    expect(submitted!.sectionInternet).toEqual({})
  })
})
