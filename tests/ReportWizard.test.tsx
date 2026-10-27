/** tests/ReportWizard.test.tsx — 向导大纲分层渲染 + 知识源动态拼接回归
 *  大纲（2.5 §前端交互）修复前：大纲被拍平为跨章连续序号（1. 2. 3...），
 *  章标题被丢弃，与模板管理的树形结构不一致；post_body 节被隐藏（设计
 *  语义是「不检索、无开关」而非「不显示」）。修复后：与后端
 *  get_execution_sections 镜像——有 children 的章显示组头 + 章内编号节行；
 *  无 children 的章自身即节；post_body 节展示标签替代互联网开关。
 *  知识源（2.7 §3.1 / §5.5，2.9 D3 收敛后随自定义库重建恢复）：静态三源
 *  + 动态自定义库拼接；预勾以关联集为准；勾选提交回写 baseProjectIds。
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

/** 打开向导并注入自定义库与关联集（2.7 §3.1 知识源拼接用例入口） */
async function openWizardWithSources(
  customLibs: Array<{ id: string; name: string; lib_type: string | null }>,
  linkedIds: string[],
) {
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'reports:listTemplates') {
      return {
        templates: [{
          template_id: 'dd', template_name: '尽职调查报告', version: '2.0',
          project_types: ['investment'], stage: 'during', output_format: 'docx',
          report_depth: 'deep', chapter_count: OUTLINE_FLAT.length, is_custom: false,
        }],
      }
    }
    if (channel === 'reports:getTemplate') {
      return { template: { outline: OUTLINE_FLAT, variables: [], template_id: 'dd', template_name: '尽职调查报告' } }
    }
    if (channel === 'baseLib:list') return { base_libs: customLibs }
    if (channel === 'project:getBaseLibs') {
      return { base_libs: linkedIds.map((id) => ({ id })) }
    }
    return {}
  })
  render(
    <ReportWizard projectId="p1" projectType="investment" onComplete={() => {}} onCancel={() => {}} />,
  )
  await flush()
  fireEvent.click(screen.getByText('尽职调查报告'))
  await flush()
}

