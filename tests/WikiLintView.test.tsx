/** tests/WikiLintView.test.tsx — lint 一键修复 + 治理工作台回归（2.6 §12.3.5）
 *  契约：broken-link finding 的 title 携带 [[dead_key]]，description 携带
 *  「PAGES: 源页」与「建议链接到: [[target]]」（lint.py pages 字段经 router 落库）。
 *  点击一键修复 → wiki:fixWikilink 服务端正则替换（E3-2026-10：不经 editPage 写回，
 *  不置 user_edited）→ resolveReview(action='fixed') → toast 成功 → 刷新发现项。
 *  发现项缺少 PAGES 行（旧数据）→ warning 提示，不产生任何写操作。
 *  E2-2026-10：无 [severity] 前缀按类型回落默认级别；治理工作台分型 chips、
 *  矛盾两栏裁决卡、存量矛盾降级提示、死链分型徽章、缺口补料上传。
 *  E3-2026-10：裁决部分失配不再笼统 toast——逐侧四态明细 + vanished 终态卡
 *  （标记解决 / 打开页面核对）；重复检测自动化（编译终态落库 wiki_dedup_groups），
 *  面板挂载 wiki:listDedupGroups 只读消费 + 组忽略 + 多选批量合并单确认框；
 *  清扫审查改编译终态自动治理链（§12.2.16），手动按钮与通道已删。
 *  3.0 一期：lint 失败路径仍刷新、onFindingsChanged 回调、快切项目 latestPidRef 守卫。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // 简单插值：真实 i18next 会替换 {{var}}，mock 需同语义（toast 断言依赖插值结果）
    t: (key: string, opts?: Record<string, unknown>) => {
      const raw = (opts?.defaultValue as string) ?? key
      return Object.entries(opts ?? {}).reduce(
        (acc, [k, v]) => acc.split(`{{${k}}}`).join(String(v)),
        raw,
      )
    },
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
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('WikiLintView one-click broken-link fix (P2-2)', () => {
  it('fixes the dead link via the server-side write path and resolves the finding', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [brokenFinding] }
      if (channel === 'wiki:fixWikilink') return { success: true, page_key: 'src_report', replaced_count: 2 }
      return {}
    })
    const { getByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByText('一键修复'))
    await flush()

    // E3-2026-10：服务端正则替换（不置 user_edited + 快照 + link_fixed），
    // 不再 getPage/editPage 写回
    expect(invokeMock).toHaveBeenCalledWith('wiki:fixWikilink', {
      projectId: 'p1', pageKey: 'src_report', deadKey: 'dead_link', targetKey: 'target_page',
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

    expect(invokeMock).not.toHaveBeenCalledWith('wiki:fixWikilink', expect.anything())
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

  it('renders per-side details and the vanished terminal card on partial failure', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [contraWithEvidence] }
      if (channel === 'wiki:adjudicateReview') {
        return {
          success: false,
          reason: 'partial_or_failed',
          details: [
            { page_key: 'p_cert', success: true, status: 'replaced' },
            { page_key: 'p_bp', success: false, status: 'vanished' },
          ],
        }
      }
      return {}
    })
    const onNavigate = vi.fn()
    const { getAllByText, getByText, container } = render(
      <WikiLintView projectId="p1" onNavigateToPage={onNavigate} />,
    )
    await flush()

    fireEvent.click(getAllByText('以此为准')[0])
    await flush()

    // 逐侧明细替代笼统 toast（免培训：每侧发生了什么）
    expect(container.textContent).toContain('[[p_cert]] 已更新为选定说法')
    expect(container.textContent).toContain('[[p_bp]] 中已找不到原来的说法')
    expect(container.textContent).toContain('矛盾很可能已随编辑消除')

    // vanished 终态卡：打开页面核对 → 直达该页
    expect(getByText('矛盾已消除，标记解决')).toBeTruthy()
    fireEvent.click(getByText('打开页面核对'))
    expect(onNavigate).toHaveBeenCalledWith('p_bp')
  })

  it('resolves the finding via dismiss from the vanished terminal card', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [contraWithEvidence] }
      if (channel === 'wiki:adjudicateReview') {
        return {
          success: false,
          reason: 'partial_or_failed',
          details: [{ page_key: 'p_bp', success: false, status: 'vanished' }],
        }
      }
      return {}
    })
    const onFindingsChanged = vi.fn()
    const { getAllByText, getByText } = render(
      <WikiLintView projectId="p1" onNavigateToPage={() => {}} onFindingsChanged={onFindingsChanged} />,
    )
    await flush()

    fireEvent.click(getAllByText('以此为准')[0])
    await flush()
    fireEvent.click(getByText('矛盾已消除，标记解决'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:resolveReview', {
      projectId: 'p1', reviewId: 'rc1', action: 'dismiss',
    })
    expect(onFindingsChanged).toHaveBeenCalled()
  })

  it('hides the vanished resolve shortcut and points to the manual page when one remains (review m-4)', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [contraWithEvidence] }
      if (channel === 'wiki:adjudicateReview') {
        return {
          success: false,
          reason: 'partial_or_failed',
          details: [
            { page_key: 'p_cert', success: true, status: 'replaced' },
            { page_key: 'p_bp', success: false, status: 'vanished' },
            { page_key: 'p_c', success: false, status: 'manual_needed', reason: 'invalid_evidence' },
          ],
        }
      }
      return {}
    })
    const onNavigate = vi.fn()
    const { getAllByText, getByText, queryByText } = render(
      <WikiLintView projectId="p1" onNavigateToPage={onNavigate} />,
    )
    await flush()

    fireEvent.click(getAllByText('以此为准')[0])
    await flush()

    // 仍有 manual_needed 侧时「矛盾已消除」不成立 → 一键归档隐藏，
    // 改出「未更新」引导，按钮指向未更新页而非 vanished 页
    expect(queryByText('矛盾已消除，标记解决')).toBeNull()
    expect(getByText(
      '部分原说法已消失，但仍有页面未能自动更新——请打开处理，完成后可忽略本条',
    )).toBeTruthy()
    fireEvent.click(getByText('打开未更新页面'))
    expect(onNavigate).toHaveBeenCalledWith('p_c')
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
    expect(container.querySelector('.lintSubtypeBadge')?.textContent).toBe('框架页缺失')
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

    // 后端失败走 HTTPException → invoke reject → catch 分支（t mock 已插值 → 实际文案）
    expect(toastMock).toHaveBeenLastCalledWith('上传失败: boom', 'error')
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

describe('WikiLintView 重复页治理面板（E3-2026-10：检测自动化落库，挂载消费 + 批量合并）', () => {
  const makeGroup = (id: string, slugs: string[], confidence = 'high') => ({
    id, slugs, reason: '同一公司主页', confidence,
  })

  it('loads persisted dedup groups on mount and renders group cards', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [makeGroup('g1', ['corp_a', 'corp_b'])] }
      return {}
    })
    const { container, getByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:listDedupGroups', { projectId: 'p1' })
    expect(container.textContent).toContain('同一公司主页')
    expect(container.querySelector('.dedupGroup')).toBeTruthy()
    expect(getByText('[[corp_a]]')).toBeTruthy()
    // 默认首个 slug 为 canonical
    expect((container.querySelector('input[type="radio"]') as HTMLInputElement).checked).toBe(true)
  })

  it('renders no panel when there are no pending groups', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [] }
      return {}
    })
    const { container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    expect(container.querySelector('.dedupPanel')).toBeNull()
  })

  it('merges a single group after confirmation with groupId', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [makeGroup('g1', ['corp_a', 'corp_b'])] }
      if (channel === 'wiki:dedupMerge') return { merged: 1, rewritten_links: 5, failed: [], truncated: [] }
      return {}
    })
    const onFindingsChanged = vi.fn()
    const { getByText, container } = render(
      <WikiLintView projectId="p1" onNavigateToPage={() => {}} onFindingsChanged={onFindingsChanged} />,
    )
    await flush()

    // 改选 corp_b 为保留页
    const radios = container.querySelectorAll('input[type="radio"]') as NodeListOf<HTMLInputElement>
    fireEvent.click(radios[1])

    fireEvent.click(getByText('合并（2 → 1）'))
    expect(container.querySelector('.confirmOverlay')).toBeTruthy()
    expect(container.textContent).toContain('将把其余 1 个重复页面合并进「corp_b」后删除')
    fireEvent.click(container.querySelector('.confirmDangerBtn')!)
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:dedupMerge', {
      projectId: 'p1', canonicalKey: 'corp_b', duplicateKeys: ['corp_a'], groupId: 'g1',
    })
    expect(toastMock).toHaveBeenLastCalledWith('已合并 1 组重复页面，重写 5 处链接', 'success')
    expect(container.querySelector('.dedupGroup')).toBeNull()
    expect(onFindingsChanged).toHaveBeenCalled()
  })

  it('batch-merges selected groups with one confirmation', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') {
        return { groups: [makeGroup('g1', ['corp_a', 'corp_b']), makeGroup('g2', ['team_x', 'team_y'], 'medium')] }
      }
      if (channel === 'wiki:dedupMerge') return { merged: 1, rewritten_links: 3, failed: [], truncated: [] }
      return {}
    })
    const { getByText, container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    const checkboxes = container.querySelectorAll('input[type="checkbox"]') as NodeListOf<HTMLInputElement>
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])
    expect(container.textContent).toContain('已选 2 组')

    fireEvent.click(getByText('合并所选（2 组）'))
    expect(container.textContent).toContain('将把所选 2 组重复页面各合并为 1 页')
    fireEvent.click(container.querySelector('.confirmDangerBtn')!)
    await flush()

    // 逐组顺序执行，均携 groupId（后端回写 status=merged）
    const mergeCalls = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:dedupMerge')
    expect(mergeCalls.length).toBe(2)
    expect(mergeCalls[0][1]).toMatchObject({ groupId: 'g1', canonicalKey: 'corp_a' })
    expect(mergeCalls[1][1]).toMatchObject({ groupId: 'g2', canonicalKey: 'team_x' })
    expect(toastMock).toHaveBeenLastCalledWith('已合并 2 组重复页面，重写 6 处链接', 'success')
    expect(container.querySelector('.dedupGroup')).toBeNull()
  })

  it('keeps failed groups in the panel and warns after a partial merge', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [makeGroup('g1', ['corp_a', 'corp_b'])] }
      if (channel === 'wiki:dedupMerge') {
        return { merged: 0, rewritten_links: 0, failed: [{ page_key: 'corp_b', reason: 'boom' }], truncated: [] }
      }
      return {}
    })
    const { getByText, container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByText('合并（2 → 1）'))
    fireEvent.click(container.querySelector('.confirmDangerBtn')!)
    await flush()

    expect(toastMock).toHaveBeenLastCalledWith(
      '已合并 0 组；1 组存在失败页面，已保留可重试或忽略',
      'warning',
    )
    // 失败组保留在面板供重试或忽略（内容不丢）
    expect(container.querySelector('.dedupGroup')).toBeTruthy()
  })

  it('removes truncated groups from the panel and hints instead of faking retry (review m-3)', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [makeGroup('g1', ['corp_a', 'corp_b'])] }
      if (channel === 'wiki:dedupMerge') return { merged: 1, rewritten_links: 5, failed: [], truncated: ['corp_b'] }
      return {}
    })
    const { getByText, container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByText('合并（2 → 1）'))
    fireEvent.click(container.querySelector('.confirmDangerBtn')!)
    await flush()

    // truncated 页合并本身已成功（后端已置 merged）→ 组照常移除，不伪装「可重试」
    expect(container.querySelector('.dedupGroup')).toBeNull()
    expect(toastMock).toHaveBeenCalledWith('已合并 1 组重复页面，重写 5 处链接', 'success')
    expect(toastMock).toHaveBeenLastCalledWith(
      '其中 1 组含内容超长页面，仅部分正文并入，建议打开保留页核对',
      'info',
    )
  })

  it('dismisses a group so it disappears from the panel', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [makeGroup('g1', ['corp_a', 'corp_b'])] }
      if (channel === 'wiki:dismissDedupGroup') return { success: true }
      return {}
    })
    const { getByText, container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByText('忽略'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:dismissDedupGroup', { projectId: 'p1', groupId: 'g1' })
    expect(container.querySelector('.dedupGroup')).toBeNull()
  })

  it('drops stale cross-project groups arriving after a project switch', async () => {
    let resolveStale: (v: unknown) => void = () => {}
    const stale = new Promise((res) => { resolveStale = res })
    invokeMock.mockImplementation(async (channel: string, args: { projectId?: string }) => {
      if (channel === 'wiki:listReviews') return { reviews: [makeFinding('r1', `gap-${args?.projectId}`)] }
      if (channel === 'wiki:listDedupGroups' && args?.projectId === 'p1') return stale
      if (channel === 'wiki:listDedupGroups') return { groups: [] }
      return {}
    })
    const { rerender, container } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    rerender(<WikiLintView projectId="p2" onNavigateToPage={() => {}} />)
    await flush()
    // p1 晚到的旧项目分组：latestPidRef 守卫丢弃——slug 同名会对新项目误合并
    resolveStale({ groups: [makeGroup('g-old', ['corp_a', 'corp_b'])] })
    await flush()

    expect(container.querySelector('.dedupPanel')).toBeNull()
    expect(container.textContent).not.toContain('同一公司主页')
  })
})

describe('WikiLintView 治理完成信号刷新(review M-2)', () => {
  it('reloads findings and dedup groups when app:governance-done arrives', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [] }
      return {}
    })
    render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    const reviewCalls = () => invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    const dedupCalls = () => invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listDedupGroups').length
    const beforeR = reviewCalls()
    const beforeD = dedupCalls()

    // 治理完成通知到达（ActivityMonitor 广播）→ 已打开的体检视图自动重载
    await act(async () => {
      window.dispatchEvent(new CustomEvent('app:governance-done', { detail: { projectId: 'p1' } }))
      await Promise.resolve()
    })
    await flush()

    expect(reviewCalls()).toBe(beforeR + 1)
    expect(dedupCalls()).toBe(beforeD + 1)
  })

  it('ignores governance-done signals from other projects', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [] }
      if (channel === 'wiki:listDedupGroups') return { groups: [] }
      return {}
    })
    render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    const reviewCalls = () => invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    const beforeR = reviewCalls()

    await act(async () => {
      window.dispatchEvent(new CustomEvent('app:governance-done', { detail: { projectId: 'p2' } }))
      await Promise.resolve()
    })
    await flush()

    expect(reviewCalls()).toBe(beforeR)
  })
})

describe('WikiLintView same-type project switch (review M-1)', () => {
  it('clears stale findings on project switch during the reload window', async () => {
    invokeMock.mockImplementation(async (channel: string, args: { projectId?: string }) => {
      if (channel === 'wiki:listReviews') {
        if (args?.projectId === 'p1') return { reviews: [makeFinding('r1', 'A 项目缺口')] }
        return new Promise(() => {}) // p2 重拉挂起，暴露切换窗口
      }
      return {}
    })
    const { rerender, queryByText } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()
    expect(queryByText('A 项目缺口')).toBeTruthy()

    // 切项目：旧 findings 必须整组清掉——残留窗口期「一键修复」会用新 projectId
    // 改写 B 项目同名页（resolveReview 才 403，页面已被改写且静默）
    rerender(<WikiLintView projectId="p2" onNavigateToPage={() => {}} />)
    await flush()
    expect(queryByText('A 项目缺口')).toBeNull()
  })

  it('shows an error toast when dismiss fails (was silent)', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:listReviews') return { reviews: [makeFinding('r1', '死链发现')] }
      if (channel === 'wiki:resolveReview') throw new Error('403 review belongs to different project')
      return {}
    })
    const { getByTitle } = render(<WikiLintView projectId="p1" onNavigateToPage={() => {}} />)
    await flush()

    fireEvent.click(getByTitle('忽略'))
    await flush()
    expect(toastMock).toHaveBeenLastCalledWith(
      '忽略失败: 403 review belongs to different project',
      'error',
    )
  })
})
