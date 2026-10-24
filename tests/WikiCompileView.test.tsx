/** tests/WikiCompileView.test.tsx — 汇编成册视图（V2 E3 整理层）
 *  空闲 CTA / 空态 force 入口 / 乐观窗口占位（CodeReview Minor #2 回归）/
 *  CompilePanel 快照嵌入 / 明细折叠入口 / 终态增量再汇编；mock 惯例同 WikiTab 测试。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/project/tabs/CompilePanel', () => ({
  CompilePanel: ({ task }: { task: { status: string } }) => (
    <div data-testid="compile-panel">{task.status}</div>
  ),
}))

import { render, cleanup, fireEvent } from '@testing-library/react'
import { WikiCompileView } from '../src/features/project/tabs/WikiCompileView'

const makeTask = (status: string) => ({
  id: 't1',
  status,
  stage: null,
  pages_total: 3,
  pages_completed: 1,
  pages_failed: 0,
  error_message: null,
  created_at: '',
  completed_at: null,
})

const baseProps = {
  ingestSummaries: [] as any[],
  compiling: false,
  detailOpen: true,
  onOpenDetail: vi.fn(),
  onCollapseDetail: vi.fn(),
  onCompile: vi.fn(),
  onForceRecompile: vi.fn(),
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

describe('WikiCompileView (V2 E3)', () => {
  it('renders idle CTA and triggers compile', () => {
    const { getByText } = render(
      <WikiCompileView {...baseProps} pendingCount={2} compileTask={null} />,
    )
    fireEvent.click(getByText('开始汇编（2）'))
    expect(baseProps.onCompile).toHaveBeenCalledTimes(1)
  })

  it('renders empty state with force entry when nothing is pending', () => {
    const { getByText } = render(
      <WikiCompileView {...baseProps} pendingCount={0} compileTask={null} />,
    )
    fireEvent.click(getByText('重新汇编'))
    expect(baseProps.onForceRecompile).toHaveBeenCalledTimes(1)
  })

  it('shows a pending placeholder instead of blank body during the optimistic window (Minor #2 regression)', () => {
    // 已点开始、首个轮询快照未回：占位可见、CompilePanel 不渲染，正文不留白一个轮询周期
    const { getByText, queryByTestId } = render(
      <WikiCompileView {...baseProps} pendingCount={2} compileTask={null} compiling />,
    )
    expect(getByText('正在处理第一个文件...')).toBeTruthy()
    expect(queryByTestId('compile-panel')).toBeNull()
  })

  it('embeds CompilePanel once the task snapshot arrives', () => {
    const { getByTestId, queryByText } = render(
      <WikiCompileView {...baseProps} pendingCount={2} compileTask={makeTask('storing')} />,
    )
    expect(getByTestId('compile-panel').textContent).toBe('storing')
    expect(queryByText('正在处理第一个文件...')).toBeNull()
  })

  it('offers view-details entry while detail is collapsed', () => {
    const { getByText } = render(
      <WikiCompileView {...baseProps} pendingCount={2} compileTask={makeTask('storing')} detailOpen={false} />,
    )
    fireEvent.click(getByText('查看详情'))
    expect(baseProps.onOpenDetail).toHaveBeenCalledTimes(1)
  })

  it('shows incremental re-compile hero after a terminal state with pending files', () => {
    const { getByText } = render(
      <WikiCompileView {...baseProps} pendingCount={3} compileTask={makeTask('completed')} />,
    )
    expect(getByText('开始汇编（3）')).toBeTruthy()
  })
})
