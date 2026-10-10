/** tests/WikiLintView.test.tsx — lint 一键修复（真实修复）回归（2.6 §12.3.5）
 *  契约：broken-link finding 的 title 携带 [[dead_key]]，description 携带
 *  「PAGES: 源页」与「建议链接到: [[target]]」（lint.py pages 字段经 router 落库）。
 *  点击一键修复 → getPage 读源页 → [[dead]]/[[dead|别名]] 替换为 [[target]] →
 *  editPage 写回 → resolveReview(action='fixed') → toast 成功 → 刷新发现项。
 *  发现项缺少 PAGES 行（旧数据）→ warning 提示，不产生任何写操作。
 *  E2-2026-10 追加：无 [severity] 前缀的发现项按类型回落默认级别（§5.4），
 *  避免摄入管线产出整列「信息」致级别筛选失效。
 *  E2-2026-10 §5.5 治理工作台：分型统计 chips（点击筛选）、矛盾两栏裁决卡
 *  （adjudicateReview 成功/部分失配）、存量矛盾降级提示、死链分型徽章
 *  （src_/结构页/异名）、缺口补料上传（knowledge:uploadFile）。
 *  3.0 一期最终 review 修复轮追加：lint 失败路径仍刷新（结构层已落库）、
 *  处置动作 onFindingsChanged 回调（计数回流链内环）、快切项目 latestPidRef 守卫。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? key,
    i18n: { language: 'zh-CN' },
  }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { WikiLintView } from '../src/features/project/tabs/WikiLintView'

const invokeMock = vi.fn()

const brokenFinding = {
  id: 'r1',
  type: 'broken-link',
  title: '死链: [[dead_link]] (来自 Report)',
  description:
    "页面 'src_report' 引用了不存在的页面 '[[dead_link]]'。\nPAGES: src_report\n建议链接到: [[target_page]]",
  resolved: 0,
  created_at: '2026-10-08T00:00:00Z',
}

const makeFinding = (id: string, title: string) => ({
  id, type: 'data-gap', title, description: 'gap', resolved: 0, created_at: '2026-10-08T00:00:00Z',
})

/** 排空 microtask（loadFindings 异步拉取 + 修复链路多次 await） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  toastMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'wiki:listReviews') return { reviews: [brokenFinding] }
    if (channel === 'wiki:getPage') {
      return {
        page: {
          page_key: 'src_report',
          title: 'Report',
          content: '见 [[dead_link]] 与 [[dead_link|别名]]。',
        },
      }
    }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('WikiLintView one-click broken-link fix (P2-2)', () => {
  it('replaces the dead link in the source page and resolves the finding', async () => {
    const { getByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByText('一键修复'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:getPage', { projectId: 'p1', pageKey: 'src_report' })
    // [[dead]] 与 [[dead|别名]] 两种形式均替换，别名保留
    expect(invokeMock).toHaveBeenCalledWith('wiki:editPage', {
      projectId: 'p1',
      pageKey: 'src_report',
      content: '见 [[target_page]] 与 [[target_page|别名]]。',
      title: 'Report',
    })
    expect(invokeMock).toHaveBeenCalledWith('wiki:resolveReview', {
      projectId: 'p1', reviewId: 'r1', action: 'fixed',
    })
    expect(toastMock).toHaveBeenLastCalledWith('死链已修复', 'success')
  })

  it('warns without writing when the finding has no source page (legacy rows)', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [{
            ...brokenFinding,
            description: "页面 'src_report' 引用了不存在的页面 '[[dead_link]]'。\n建议链接到: [[target_page]]",
          }],
        }
      }
      return {}
    })
    const { getByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByText('一键修复'))
    await flush()

    expect(invokeMock).not.toHaveBeenCalledWith('wiki:editPage', expect.anything())
    expect(invokeMock).not.toHaveBeenCalledWith('wiki:resolveReview', expect.anything())
    expect(toastMock).toHaveBeenLastCalledWith('无法定位源页面或死链，请手动处理', 'warning')
  })
})

describe('WikiLintView severity fallback (E2-2026-10)', () => {
  it('falls back to type default severity for findings without [severity] prefix', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [
            { id: 'r1', type: 'suggestion', title: '核实宣称', description: '需要外部验证', resolved: 0, created_at: '2026-10-08T00:00:00Z' },
            { id: 'r2', type: 'data-gap', title: 'BP缺少财务数据', description: '无财务材料', resolved: 0, created_at: '2026-10-08T00:00:00Z' },
            { id: 'r3', type: 'contradiction', title: '认证时间矛盾', description: '证书 2008 vs BP 2009', resolved: 0, created_at: '2026-10-08T00:00:00Z' },
          ],
        }
      }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    // 排序后徽章依次为 contradiction=high / data-gap=medium / suggestion=low
    // （t 为 mock，返回 defaultValue 即原始 severity；中文标签由 i18n 资源提供）
    const badges = Array.from(container.querySelectorAll('.lintSeverityBadge')).map(el => el.textContent)
    expect(badges).toEqual(['high', 'medium', 'low'])
  })

  it('prefers the [severity] prefix over the type default', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [
            { id: 'r1', type: 'contradiction', title: '轻微措辞不一致', description: '[low] 两处表述略有出入', resolved: 0, created_at: '2026-10-08T00:00:00Z' },
          ],
        }
      }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    const badges = Array.from(container.querySelectorAll('.lintSeverityBadge')).map(el => el.textContent)
    expect(badges).toEqual(['low'])
  })
})

describe('WikiLintView governance workbench (E2-2026-10 §5.5)', () => {
  const gapFinding = {
    id: 'g1', type: 'data-gap', title: '缺少注册资本', description: '年报未提及注册资本',
    resolved: 0, created_at: '2026-10-08T00:00:00Z',
  }

  const contraWithEvidence = {
    id: 'rc1', type: 'contradiction', title: '成立时间矛盾',
    description: '证书与年报说法不一', resolved: 0, created_at: '2026-10-08T00:00:00Z',
    evidence: [
      { page_key: 'p_cert', quote: '公司成立于 2008 年' },
      { page_key: 'p_bp', quote: 'BP 载明 2009 年设立' },
    ],
  }

  it('renders type stat chips with counts and filters on click', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [
            { id: 'r1', type: 'contradiction', title: 'T1', description: 'd', resolved: 0, created_at: '2026-10-08T00:00:00Z' },
            gapFinding,
            { id: 'r2', type: 'suggestion', title: 'T2', description: 'd', resolved: 0, created_at: '2026-10-08T00:00:00Z' },
          ],
        }
      }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    const chips = Array.from(container.querySelectorAll('.lintStatChip'))
    expect(chips.length).toBe(3)
    expect(chips[0].textContent).toContain('contradiction')
    expect(chips[1].textContent).toContain('data-gap')
    expect(chips.map(c => c.querySelector('.lintStatChipCount')?.textContent)).toEqual(['1', '1', '1'])
    expect(container.querySelectorAll('.lintItem').length).toBe(3)

    // 点击 chip 筛选，再点取消
    fireEvent.click(chips[1])
    await flush()
    expect(container.querySelectorAll('.lintItem').length).toBe(1)
    expect(container.querySelector('.lintStatChipActive')?.textContent).toContain('data-gap')

    fireEvent.click(container.querySelectorAll('.lintStatChip')[1])
    await flush()
    expect(container.querySelectorAll('.lintItem').length).toBe(3)
  })

  it('renders two-sided evidence cards and adjudicates on click', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [contraWithEvidence] }
      if (channel === 'wiki:adjudicateReview') return { success: true, details: [] }
      return {}
    })
    const { container, getAllByText, getByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    expect(container.querySelectorAll('.lintEvidenceCard').length).toBe(2)
    expect(container.textContent).toContain('「公司成立于 2008 年」')
    expect(getByText('[[p_bp]]')).toBeTruthy()

    // 点第二侧「以此为准」→ chosen=1
    fireEvent.click(getAllByText('以此为准')[1])
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:adjudicateReview', {
      projectId: 'p1', reviewId: 'rc1', chosen: 1,
    })
    expect(toastMock).toHaveBeenLastCalledWith('已按选定说法更新相关页面', 'success')
  })

  it('warns and keeps the finding when adjudication partially fails', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [contraWithEvidence] }
      if (channel === 'wiki:adjudicateReview') {
        return { success: false, reason: 'partial_or_failed', details: [] }
      }
      return {}
    })
    const { getAllByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getAllByText('以此为准')[0])
    await flush()

    expect(toastMock).toHaveBeenLastCalledWith(
      '部分页面内容已变化，未能自动替换，请手动处理', 'warning',
    )
  })

  it('shows legacy hint for contradiction without structured evidence', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [{
            id: 'rc2', type: 'contradiction', title: '旧矛盾', description: '无证据',
            resolved: 0, created_at: '2026-10-08T00:00:00Z',
          }],
        }
      }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    expect(container.querySelectorAll('.lintEvidenceRow').length).toBe(0)
    expect(container.textContent).toContain('此发现项产生于结构化证据之前')
  })

  it('classifies src_ dead links as missing-source with hint', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [{
            id: 'rb1', type: 'broken-link', title: '死链: [[src_gone]] (来自 X)',
            description: "页面 'p1' 引用了不存在的页面 '[[src_gone]]'。",
            resolved: 0, created_at: '2026-10-08T00:00:00Z',
          }],
        }
      }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    expect(container.querySelector('.lintSubtypeBadge')?.textContent).toBe('来源页缺失')
    expect(container.textContent).toContain('来源摘要页')
  })

  it('classifies schema-page dead links as missing-schema', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [{
            id: 'rb2', type: 'broken-link', title: '死链: [[org_structure]] (来自 X)',
            description: "页面 'p1' 引用了不存在的页面 '[[org_structure]]'。",
            resolved: 0, created_at: '2026-10-08T00:00:00Z',
          }],
        }
      }
      if (channel === 'wiki:getSchemas') return { schemas: [{ page_key: 'Org Structure' }] }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    // schema key 「Org Structure」经 slug 归一与 [[org_structure]] 匹配
    expect(container.querySelector('.lintSubtypeBadge')?.textContent).toBe('结构页缺失')
  })

  it('classifies unknown dead links without suggestion', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') {
        return {
          reviews: [{
            id: 'rb3', type: 'broken-link', title: '死链: [[weird_key]] (来自 X)',
            description: "页面 'p1' 引用了不存在的页面 '[[weird_key]]'。",
            resolved: 0, created_at: '2026-10-08T00:00:00Z',
          }],
        }
      }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    expect(container.querySelector('.lintSubtypeBadge')?.textContent).toBe('疑似异名')
    expect(container.textContent).toContain('疑似异名或无效链接')
  })

  it('uploads gap material via knowledge:uploadFile', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [gapFinding] }
      // 对齐后端真实契约：成功必带 success: true（routers/knowledge.py upload_file）
      if (channel === 'knowledge:uploadFile') return { success: true, file_id: 'file_gap1', queued: true }
      return {}
    })
    const { container, getByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    // 点击按钮设置上传目标，再向隐藏 file input 派发 change（jsdom 不支持文件选择框）
    fireEvent.click(getByText('上传补充材料'))
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['%PDF-1.4 fake'], 'bp.pdf', { type: 'application/pdf' })
    Object.defineProperty(input, 'files', { value: [file] })
    await act(async () => {
      fireEvent.change(input)
    })
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('knowledge:uploadFile', {
      projectId: 'p1',
      filename: 'bp.pdf',
      fileType: '.pdf',
      data: expect.stringMatching(/^data:application\/pdf;base64,/),
    })
    expect(toastMock).toHaveBeenLastCalledWith(
      expect.stringContaining('已上传并开始解析'), 'success',
    )
  })

  it('reports upload failure when the upload channel rejects', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [gapFinding] }
      if (channel === 'knowledge:uploadFile') throw new Error('boom')
      return {}
    })
    const { container, getByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByText('上传补充材料'))
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['x'], 'bp.pdf', { type: 'application/pdf' })
    Object.defineProperty(input, 'files', { value: [file] })
    await act(async () => {
      fireEvent.change(input)
    })
    await flush()

    // 后端失败走 HTTPException → invoke reject → catch 分支（t mock 返回 defaultValue 原文）
    expect(toastMock).toHaveBeenLastCalledWith('上传失败: {{reason}}', 'error')
  })
})

describe('WikiLintView 治理动作刷新(3.0 一期最终 review 修复)', () => {
  it('refreshes findings and notifies parent even when lint scan fails', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [makeFinding('r1', 'gap-A')] }
      if (channel === 'wiki:lint') throw new Error('semantic boom')
      return {}
    })
    const onFindingsChanged = vi.fn()
    const { getByText } = render(
      <WikiLintView projectId="p1" onNavigateToPage={() => {}} onFindingsChanged={onFindingsChanged} />,
    )
    await flush()

    const before = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    fireEvent.click(getByText('重新扫描'))
    await flush()

    const after = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    expect(after).toBe(before + 1) // 失败路径同样刷新:full 模式结构层发现项可能已落库
    expect(onFindingsChanged).toHaveBeenCalled() // 父级链路同步回流
  })

  it('notifies onFindingsChanged after a successful dismiss', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [makeFinding('r1', 'gap-A')] }
      if (channel === 'wiki:resolveReview') return { success: true }
      return {}
    })
    const onFindingsChanged = vi.fn()
    const { container } = render(
      <WikiLintView projectId="p1" onNavigateToPage={() => {}} onFindingsChanged={onFindingsChanged} />,
    )
    await flush()

    fireEvent.click(container.querySelector('.lintDismissBtn') as HTMLElement)
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:resolveReview', {
      projectId: 'p1', reviewId: 'r1', action: 'dismiss',
    })
    expect(onFindingsChanged).toHaveBeenCalledTimes(1)
  })
})

describe('WikiLintView 快切项目守卫(re-review 建议)', () => {
  it('drops stale cross-project response arriving after project switch', async () => {
    let resolveStale: (v: unknown) => void = () => {}
    const stale = new Promise((res) => { resolveStale = res })
    invokeMock.mockImplementation(async (channel: string, args: { projectId?: string }) => {
      if (channel === 'wiki:listReviews') {
        if (args.projectId === 'p1') return stale
        return { reviews: [makeFinding('r2', 'NEW-B')] }
      }
      return {}
    })
    const { rerender, container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    // 快切到 p2:p2 响应先行落定
    rerender(<WikiLintView projectId="p2" onNavigateToPage={() => {}} />)
    await flush()
    expect(container.textContent).toContain('NEW-B')

    // p1 旧响应晚到:守卫丢弃,不得覆盖 p2 列表
    resolveStale({ reviews: [makeFinding('r0', 'STALE-A')] })
    await flush()
    expect(container.textContent).toContain('NEW-B')
    expect(container.textContent).not.toContain('STALE-A')
  })
})
