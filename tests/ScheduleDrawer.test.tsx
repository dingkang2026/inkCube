/**
 * tests/ScheduleDrawer.test.tsx — 期次三态抽屉容器契约单测（0.6 §3.2 第二步，
 * 2026-10-26）。
 *
 * 覆盖：默认宽 = 视口 60%（小视口夹至下限 420，2026-10-26 由固定 760px 改比例）；
 * 三态头部「返回」按钮 → onClose（头部返回/Esc/点遮罩三通道等价收口）；
 * read↔replay 抽屉内互切走 onViewChange 不关抽屉；Esc 关闭（输入框聚焦豁免）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ScheduleDrawer, type ScheduleView } from '../src/features/schedule/ScheduleDrawer'

// 三态子视图打桩：桩内渲染按钮触发各自回调，props 留存供契约断言
const propsState = vi.hoisted(() => ({
  read: null as Record<string, unknown> | null,
  replay: null as Record<string, unknown> | null,
  archive: null as Record<string, unknown> | null,
}))

vi.mock('../src/features/schedule/ReadView', () => ({
  ReadView: (props: Record<string, unknown>) => {
    propsState.read = props
    return <button data-testid="mock-read-back" onClick={() => (props.onBack as () => void)()} />
  },
}))
vi.mock('../src/features/schedule/RunReplay', () => ({
  RunReplay: (props: Record<string, unknown>) => {
    propsState.replay = props
    return <button data-testid="mock-replay-back" onClick={() => (props.onBack as () => void)()} />
  },
}))
vi.mock('../src/features/schedule/ArchiveView', () => ({
  ArchiveView: (props: Record<string, unknown>) => {
    propsState.archive = props
    return <button data-testid="mock-archive-back" onClick={() => (props.onBack as () => void)()} />
  },
}))

const setViewport = (w: number) => {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
}

const READ: ScheduleView = { type: 'read', runId: 'run_1' }
const REPLAY: ScheduleView = { type: 'replay', runId: 'run_1', taskId: 't1' }
const ARCHIVE: ScheduleView = {
  type: 'archive', targetId: 'p1', fileId: 'f1', scheduleName: '华润舆情',
}

const mountDrawer = (view: ScheduleView, onClose = vi.fn(), onViewChange = vi.fn()) => {
  const utils = render(
    <ScheduleDrawer view={view} onClose={onClose} onViewChange={onViewChange} />,
  )
  return { onClose, onViewChange, container: utils.container, unmount: utils.unmount }
}

beforeEach(() => {
  propsState.read = null
  propsState.replay = null
  propsState.archive = null
  setViewport(1000)
})

describe('ScheduleDrawer（0.6 §3.2 三态抽屉容器）', () => {
  it('默认宽 = 视口 60%（1000 → 600px）', () => {
    const { container, unmount } = mountDrawer(READ)
    const panel = container.querySelector('[data-testid="schedule-drawer"]') as HTMLElement
    expect(panel.style.width).toBe('600px')
    unmount()
  })

  it('小视口默认宽夹至下限 420（500 → 420px）', () => {
    setViewport(500)
    const { container, unmount } = mountDrawer(READ)
    const panel = container.querySelector('[data-testid="schedule-drawer"]') as HTMLElement
    expect(panel.style.width).toBe('420px')
    unmount()
  })

  it('三态头部「返回」按钮 → onClose（与 Esc/点遮罩三通道等价）', () => {
    const r1 = mountDrawer(READ)
    fireEvent.click(screen.getByTestId('mock-read-back'))
    expect(r1.onClose).toHaveBeenCalledTimes(1)
    r1.unmount()

    const r2 = mountDrawer(REPLAY)
    fireEvent.click(screen.getByTestId('mock-replay-back'))
    expect(r2.onClose).toHaveBeenCalledTimes(1)
    r2.unmount()

    const r3 = mountDrawer(ARCHIVE)
    fireEvent.click(screen.getByTestId('mock-archive-back'))
    expect(r3.onClose).toHaveBeenCalledTimes(1)
    r3.unmount()
  })

  it('read「执行过程」→ onViewChange(replay 同 runId)，抽屉不直接关闭', () => {
    const { onViewChange, unmount } = mountDrawer(READ)
    ;(propsState.read!.onReplay as (taskId: string) => void)('t1')
    expect(onViewChange).toHaveBeenCalledWith({ type: 'replay', runId: 'run_1', taskId: 't1' })
    unmount()
  })

  it('replay「查看报告」→ onViewChange(read 同 runId)', () => {
    const { onViewChange, unmount } = mountDrawer(REPLAY)
    ;(propsState.replay!.onOpenReport as () => void)()
    expect(onViewChange).toHaveBeenCalledWith({ type: 'read', runId: 'run_1' })
    unmount()
  })

  it('archive 态透传三 props（targetId/fileId/scheduleName）', () => {
    const { unmount } = mountDrawer(ARCHIVE)
    expect(propsState.archive).toMatchObject({
      targetId: 'p1', fileId: 'f1', scheduleName: '华润舆情',
    })
    unmount()
  })

  it('Esc 关抽屉；输入框聚焦时豁免（FindBar 两段式关闭）', () => {
    const { onClose, unmount } = mountDrawer(READ)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)

    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1) // 输入框聚焦：不关
    input.remove()
    unmount()
  })

  it('点遮罩关抽屉（无拖动/选字时 suppressClick 不触发）', () => {
    const { onClose, unmount } = mountDrawer(READ)
    fireEvent.click(screen.getByTestId('schedule-drawer-overlay'))
    expect(onClose).toHaveBeenCalledTimes(1)
    unmount()
  })
})
