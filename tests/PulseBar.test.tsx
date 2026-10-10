/** tests/PulseBar.test.tsx — 脉搏带 v1:chips 显隐/状态句/折叠持久化/「一切正常」态(3.0 §2/§6.1.1)
 *  心跳 mock:无参形态返回 byProject(kind 分支返回 items);CSS Proxy 同 ProjectDetailPage.test。
 *  localStorage 用普通对象替身,折叠偏好断言可精确回读。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const byProject = new Map<string, { files: number; compiles: number; reports: number }>()
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) =>
    kind === 'wiki_compile'
      ? { items: [] }
      : { byProject, items: [] },
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }))
vi.mock('../src/features/project/PulseBar.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, cleanup, fireEvent } from '@testing-library/react'
import { PulseBar } from '../src/features/project/PulseBar'

const base = { projectId: 'p1', stageLabel: '推进中', pendingCompile: 0, onOpenWiki: vi.fn() }
const store: Record<string, string> = {}
beforeEach(() => {
  byProject.clear()
  Object.keys(store).forEach((k) => delete store[k])
  ;(global as unknown as { localStorage: unknown }).localStorage = {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
  }
  vi.clearAllMocks()
})
afterEach(() => cleanup())

describe('PulseBar v1', () => {
  it('shows all-clear row when no signals', () => {
    const { container } = render(<PulseBar {...base} reviews={{ contradiction: 0, gap: 0 }} />)
    expect(container.textContent).toContain('pulse.allClear')
    expect(container.querySelectorAll('.chip').length).toBe(0)
  })

  it('shows failure sentence instead of all-clear when loadFailed (review minor②)', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 0, gap: 0 }} loadFailed />
    )
    const text = container.textContent!
    expect(text).toContain('pulse.loadFailed')
    expect(text).not.toContain('pulse.allClear') // 首拉失败时计数全 0,不得呈现假「一切正常」
    expect(container.querySelectorAll('.chip').length).toBe(0)
  })

  it('renders contradiction/gap/pendingCompile chips with counts, hides zero chips', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 3, gap: 5 }} pendingCompile={5} />
    )
    const text = container.textContent!
    expect(text).toContain('pulse.chipContradiction')
    expect(text).toContain('3')
    expect(text).toContain('pulse.chipGap')
    expect(text).toContain('pulse.chipPendingCompile')
    expect(text).not.toContain('pulse.chipReports') // 无生成中报告 → 隐藏
  })

  it('shows reports chip when heartbeat has active reports', () => {
    byProject.set('p1', { files: 0, compiles: 0, reports: 1 })
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 0, gap: 0 }} />
    )
    expect(container.textContent).toContain('pulse.chipReports')
  })

  it('renders status sentence with stage label and top signal', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 3, gap: 0 }} />
    )
    expect(container.textContent).toContain('推进中')
    expect(container.textContent).toContain('pulse.sentenceContradiction')
  })

  it('persists collapsed state per project', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 1, gap: 0 }} />
    )
    // 折叠动作:点击头部「收起」按钮(collapseBtn 仅展开态渲染);fireEvent 自带 act 提交重渲染
    fireEvent.click(container.querySelector<HTMLButtonElement>('.collapseBtn')!)
    expect(store['pulsebar:collapsed:p1']).toBe('1')
    expect(container.textContent).toContain('pulse.expand') // 折叠态单行,出现「展开」提示
    expect(container.textContent).not.toContain('pulse.collapse')
  })

  it('reads stored preference on mount and re-syncs on projectId change', () => {
    store['pulsebar:collapsed:p1'] = '1'
    const { rerender, container } = render(
      <PulseBar {...base} reviews={{ contradiction: 1, gap: 0 }} />
    )
    expect(container.textContent).toContain('pulse.expand') // p1 有偏好 → 挂载即折叠
    // 项目切换不重挂载(同页面 re-sync 模式):p2 无偏好 → 恢复展开,且不污染 p2 偏好
    rerender(<PulseBar {...base} projectId="p2" reviews={{ contradiction: 1, gap: 0 }} />)
    expect(container.textContent).toContain('pulse.collapse')
    expect(store['pulsebar:collapsed:p2']).toBeUndefined()
  })

  it('invokes onOpenWiki with lint for contradiction chip click', () => {
    const onOpenWiki = vi.fn()
    const { getByText } = render(
      <PulseBar {...base} onOpenWiki={onOpenWiki} reviews={{ contradiction: 1, gap: 0 }} />
    )
    fireEvent.click(getByText(/pulse\.chipContradiction/).closest('button')!)
    expect(onOpenWiki).toHaveBeenCalledWith('lint')
  })
})

describe('PulseBar due tasks (3.0 二期 Task 12)', () => {
  it('shows due chip with combined overdue+week count and fires onOpenAffairs', () => {
    const onOpenAffairs = vi.fn()
    const { getByText } = render(
      <PulseBar {...base} onOpenAffairs={onOpenAffairs} reviews={{ contradiction: 0, gap: 0 }}
        dueTasks={{ overdue: 2, weekDue: 3, nearestOverdueTitle: '上传BP', nearestOverdueDays: 3 }} />
    )
    const chip = getByText(/pulse\.chipDue/).closest('button')!
    expect(chip.textContent).toContain('5')
    fireEvent.click(chip)
    expect(onOpenAffairs).toHaveBeenCalled()
  })

  it('elevates overdue sentence above contradiction/gap', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 1, gap: 1 }}
        dueTasks={{ overdue: 2, weekDue: 0, nearestOverdueTitle: null, nearestOverdueDays: 0 }} />
    )
    const text = container.textContent!
    expect(text).toContain('pulse.sentenceOverdue')
    expect(text).not.toContain('pulse.sentenceContradiction')
  })

  it('shows overdue badge in collapsed row', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 0, gap: 0 }}
        dueTasks={{ overdue: 1, weekDue: 0, nearestOverdueTitle: null, nearestOverdueDays: 1 }} />
    )
    fireEvent.click(container.querySelector('.collapseBtn')!)
    const folded = container.querySelector('.foldedRow')!
    expect(folded.textContent).toContain('1')
  })

  it('adds compile view to pending chip click (裁决 6,Task 13)', () => {
    const onOpenWiki = vi.fn()
    const { getByText } = render(
      <PulseBar {...base} onOpenWiki={onOpenWiki} reviews={{ contradiction: 0, gap: 0 }}
        pendingCompile={4} />
    )
    fireEvent.click(getByText(/pulse\.chipPendingCompile/).closest('button')!)
    expect(onOpenWiki).toHaveBeenCalledWith('compile')
  })
})
