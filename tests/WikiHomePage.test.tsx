/** tests/WikiHomePage.test.tsx — 知识总览健康卡(3.0 §6.1.2 一期)
 *  三型计数(矛盾/缺口/死链)、全 0 整卡不渲染、零型 chip 隐藏、「去处理」直达治理视图。
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

import { render, cleanup, fireEvent } from '@testing-library/react'
import { WikiHomePage } from '../src/features/project/tabs/WikiHomePage'

const minimal = {
  projectId: 'p1',
  purpose: '',
  overview: '',
  pages: [],
  sourcePageCount: 0,
  pendingCount: 0,
  onNavigate: vi.fn(),
  onNavigateToPage: vi.fn(),
  onCompile: vi.fn(),
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => cleanup())

describe('WikiHomePage health card (3.0 §6.1.2)', () => {
  it('hides health card when counts are all zero', () => {
    const { container } = render(
      <WikiHomePage {...minimal} reviewCounts={{ contradiction: 0, gap: 0, deadLink: 0 }} />
    )
    expect(container.textContent).not.toContain('project.wiki.homePage.healthTitle')
  })

  it('hides health card when prop absent', () => {
    const { container } = render(<WikiHomePage {...minimal} />)
    expect(container.textContent).not.toContain('project.wiki.homePage.healthTitle')
  })

  it('renders three typed counts and opens lint via action', () => {
    const onOpenLint = vi.fn()
    const { container, getByText } = render(
      <WikiHomePage {...minimal}
        reviewCounts={{ contradiction: 3, gap: 5, deadLink: 2 }} onOpenLint={onOpenLint} />
    )
    const text = container.textContent!
    expect(text).toContain('project.wiki.homePage.healthTitle')
    // getByText 全串精确匹配:同时防两类污染——onboarding 步骤数字(1/2/3)误命中裸数字、子串前缀逃逸(3 vs 30)
    getByText('project.wiki.homePage.healthContradiction 3')
    getByText('project.wiki.homePage.healthGap 5')
    getByText('project.wiki.homePage.healthDeadLink 2')
    fireEvent.click(getByText('project.wiki.homePage.healthGo'))
    expect(onOpenLint).toHaveBeenCalledTimes(1)
  })

  it('hides individual zero chips but keeps the card', () => {
    const { container } = render(
      <WikiHomePage {...minimal}
        reviewCounts={{ contradiction: 1, gap: 0, deadLink: 0 }}
        onOpenLint={vi.fn()} />
    )
    const text = container.textContent!
    expect(text).toContain('project.wiki.homePage.healthContradiction')
    expect(text).not.toContain('project.wiki.homePage.healthGap')
    expect(text).not.toContain('project.wiki.homePage.healthDeadLink')
  })
})

describe('WikiHomePage schema completion (3.0 一期 Task 6)', () => {
  it('renders schema completion bar with missing-page skeleton chips', () => {
    const { container } = render(
      <WikiHomePage {...minimal}
        schemas={[
          { page_key: 'enterprise_profile', title: '企业画像' },
          { page_key: 'policy_ledger', title: '政策台账' },
          { page_key: 'risk_ledger', title: '风险台账' },
        ]}
        pages={[{ page_key: 'enterprise_profile' }, { page_key: 'policy_ledger' }] as never}
      />
    )
    const text = container.textContent!
    expect(text).toContain('project.wiki.homePage.completionTitle')
    expect(text).toContain('2/3')                                  // 进度
    expect(text).toContain('企业画像')                              // 已生成 chip
    expect(text).toContain('风险台账')                              // 骨架占位 chip
    // done/missing 视觉区分锁（CSS Proxy mock 下 class 名即字面量）
    expect(container.querySelector('.completionChipDone')).not.toBeNull()
    expect(container.querySelector('.completionChipMissing')).not.toBeNull()
  })

  it('hides completion block when schemas empty', () => {
    const { container } = render(<WikiHomePage {...minimal} schemas={[]} />)
    expect(container.textContent).not.toContain('project.wiki.homePage.completionTitle')
  })
})

describe('WikiHomePage ledger timeline card (3.0 四期)', () => {
  const entries = [
    { id: 'e1', page_key: 'milestones', entry_date: '2026-10', entry_type: 'milestone', content: '一期设备全部进场' },
    { id: 'e2', page_key: 'risk_watchlist', entry_date: '2026-09', entry_type: 'risk', content: '企业被列入经营异常名录' },
    { id: 'e3', page_key: 'milestones', entry_date: null, entry_type: 'milestone', content: '无日期条目' },
  ]

  it('hides card when entries empty or prop absent', () => {
    const { container: c1 } = render(<WikiHomePage {...minimal} ledgerEntries={[]} />)
    expect(c1.textContent).not.toContain('project.wiki.timeline.title')
    const { container: c2 } = render(<WikiHomePage {...minimal} />)
    expect(c2.textContent).not.toContain('project.wiki.timeline.title')
  })

  it('renders date-grouped entries and navigates on click', () => {
    const onNavigateToPage = vi.fn()
    const { getByText, container } = render(
      <WikiHomePage {...minimal} ledgerEntries={entries} onNavigateToPage={onNavigateToPage} />)
    const text = container.textContent!
    expect(text).toContain('project.wiki.timeline.title')
    expect(text).toContain('2026-10')
    expect(text).toContain('一期设备全部进场')
    expect(text).toContain('企业被列入经营异常名录')
    expect(text).toContain('—')                                   // NULL 日期置尾组
    // 条目行点击 → 直达台账页(标题找不到时显示 page_key 本身)
    fireEvent.click(getByText('一期设备全部进场').closest('button')!)
    expect(onNavigateToPage).toHaveBeenCalledWith('milestones')
  })

  it('shows parsing hint while keeping old entries visible', () => {
    const { container } = render(
      <WikiHomePage {...minimal} ledgerEntries={entries.slice(0, 1)} ledgerParsing />)
    const text = container.textContent!
    expect(text).toContain('project.wiki.timeline.parsing')
    expect(text).toContain('一期设备全部进场')                       // 旧条目照常可读
  })
})
