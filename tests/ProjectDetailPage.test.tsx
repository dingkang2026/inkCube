/** tests/ProjectDetailPage.test.tsx — #4 谓词心跳化 + 角标派生 + 边沿补拉回归（0.4 §6.3，顺序 4）
 *  轮询启停由 useActivity('wiki_compile') 项目级心跳派生（compileDetailOpen 兜住
 *  启动间隙）；挂载 / 项目切换 / 切到 wiki Tab 各拉一次快照；wikiCompileActive
 *  true→false 边沿补拉终态快照；Tab 角标从心跳 byProject 派生（不再依赖本地快照）。
 *  Tab 内容组件全部打桩：仅验证页面主体接线；机制本身由 useEdgeRefetch.test 覆盖。
 *  时序约定（同 tests/usePolling.test.ts）：fake timers + flush 排空 microtask。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const compileItems: Array<{ project_id?: string }> = []
const ledgerItems: Array<{ project_id?: string }> = []
const byProject = new Map<
  string,
  { files: number; imageDescribing: number; compiles: number; reports: number }
>()

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) =>
    kind === 'wiki_compile'
      ? { kind, items: compileItems, isActive: compileItems.length > 0 }
      : kind === 'ledger_parse'
        ? { items: ledgerItems }
        : {
            status: null,
            summary: {},
            byProject,
            activeProjectIds: new Set(),
            feed: [],
            applyStatus: () => {},
            markRead: async () => {},
          },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/features/project/ProjectDetailPage.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/features/knowledge/KnowledgePage', () => ({ KnowledgePage: () => null }))
vi.mock('../src/features/project/tabs/WikiTab', () => ({ WikiTab: () => null }))
vi.mock('../src/features/project/tabs/ReportsTab', () => ({ ReportsTab: () => null }))
vi.mock('../src/features/project/tabs/FormsTab', () => ({ FormsTab: () => null }))
vi.mock('../src/features/project/affairs/AffairsTab', () => ({ AffairsTab: () => null }))
vi.mock('../src/features/project/ProjectChatPanel', () => ({ ProjectChatPanel: () => null }))
vi.mock('../src/features/project/PulseBar', () => ({
  PulseBar: (props: { reviews?: { contradiction: number; gap: number }; pendingCompile?: number;
    aiStatus?: string; suggestionCount?: number }) => (
    <div data-testid="pulsebar" data-contradiction={props.reviews?.contradiction}
      data-gap={props.reviews?.gap}
      data-pending={props.pendingCompile} data-ai={props.aiStatus}
      data-suggestions={props.suggestionCount} />
  ),
}))

import { render, cleanup, act, fireEvent } from '@testing-library/react'
import { ProjectDetailPage } from '../src/features/project/ProjectDetailPage'
import type { Project } from '../src/features/knowledge/types'

const project: Project = { id: 'p1', name: 'P', file_count: 0, chunk_count: 0, created_at: '' }

const invokeMock = vi.fn()

const compileStatusCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'wiki:compileStatus').length

/** 排空 microtask（checkWikiCompileStatus 与轮询 tick 均为 async，fake timers 不自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  compileItems.length = 0
  ledgerItems.length = 0
  byProject.clear()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async () => ({ task: null, ingest_summaries: [] }))
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('ProjectDetailPage wiki compile heartbeat wiring', () => {
  it('fetches a snapshot baseline on mount and stays idle without active compile', async () => {
    render(<ProjectDetailPage project={project} />)
    await flush()
    // 挂载即拉（0.4 §6.3 启动间隙）：不等首个轮询 tick
    expect(compileStatusCount()).toBe(1)

    // 心跳无活跃编译：轮询不启动，计数不增
    await flush()
    expect(compileStatusCount()).toBe(1)
  })

  it('starts polling on first tick when heartbeat has a compile of this project', async () => {
    const { rerender } = render(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(1)

    // 心跳出现本项目活跃编译 → 轮询 enabled 翻真立即首跑
    compileItems.push({ project_id: 'p1' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(2)
  })

  it('ignores compiles of other projects', async () => {
    const { rerender } = render(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(1)

    compileItems.push({ project_id: 'other' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(1)
  })

  it('refetches exactly once on wikiCompileActive true→false edge (terminal snapshot)', async () => {
    const { rerender } = render(<ProjectDetailPage project={project} />)
    await flush()
    compileItems.push({ project_id: 'p1' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    expect(compileStatusCount()).toBe(2)

    // 心跳先行翻假（终态写入后 δ<5s 窗口，快照停在中间态）：边沿补拉恰一次
    compileItems.length = 0
    rerender(<ProjectDetailPage project={project} />)
    expect(compileStatusCount()).toBe(3)

    // 轮询已停，无额外拉取
    await flush()
    expect(compileStatusCount()).toBe(3)
  })

  it('derives tab pulse from heartbeat byProject compiles', async () => {
    byProject.set('p1', { files: 0, imageDescribing: 0, compiles: 1, reports: 0 })
    const { rerender, container } = render(<ProjectDetailPage project={project} />)
    await flush()
    // 编译中：pulse 出现
    expect(container.querySelector('.tabPulse')).toBeTruthy()

    // 无活跃编译：pulse 消失
    byProject.set('p1', { files: 0, imageDescribing: 0, compiles: 0, reports: 0 })
    rerender(<ProjectDetailPage project={project} />)
    expect(container.querySelector('.tabPulse')).toBeNull()
  })

  it('defaults to the affairs tab, first in TABS order (0.6 §3.4 V2 翻转决议)', async () => {
    const { container } = render(<ProjectDetailPage project={project} />)
    await flush()
    const tabs = Array.from(container.querySelectorAll('button'))
    const active = tabs.find((b) => b.className.includes('tabActive'))
    expect(active?.textContent).toContain('project.tab.affairs')
    // 顺序不变量（0.6 §6.3 规则 4）：事务恒第一（E1a plan T11 原定序，实施偏差 2026-10-24 补正）
    const tabBtns = tabs.filter((b) => b.textContent?.startsWith('project.tab.'))
    expect(tabBtns.map((b) => b.textContent)).toEqual([
      'project.tab.affairs', 'project.tab.wiki', 'project.tab.output', 'project.tab.materials',
    ])
  })

  it('refetches once per manual switch back to wiki (prev-tab guard)', async () => {
    const { getByText } = render(<ProjectDetailPage project={project} />)
    await flush()
    // 挂载恰 1 次（挂载无条件快照与 Tab 无关；默认事务 Tab 下切到 wiki 时由此守卫补拉防双拉）
    expect(compileStatusCount()).toBe(1)
    // 用户切走再切回：恰 +1（锁守卫「切到仍拉取」半边；删 effect 则停在 1）
    fireEvent.click(getByText('project.tab.materials'))
    fireEvent.click(getByText('project.tab.wiki'))
    await flush()
    expect(compileStatusCount()).toBe(2)
  })

  it('mounts PulseBar and refetches pulse data on compile terminal edge', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'wiki:listReviews') {
        return {
          reviews: [{ type: 'contradiction' }, { type: 'contradiction' }, { type: 'data-gap' }],
          counts: {
            contradiction: { pending: 2, resolved: 0, total: 2 },
            'data-gap': { pending: 1, resolved: 0, total: 1 },
            // broken-link 不计入 gap（V2 全量 review M-1：与后端 E4 桶/封面行两型口径对齐）
            'missing-page': { pending: 2, resolved: 0, total: 2 },
            'broken-link': { pending: 7, resolved: 0, total: 7 },
          },
        }
      }
      if (ch === 'wiki:listPages') return { pages: [], pending_compile_count: 5 }
      return { task: null, ingest_summaries: [] }
    })
    const { rerender, container } = render(<ProjectDetailPage project={project} />)
    await flush()
    // 挂载注入脉搏数据：矛盾分型计数 + 后端 pending_compile_count 直读（P11 裁决）；
    // gap = data-gap(1) + missing-page(2) 两型，broken-link(7) 不计入（M-1 口径对齐）
    const bar = container.querySelector('[data-testid="pulsebar"]')!
    expect(bar.getAttribute('data-contradiction')).toBe('2')
    expect(bar.getAttribute('data-gap')).toBe('3')
    expect(bar.getAttribute('data-pending')).toBe('5')

    // 活跃期推进两个轮询周期：listReviews 不随 #4 轮询增长（零轮询回归锁，若被轮询化则必然 >1）
    compileItems.push({ project_id: 'p1' })
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    await act(async () => { vi.advanceTimersByTime(11000) })
    expect(invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length).toBe(1)

    // 编译终态边沿（true→false）补拉：恰第二次，无轮询增量
    compileItems.length = 0
    rerender(<ProjectDetailPage project={project} />)
    await flush()
    const reviewCalls = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
    expect(reviewCalls).toBe(2)
  })

  it('wires ai status + suggestion count, refires project refresh on ledger_parse edge (3.0 P4)', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'wiki:listReviews') return { reviews: [] }
      if (ch === 'wiki:listPages') return { pages: [], pending_compile_count: 0 }
      // 分页契约形态（0.1 §16）：徽章消费服务端 total 而非数组长度（ProjectDetailPage L141）
      if (ch === 'affairs:listSuggestions') return { suggestions: [{ id: 's1' }, { id: 's2' }], total: 2, has_more: false }
      return { task: null, ingest_summaries: [] }
    })
    const onDataChanged = vi.fn()
    const withAi: Project = { ...project, pulse_ai_status: '投产 3 个月,建议确认整改。' }
    const { rerender, container } = render(
      <ProjectDetailPage project={withAi} onProjectDataChanged={onDataChanged} />)
    await flush()
    const bar = container.querySelector('[data-testid="pulsebar"]')!
    expect(bar.getAttribute('data-ai')).toContain('投产 3 个月')       // projects 行直传
    expect(bar.getAttribute('data-suggestions')).toBe('2')            // 挂载拉建议计数
    expect(onDataChanged).not.toHaveBeenCalled()

    // ledger_parse 活动从有到无边沿 → onProjectDataChanged(新 AI 句浮现,3.1 §4.3)
    ledgerItems.push({ project_id: 'p1' })
    rerender(<ProjectDetailPage project={withAi} onProjectDataChanged={onDataChanged} />)
    await flush()
    expect(onDataChanged).not.toHaveBeenCalled()                      // 活跃中不触发
    ledgerItems.length = 0
    rerender(<ProjectDetailPage project={withAi} onProjectDataChanged={onDataChanged} />)
    await flush()
    expect(onDataChanged).toHaveBeenCalledTimes(1)                    // 终态恰一次
  })
})

describe('ProjectDetailPage edit project modal (todo: fields editable after create)', () => {
  const companyProject: Project = {
    ...project,
    description: '老描述',
    company_name: '旧公司',
    credit_code: '91110000OLDCODE',
  }

  it('opens with prefilled fields and saves all four via knowledge:updateProject', async () => {
    const onDataChanged = vi.fn()
    const { getByText, container } = render(
      <ProjectDetailPage project={companyProject} onProjectDataChanged={onDataChanged} />,
    )
    await flush()
    // 头部铅笔按钮（title/aria 为 i18n key，icon 内容不参与文本匹配）
    fireEvent.click(container.querySelector('.editBtn')!)
    // 弹层 4 字段全量回填
    const inputs = container.querySelectorAll('.editModal input')
    expect(inputs.length).toBe(4)
    expect((inputs[0] as HTMLInputElement).value).toBe('P')
    expect((inputs[1] as HTMLInputElement).value).toBe('老描述')
    expect((inputs[2] as HTMLInputElement).value).toBe('旧公司')
    expect((inputs[3] as HTMLInputElement).value).toBe('91110000OLDCODE')
    // 改名称 + 公司名保存：恒传 4 字段（所见即所得，清空 = 置空）
    fireEvent.change(inputs[0], { target: { value: '新项目名' } })
    fireEvent.change(inputs[2], { target: { value: '新公司' } })
    fireEvent.click(getByText('project.save'))
    await flush()
    const call = invokeMock.mock.calls.find((c) => c[0] === 'knowledge:updateProject')
    expect(call?.[1]).toEqual({
      projectId: 'p1',
      name: '新项目名',
      description: '老描述',
      company_name: '新公司',
      credit_code: '91110000OLDCODE',
    })
    expect(onDataChanged).toHaveBeenCalledTimes(1)   // App 层 loadProjects 刷新 project prop
    expect(container.querySelector('.editModal')).toBeNull()   // 保存成功后弹层关闭
  })

  it('disables save and refuses to invoke when name is blank', async () => {
    const { getByText, container } = render(<ProjectDetailPage project={companyProject} />)
    await flush()
    fireEvent.click(container.querySelector('.editBtn')!)
    const nameInput = container.querySelector('.editModal input') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: '   ' } })
    const saveBtn = getByText('project.save').closest('button') as HTMLButtonElement
    expect(saveBtn.disabled).toBe(true)
    fireEvent.click(saveBtn)
    await flush()
    expect(invokeMock.mock.calls.some((c) => c[0] === 'knowledge:updateProject')).toBe(false)
  })

  it('closes the edit modal when switching to another project (no cross-project write)', async () => {
    const { container, rerender } = render(<ProjectDetailPage project={companyProject} />)
    await flush()
    fireEvent.click(container.querySelector('.editBtn')!)
    expect(container.querySelector('.editModal')).not.toBeNull()
    // 弹层开着时切项目（活动 Toast「查看」/OS 通知点击可绕过全屏遮罩）：
    // 弹层必须随 project.id 变化关闭，否则保存会把旧项目字段串写到新项目
    rerender(<ProjectDetailPage project={{ ...companyProject, id: 'p2', name: '另一个项目' }} />)
    await flush()
    expect(container.querySelector('.editModal')).toBeNull()
    expect(invokeMock.mock.calls.some((c) => c[0] === 'knowledge:updateProject')).toBe(false)
  })
})
