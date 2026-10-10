/** tests/TaskDrawer.test.tsx — 3.0 二期 Task 11:完成/跳过/恢复/删除确认/发起报告 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const invokeMock = vi.fn()
const confirmMock = vi.fn()
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))
vi.mock('../src/features/project/affairs/TaskDrawer.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { TaskDrawer } from '../src/features/project/affairs/TaskDrawer'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve() })
  }
}

const base = { projectId: 'p1', onClose: vi.fn(), onChanged: vi.fn(),
  onEdit: vi.fn(), onOpenReports: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  invokeMock.mockResolvedValue({ task: {} })
  confirmMock.mockResolvedValue(true)
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('TaskDrawer', () => {
  it('marks open task done via updateTask and notifies parent', async () => {
    const { getByText } = render(<TaskDrawer {...base} task={task()} />)
    fireEvent.click(getByText('affairs.drawer.done').closest('button')!)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask',
      expect.objectContaining({ projectId: 'p1', taskId: 't1', status: 'done' }))
    expect(base.onChanged).toHaveBeenCalled()
  })

  it('skips open task and offers reopen for done', async () => {
    const { getByText } = render(<TaskDrawer {...base} task={task()} />)
    fireEvent.click(getByText('affairs.drawer.skip').closest('button')!)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask',
      expect.objectContaining({ status: 'skipped' }))
    const { getByText: get2 } = render(<TaskDrawer {...base} task={task({ status: 'done' })} />)
    expect(get2('affairs.drawer.reopen')).toBeTruthy()
  })

  it('deletes via confirm dialog (danger) and closes', async () => {
    const { getByText } = render(<TaskDrawer {...base} task={task()} />)
    fireEvent.click(getByText('affairs.drawer.delete').closest('button')!)
    await flush()
    expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ danger: true }))
    expect(invokeMock).toHaveBeenCalledWith('affairs:deleteTask',
      expect.objectContaining({ projectId: 'p1', taskId: 't1' }))
    expect(base.onClose).toHaveBeenCalled()
  })

  it('shows start-report for report tasks and fires onOpenReports', () => {
    const { getByText, queryByText } = render(
      <TaskDrawer {...base} task={task({ task_type: 'report', title: '初评报告' })} />)
    expect(queryByText('affairs.drawer.uploadHint')).toBeNull()   // 仅 material 显示
    fireEvent.click(getByText('affairs.drawer.startReport').closest('button')!)
    expect(base.onOpenReports).toHaveBeenCalled()
  })

  it('shows upload hint and auto-complete rule text for material', () => {
    const { container } = render(
      <TaskDrawer {...base} task={task({
        auto_complete: { on: 'file_parsed', match: 'BP|商业计划' } })} />)
    const text = container.textContent!
    expect(text).toContain('affairs.drawer.uploadHint')
    expect(text).toContain('affairs.drawer.ruleFile')
  })

  it('shows action-failed message and keeps drawer open when invoke rejects', async () => {
    // 失败不得静默(对齐 TaskFormModal saveFailed / AffairsTab loadFailed 原则)
    invokeMock.mockRejectedValueOnce(new Error('boom'))
    const { getByText, container } = render(<TaskDrawer {...base} task={task()} />)
    fireEvent.click(getByText('affairs.drawer.done').closest('button')!)
    await flush()
    expect(container.textContent).toContain('affairs.drawer.actionFailed')
    expect(base.onChanged).not.toHaveBeenCalled()
    expect(base.onClose).not.toHaveBeenCalled()
  })

  it('renders non-ISO due_date as-is instead of NaN overdue days', () => {
    // 坏值哨兵(与 PhaseBoard dueText 同口径):直调 API 可植入坏 due_date,
    // 字典序小于当日时旧实现会产「已逾期 NaN 天」
    const { container } = render(<TaskDrawer {...base} task={task({ due_date: '0000-bad' })} />)
    const text = container.textContent!
    expect(text).toContain('0000-bad')
    expect(text).not.toContain('NaN')
  })
})
