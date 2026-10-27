/** tests/WikiChangeLog.test.tsx — 时间线双栏（2026-10-26 IA 收敛）:
 *  左=系统变更日志（usePagedList wiki:getChangeLog 打桩固定）、
 *  右=台账动态（原首页台账卡迁入:entry_date 分组/NULL 置尾/标题反查/行点击直达 Reader）。
 *  两栏数据源不同,分栏即语义;CSS Proxy mock 同仓库惯例。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k, i18n: { language: 'zh-CN' } }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({ useToast: () => vi.fn() }))

const logsFixture = [
  {
    id: 'c1', action: 'ingest', wiki_page_id: null,
    summary: '导入 2 个文件', source_title: 'a.pdf', trigger_file_id: null,
    affected_page_keys: [], created_at: '2026-10-01T10:00:00Z',
  },
]
vi.mock('../src/hooks/usePagedList', () => ({
  usePagedList: () => ({
    items: logsFixture, hasMore: false, loadingMore: false,
    loading: false, error: null, loadMore: vi.fn(),
  }),
}))

import { render, cleanup, fireEvent } from '@testing-library/react'
import { WikiChangeLog } from '../src/features/project/tabs/WikiChangeLog'

const pages = [
  { page_key: 'milestones', title: '里程碑' },
  { page_key: 'risk_watchlist', title: '风险观察' },
]

const entries = [
  { id: 'e1', page_key: 'milestones', entry_date: '2026-10', entry_type: 'milestone', content: '一期设备全部进场' },
  { id: 'e2', page_key: 'risk_watchlist', entry_date: '2026-09', entry_type: 'risk', content: '企业被列入经营异常名录' },
  { id: 'e3', page_key: 'milestones', entry_date: null, entry_type: 'milestone', content: '无日期条目' },
]

const base = {
  projectId: 'p1',
  onNavigateToPage: vi.fn(),
  ledgerEntries: entries,
  pages,
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

describe('WikiChangeLog 双栏 (IA 收敛,2026-10-26)', () => {
  it('renders both columns; ledger feed groups by entry_date with NULL pinned last', () => {
    const { container } = render(<WikiChangeLog {...base} />)
    const text = container.textContent!
    // 两栏栏头（数据源不同,分栏即语义）
    expect(text).toContain('project.wiki.changeLog')
    expect(text).toContain('project.wiki.timeline.ledgerFeed')
    // 左栏系统事件
    expect(text).toContain('导入 2 个文件')
    // 右栏台账:日期组 + NULL 置尾组
    expect(text).toContain('2026-10')
    expect(text).toContain('一期设备全部进场')
    expect(text).toContain('—')
    // 双栏容器形态锁
    expect(container.querySelector('.changelogColumns')).not.toBeNull()
    expect(container.querySelector('.ledgerFeedCol')).not.toBeNull()
  })

  it('resolves ledger row titles via pages prop and navigates on click', () => {
    const onNavigateToPage = vi.fn()
    const { getByText, getAllByText } = render(
      <WikiChangeLog {...base} onNavigateToPage={onNavigateToPage} />
    )
    // 标题反查:行内显示页标题而非 page_key（e1/e3 同页,标题出现两次）
    expect(getAllByText('里程碑').length).toBe(2)
    fireEvent.click(getByText('一期设备全部进场').closest('button')!)
    expect(onNavigateToPage).toHaveBeenCalledWith('milestones')
  })

  it('falls back to raw page_key when title lookup misses', () => {
    const { container } = render(<WikiChangeLog {...base} pages={[]} />)
    expect(container.textContent).toContain('milestones')
  })

  it('shows empty state without entries and parsing hint alongside old entries', () => {
    const { container: c1 } = render(<WikiChangeLog {...base} ledgerEntries={[]} />)
    expect(c1.textContent).toContain('project.wiki.timeline.ledgerFeedEmpty')

    const { container: c2 } = render(<WikiChangeLog {...base} ledgerParsing />)
    expect(c2.textContent).toContain('project.wiki.timeline.parsing')
    expect(c2.textContent).toContain('一期设备全部进场') // 旧条目照常可读
  })
})
