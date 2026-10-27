/** tests/WikiHomePage.test.tsx — Wiki 首页（2026-10-26 二次收敛:「页面」视图并入）:
 *  目录态 = 引导卡/全局摘要/成册进度轻量条/目录分组(台账置顶+缺失置灰)/空态 CTA/统计条;
 *  搜索态 = 全文搜索(Enter 触发/失败与无结果分义)。
 *  用例谱系:原 WikiHomePage.test + WikiPagesView.test 合并(组件删除,逻辑迁入首页)。
 *  组件消费共享 WikiTab.module.css(Proxy mock 同仓库惯例);Markdown 打桩避免重依赖。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({ useToast: () => vi.fn() }))
vi.mock('../src/components/Markdown', () => ({ Markdown: () => null }))

import { render, cleanup, fireEvent, act } from '@testing-library/react'
import { WikiHomePage } from '../src/features/project/tabs/WikiHomePage'

const invokeMock = vi.fn()

/** 排空 microtask（doSearch 异步拉取） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const minimal = {
  projectId: 'p1',
  overview: '',
  sourcePageCount: 0,
  pages: [] as never[],
  pendingCount: 0,
  pagesTotal: 0,
  onNavigateToPage: vi.fn(),
  onCompile: vi.fn(),
  compiling: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  invokeMock.mockReset()
  invokeMock.mockResolvedValue({ results: [] })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('WikiHomePage onboarding (引导卡不再是汇编入口)', () => {
  it('shows onboarding steps for a first-time project (no pages & no source files)', () => {
    const { container } = render(<WikiHomePage {...minimal} />)
    const text = container.textContent!
    expect(text).toContain('project.wiki.homePage.onboardTitle')
    expect(text).toContain('project.wiki.homePage.step3Hint')
    // 引导卡自身无按钮——开始汇编 CTA 在目录区，且仅 pendingCount>0 时出现
    expect(text).not.toContain('project.wiki.homePage.startCompile')
  })

  it('hides onboarding once any page or source file exists', () => {
    const { container: c1 } = render(<WikiHomePage {...minimal} pagesTotal={2} />)
    expect(c1.textContent!).not.toContain('project.wiki.homePage.onboardTitle')
    const { container: c2 } = render(<WikiHomePage {...minimal} sourcePageCount={1} />)
    expect(c2.textContent!).not.toContain('project.wiki.homePage.onboardTitle')
  })
})

describe('WikiHomePage 目录分组 (内容索引 → 页面视图 → 首页，逻辑迁移非重写)', () => {
  const indexPages = [
    { page_key: 'enterprise_profile', title: '企业画像', content: '画像内容', page_type: 'structure', status: 'generated' },
    { page_key: 'policy_ledger', title: '政策台账', content: '台账内容', page_type: 'structure', status: 'generated' },
  ]
  const indexSchemas = [
    { page_key: 'enterprise_profile', title: '企业画像' },
    { page_key: 'policy_ledger', title: '政策台账', is_ledger: true },
    { page_key: 'risk_ledger', title: '风险台账', is_ledger: true },
  ]

  it('pins ledger group on top and greys missing framework pages', () => {
    const onNavigateToPage = vi.fn()
    const { container } = render(
      <WikiHomePage {...minimal} pages={indexPages as never}
        schemas={indexSchemas} pagesTotal={2}
        missingPages={[{ page_key: 'missing_frame', title: '缺失框架页' }, { page_key: 'risk_ledger', title: '风险台账' }]}
        onNavigateToPage={onNavigateToPage} />
    )
    const text = container.textContent!
    // 台账组置顶:组标题先于知识框架组标题出现
    const ledgerPos = text.indexOf('project.wiki.homePage.indexGroupLedger')
    const structurePos = text.indexOf('project.wiki.homePage.typeStructure')
    expect(ledgerPos).toBeGreaterThan(-1)
    expect(structurePos).toBeGreaterThan(-1)
    expect(ledgerPos).toBeLessThan(structurePos)
    // 缺失页置灰行(待汇编小标):台账组内 risk_ledger + 知识框架组内 missing_frame
    expect(text).toContain('风险台账')
    expect(text).toContain('缺失框架页')
    expect(text).toContain('project.wiki.homePage.pendingTag')
    expect(container.querySelectorAll('.homeIndexItemPending')).toHaveLength(2)
    // 已生成台账页仍可点(置顶组内第一行)
    fireEvent.click(container.querySelector('.homeIndexItem')!)
    expect(onNavigateToPage).toHaveBeenCalledWith('policy_ledger')
    // 总数行(pagesCountLine,全量口径)
    expect(text).toContain('project.wiki.homePage.pagesCount')
  })

  it('renders a pending-only structure group when no structure page generated', () => {
    const { container } = render(
      <WikiHomePage {...minimal}
        pages={[{ page_key: 'topic_a', title: '主题页', content: '', page_type: 'topic', status: 'generated' }] as never}
        schemas={[{ page_key: 'framework_x', title: '框架页X' }]}
        missingPages={[{ page_key: 'framework_x', title: '框架页X' }]} />
    )
    const text = container.textContent!
    // 无已生成结构页,但缺失定义仍以纯置灰组呈现
    expect(text).toContain('框架页X')
    expect(container.querySelector('.homeIndexItemPending')).not.toBeNull()
  })
})

describe('WikiHomePage 空态 CTA (待汇编即启动,经 startCompileMaybe 契约)', () => {
  it('pending pages offer the start-compile CTA wired to onCompile', () => {
    const onCompile = vi.fn()
    const { getByText, container } = render(
      <WikiHomePage {...minimal} pendingCount={3} onCompile={onCompile} />
    )
    expect(container.textContent).toContain('project.wiki.homePage.pendingCta')
    fireEvent.click(getByText('project.wiki.homePage.startCompile'))
    expect(onCompile).toHaveBeenCalledTimes(1)
  })

  it('disabled CTA while compiling; plain placeholder when nothing pending', () => {
    const { container } = render(
      <WikiHomePage {...minimal} pendingCount={3} compiling />
    )
    const btn = container.querySelector('.retryBtn') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(container.textContent).toContain('project.wiki.homePage.compiling')

    const { container: c2 } = render(
      <WikiHomePage {...minimal} pendingCount={0} />
    )
    expect(c2.textContent).toContain('project.wiki.homePage.emptyIndex')
  })
})

describe('WikiHomePage 搜索态 (自 WikiPagesView 迁入,原 WikiSearchView 降级)', () => {
  const getInput = (container: HTMLElement) =>
    container.querySelector('.searchInput') as HTMLInputElement

  it('searches on Enter and renders results that navigate on click', async () => {
    const onNavigateToPage = vi.fn()
    invokeMock.mockResolvedValue({
      results: [
        { page_key: 'alpha', title: 'Alpha', content: 'alpha 内容', page_type: 'topic', _score: 0.92, _source: 'fts' },
      ],
    })
    const { container } = render(
      <WikiHomePage {...minimal} onNavigateToPage={onNavigateToPage} />
    )
    fireEvent.change(getInput(container), { target: { value: 'alpha' } })
    fireEvent.keyDown(getInput(container), { key: 'Enter' })
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:search', {
      projectId: 'p1', query: 'alpha', topK: 20,
    })
    expect(container.querySelector('.searchResultItem')).not.toBeNull()
    expect(container.textContent).toContain('Alpha')
    fireEvent.click(container.querySelector('.searchResultItem')!)
    expect(onNavigateToPage).toHaveBeenCalledWith('alpha')
  })

  it('hides overview and index sections while searching (搜索态聚焦结果)', async () => {
    const { container } = render(<WikiHomePage {...minimal} overview="摘要内容" pagesTotal={3} />)
    expect(container.textContent).toContain('project.wiki.overview')
    fireEvent.change(getInput(container), { target: { value: 'alpha' } })
    fireEvent.keyDown(getInput(container), { key: 'Enter' })
    await flush()

    expect(container.textContent).not.toContain('project.wiki.overview')
    expect(container.textContent).not.toContain('project.wiki.homePage.pagesCount')
  })

  it('shows a dedicated failure state distinct from no-results (后端错误透出)', async () => {
    invokeMock.mockRejectedValue(new Error('boom'))
    const { container } = render(<WikiHomePage {...minimal} />)
    fireEvent.change(getInput(container), { target: { value: 'alpha' } })
    fireEvent.keyDown(getInput(container), { key: 'Enter' })
    await flush()

    expect(container.textContent).toContain('project.wiki.searchFailed')
  })
})

describe('WikiHomePage 成册进度轻量条 (进度环压缩,2026-10-26)', () => {
  const schemas = [
    { page_key: 'enterprise_profile', title: '企业画像' },
    { page_key: 'policy_ledger', title: '政策台账' },
    { page_key: 'risk_ledger', title: '风险台账' },
  ]

  it('renders a one-line lite bar and navigates to schema view on click', () => {
    const onOpenSchema = vi.fn()
    const { container, getByText } = render(
      <WikiHomePage {...minimal}
        schemas={schemas}
        // 完成度消费后端 missing_pages（全量口径，2026-10-22 起不再用分页 pages 复算）
        missingPages={[{ page_key: 'risk_ledger', title: '风险台账' }]}
        pagesTotal={2}
        onOpenSchema={onOpenSchema} />
    )
    const text = container.textContent!
    expect(text).toContain('project.wiki.homePage.completionTitle')
    expect(text).toContain('2/3')                                  // generated/total
    // 轻量条形态锁:进度环与 chips 清单均已删（同一信息不在两处维护）
    expect(container.querySelector('.progressLiteFill')).not.toBeNull()
    expect(container.querySelector('.completionRing')).toBeNull()
    expect(container.querySelector('.completionChipDone')).toBeNull()
    fireEvent.click(getByText('project.wiki.homePage.completionTitle'))
    expect(onOpenSchema).toHaveBeenCalledTimes(1)
  })

  it('hides the lite bar when schemas empty or prop absent', () => {
    const { container: c1 } = render(<WikiHomePage {...minimal} schemas={[]} />)
    expect(c1.textContent!).not.toContain('project.wiki.homePage.completionTitle')
    const { container: c2 } = render(<WikiHomePage {...minimal} />)
    expect(c2.textContent!).not.toContain('project.wiki.homePage.completionTitle')
  })
})

describe('WikiHomePage stats bar', () => {
  it('reports totals from pagesTotal (全量口径) and source file count', () => {
    const { container } = render(<WikiHomePage {...minimal} pagesTotal={7} sourcePageCount={3} />)
    const text = container.textContent!
    expect(text).toContain('7')
    expect(text).toContain('project.wiki.wikiPages')
    expect(text).toContain('project.wiki.sourceFiles')
  })

  it('hides the stats bar for a fully empty project', () => {
    const { container } = render(<WikiHomePage {...minimal} />)
    expect(container.querySelector('.homeStats')).toBeNull()
  })
})
