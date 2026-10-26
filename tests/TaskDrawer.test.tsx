/**
 * tests/TaskDrawer.test.tsx — 执行抽屉单测(3.0 §6.1.7)。
 *
 * 宿主行为:遮罩点击关闭(busy 豁免保留)、拖宽/正文选字松手于遮罩不误关
 * (浏览器合成 click 落 mousedown/mouseup 公共祖先 = overlay,suppressClick
 * 守卫回归,2026-10-25 review 发现后修)。
 * 动作链:完成/跳过/恢复(affairs:updateTask + onChanged)、删除确认流
 * (useConfirm danger → affairs:deleteTask → onClose,取消则不动)、invoke
 * 拒绝显示 actionFailed(失败不静默)、report 类 startReport → onOpenReports、
 * material 类 uploadHint/ruleFile、非 ISO due_date 原文哨兵(不产 NaN 碎片)。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { TaskDrawer } from '../src/features/project/affairs/TaskDrawer'
import type { PhaseTask } from '../src/features/project/affairs/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

// useConfirm 打桩(不渲染真实确认 UI);value 切换确认/取消两路
const mockConfirmResult = { value: true }
vi.mock('../src/components/ConfirmDialog', () => ({
  ConfirmProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useConfirm: () => async () => mockConfirmResult.value,
}))

const TASK: PhaseTask = {
  id: 't1',
  project_id: 'p1',
  phase: 'during',
  task_type: 'milestone',
  title: '任务A',
  description: '',
  template_id: null,
  status: 'open',
  occurrence_key: null,
  due_date: null,
  recurrence_rule: null,
  auto_complete: null,
  linked_entity_type: null,
  linked_entity_id: null,
  completed_at: null,
  completed_by: null,
  created_at: '2026-10-25T00:00:00Z',
}

const withOverrides = (o: Partial<PhaseTask>): PhaseTask => ({ ...TASK, ...o })

const invokeMock = vi.fn()

describe('TaskDrawer(3.0 §6.1.7 执行抽屉)', () => {
  const renderView = (
    task: PhaseTask = TASK,
    onClose: () => void = () => {},
    onChanged: () => void = () => {},
    onOpenReports: () => void = () => {},
  ) =>
    render(
      <TaskDrawer
        task={task}
        projectId="p1"
        onClose={onClose}
        onChanged={onChanged}
        onEdit={() => {}}
        onOpenReports={onOpenReports}
      />,
    )

  beforeEach(() => {
    mockConfirmResult.value = true
    invokeMock.mockReset()
    invokeMock.mockResolvedValue({ ok: true })
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: { invoke: invokeMock },
    })
  })

  /* ── 宿主行为(遮罩/拖宽/守卫) ── */

  it('点遮罩关闭;点 panel 内不关(stopPropagation)', () => {
    const onClose = vi.fn()
    const { container } = renderView(TASK, onClose)
    const overlay = container.querySelector('[class*="overlay"]') as HTMLElement
    const panel = container.querySelector('[class*="panel"]') as HTMLElement
    fireEvent.click(overlay)
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(panel)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('busy 在途(patch 未返回)点遮罩不关;完成后恢复可关', async () => {
    let resolvePatch!: (v: unknown) => void
    invokeMock.mockImplementationOnce(
      () => new Promise((r) => { resolvePatch = r }),
    )
    const onClose = vi.fn()
    const { container } = renderView(TASK, onClose)
    const overlay = container.querySelector('[class*="overlay"]') as HTMLElement
    // 「完成」→ patch 在途(busy),遮罩点击被豁免
    fireEvent.click(screen.getByText('affairs.drawer.done'))
    fireEvent.click(overlay)
    expect(onClose).not.toHaveBeenCalled()
    resolvePatch(undefined)
    await waitFor(() =>
      expect((screen.getByText('affairs.drawer.done') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(overlay)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('拖宽松手于遮罩:合成 click 落 overlay 不误关(守卫只吞松手后一个 tick)', async () => {
    const onClose = vi.fn()
    const { container } = renderView(TASK, onClose)
    const overlay = container.querySelector('[class*="overlay"]') as HTMLElement
    const handle = container.querySelector('[class*="dragHandle"]') as HTMLElement
    // 真实浏览器:mouseup 于 overlay 时,click 派发到 mousedown/mouseup 目标的
    // 公共祖先 = overlay 本身(绕过 panel stopPropagation)——松手即误关的回归
    fireEvent.mouseDown(handle)
    fireEvent.mouseUp(overlay)
    fireEvent.click(overlay)
    expect(onClose).not.toHaveBeenCalled()
    await new Promise((r) => setTimeout(r, 0))  // 守卫 tick 复位
    fireEvent.click(overlay)                    // 其后正常点击遮罩照常关闭
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('panel 内选字拖到遮罩松手:合成 click 同样被吞(守卫覆盖 mousedown 于 panel)', async () => {
    const onClose = vi.fn()
    const { container } = renderView(TASK, onClose)
    const overlay = container.querySelector('[class*="overlay"]') as HTMLElement
    const panel = container.querySelector('[class*="panel"]') as HTMLElement
    fireEvent.mouseDown(panel)  // 正文选字起步
    fireEvent.mouseUp(overlay)  // 拖出面板松手
    fireEvent.click(overlay)
    expect(onClose).not.toHaveBeenCalled()
    await new Promise((r) => setTimeout(r, 0))
    fireEvent.click(overlay)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  /* ── 动作链(完成/跳过/恢复/删除/失败反馈/类型变体) ── */

  it('「完成」→ affairs:updateTask({status:"done"}) + onChanged', async () => {
    const onChanged = vi.fn()
    renderView(TASK, () => {}, onChanged)
    fireEvent.click(screen.getByText('affairs.drawer.done'))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    const call = invokeMock.mock.calls.find((c) => c[0] === 'affairs:updateTask')
    expect(call?.[1]).toMatchObject({ projectId: 'p1', taskId: 't1', status: 'done' })
  })

  it('「跳过」→ affairs:updateTask({status:"skipped"})', async () => {
    const onChanged = vi.fn()
    renderView(TASK, () => {}, onChanged)
    fireEvent.click(screen.getByText('affairs.drawer.skip'))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    const call = invokeMock.mock.calls.find((c) => c[0] === 'affairs:updateTask')
    expect(call?.[1]).toMatchObject({ status: 'skipped' })
  })

  it('done 态「恢复」→ affairs:updateTask({status:"open"})', async () => {
    const onChanged = vi.fn()
    renderView(withOverrides({ status: 'done' }), () => {}, onChanged)
    fireEvent.click(screen.getByText('affairs.drawer.reopen'))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    const call = invokeMock.mock.calls.find((c) => c[0] === 'affairs:updateTask')
    expect(call?.[1]).toMatchObject({ status: 'open' })
  })

  it('删除:确认 → affairs:deleteTask + onChanged + onClose', async () => {
    const onClose = vi.fn()
    const onChanged = vi.fn()
    renderView(TASK, onClose, onChanged)
    fireEvent.click(screen.getByText('affairs.drawer.delete'))
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(invokeMock.mock.calls.some((c) => c[0] === 'affairs:deleteTask')).toBe(true)
  })

  it('删除:取消(useConfirm false)→ 不调 deleteTask 不关', async () => {
    mockConfirmResult.value = false
    const onClose = vi.fn()
    renderView(TASK, onClose)
    fireEvent.click(screen.getByText('affairs.drawer.delete'))
    await new Promise((r) => setTimeout(r, 0))  // 让 confirm promise resolve
    expect(invokeMock.mock.calls.some((c) => c[0] === 'affairs:deleteTask')).toBe(false)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('invoke 拒绝 → actionFailed 提示出现(失败不静默)', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      invokeMock.mockRejectedValueOnce(new Error('ipc down'))
      renderView(TASK)
      fireEvent.click(screen.getByText('affairs.drawer.done'))
      await waitFor(() => expect(screen.getByText('affairs.drawer.actionFailed')).toBeTruthy())
    } finally {
      errSpy.mockRestore()
    }
  })

  it('report 类非 done → startReport → onOpenReports', () => {
    const onOpenReports = vi.fn()
    renderView(withOverrides({ task_type: 'report' }), () => {}, () => {}, onOpenReports)
    fireEvent.click(screen.getByText('affairs.drawer.startReport'))
    expect(onOpenReports).toHaveBeenCalledTimes(1)
  })

  it('material 类 open → uploadHint + ruleFile(auto_complete=file_parsed)', () => {
    renderView(withOverrides({
      task_type: 'material',
      auto_complete: { on: 'file_parsed', match: '*.pdf' },
    }))
    expect(screen.getByText('affairs.drawer.uploadHint')).toBeTruthy()
    expect(screen.getByText('affairs.drawer.ruleFile')).toBeTruthy()
  })

  it('非 ISO due_date → 原文哨兵显示,不产 NaN 天数碎片', () => {
    renderView(withOverrides({ due_date: '2026年10月' }))
    expect(screen.getByText('2026年10月')).toBeTruthy()
  })
})