describe('知识源动态拼接（2.7 §3.1，自定义库恢复）', () => {
  it('自定义库出现为动态条目；预置库不重复出现（仅静态条目）', async () => {
    await openWizardWithSources([
      { id: 'bl-c1', name: '半导体产业情报', lib_type: 'custom' },
      { id: 'bl-recruitment', name: '招商基础库', lib_type: 'preset_recruitment' },
    ], [])

    // 动态条目直用库名；预置库走静态 i18n key（过滤后的预置行不再以库名出现）
    expect(screen.getByText('半导体产业情报')).toBeTruthy()
    expect(screen.getByText('project.report.wizard.recruitmentLib')).toBeTruthy()
    expect(screen.queryByText('招商基础库')).toBeNull()
  })

  it('关联集命中的自定义库预勾，未关联的不预勾；项目库恒选', async () => {
    await openWizardWithSources([
      { id: 'bl-c1', name: '半导体产业情报', lib_type: 'custom' },
      { id: 'bl-c2', name: '另一库', lib_type: 'custom' },
    ], ['bl-c1'])

    const checkedRow = screen.getByText('半导体产业情报').closest('label')!
    expect((checkedRow.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(true)
    const uncheckedRow = screen.getByText('另一库').closest('label')!
    expect((uncheckedRow.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false)
  })

  it('勾选自定义库生成 → baseProjectIds 携带库 id（检索与关联回写同源）', async () => {
    await openWizardWithSources([
      { id: 'bl-c1', name: '半导体产业情报', lib_type: 'custom' },
    ], ['bl-c1'])

    let submitted: Record<string, unknown> | undefined
    invokeMock.mockImplementation(async (channel: string, args?: unknown) => {
      if (channel === 'reports:generateReport') {
        submitted = args as Record<string, unknown>
        return { report_id: 'r-new' }
      }
      if (channel === 'project:getBaseLibs') return { base_libs: [{ id: 'bl-c1' }] }
      return {}
    })

    fireEvent.click(screen.getByText('project.report.wizard.startGenerate').closest('button')!)
    await flush()

    expect(submitted).toBeTruthy()
    expect(submitted!.baseProjectIds).toEqual(['bl-c1'])
  })

  it('baseLib:list 失败只降级自定义库条目：关联集命中的预置库仍预勾（M-3 拆分）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:listTemplates') {
        return { templates: [{
          template_id: 'dd', template_name: '尽职调查报告', version: '2.0',
          project_types: ['investment'], stage: 'during', output_format: 'docx',
          report_depth: 'deep', chapter_count: OUTLINE_FLAT.length, is_custom: false,
        }] }
      }
      if (channel === 'reports:getTemplate') {
        return { template: { outline: OUTLINE_FLAT, variables: [], template_id: 'dd', template_name: '尽职调查报告' } }
      }
      if (channel === 'baseLib:list') throw new Error('service down')
      if (channel === 'project:getBaseLibs') return { base_libs: [{ id: 'bl-investment' }] }
      return {}
    })
    render(
      <ReportWizard projectId="p1" projectType="investment" onComplete={() => {}} onCancel={() => {}} />,
    )
    await flush()
    fireEvent.click(screen.getByText('尽职调查报告'))
    await flush()

    // 静态条目仍在，动态区为空，不影响生成入口
    expect(screen.getByText('project.report.wizard.projectKb')).toBeTruthy()
    expect(screen.getByText('project.report.wizard.startGenerate').closest('button')!
      .hasAttribute('disabled')).toBe(false)
    // 拆分修复（M-3）：展示性请求失败不连坐关联预勾
    const row = screen.getByText('project.report.wizard.investmentLib').closest('label')!
    expect((row.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(true)
  })

  it('关联集加载失败：生成照常，但不回写关联（防降级勾选清空存量，M-3）', async () => {
    let submitted: Record<string, unknown> | undefined
    invokeMock.mockImplementation(async (channel: string, args?: unknown) => {
      if (channel === 'reports:listTemplates') {
        return { templates: [{
          template_id: 'dd', template_name: '尽职调查报告', version: '2.0',
          project_types: ['investment'], stage: 'during', output_format: 'docx',
          report_depth: 'deep', chapter_count: OUTLINE_FLAT.length, is_custom: false,
        }] }
      }
      if (channel === 'reports:getTemplate') {
        return { template: { outline: OUTLINE_FLAT, variables: [], template_id: 'dd', template_name: '尽职调查报告' } }
      }
      if (channel === 'baseLib:list') return { base_libs: [{ id: 'bl-c1', name: '半导体产业情报', lib_type: 'custom' }] }
      if (channel === 'project:getBaseLibs') throw new Error('assoc down')
      if (channel === 'reports:generateReport') {
        submitted = args as Record<string, unknown>
        return { report_id: 'r-new' }
      }
      return {}
    })
    render(
      <ReportWizard projectId="p1" projectType="investment" onComplete={() => {}} onCancel={() => {}} />,
    )
    await flush()
    fireEvent.click(screen.getByText('尽职调查报告'))
    await flush()

    // list 成功 → 动态条目照常展示；关联集失败 → 仅放弃预勾（降级互补语义）
    const libRow = screen.getByText('半导体产业情报').closest('label')!
    expect((libRow.querySelector('input[type="checkbox"]') as HTMLInputElement).checked).toBe(false)

    fireEvent.click(screen.getByText('project.report.wizard.startGenerate').closest('button')!)
    await flush()

    // 生成照常（降级勾选：仅项目库 → baseProjectIds 为空）
    expect(submitted).toBeTruthy()
    expect(submitted!.baseProjectIds).toEqual([])
    // 不回写关联（sourcesLoaded=false 跳过 sync，存量关联不动）
    const syncCalls = invokeMock.mock.calls.filter(
      ([c]) => c === 'project:linkBaseLib' || c === 'project:unlinkBaseLib',
    )
    expect(syncCalls).toHaveLength(0)
    // 守卫锁力：getBaseLibs 仅被 loadSources 调用 1 次——守卫被删时 sync 会
    // 二次调用该通道（sync 第一步即 getBaseLibs），本断言变红
    const assocCalls = invokeMock.mock.calls.filter(([c]) => c === 'project:getBaseLibs')
    expect(assocCalls).toHaveLength(1)
  })
})
