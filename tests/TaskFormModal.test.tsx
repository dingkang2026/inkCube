/** tests/TaskFormModal.test.tsx — 3.0 二期 Task 10:新建/节奏字段/编辑预填/预填截止日
 *  拉齐补丁增:完成匹配词字段(§6.1.7 清空=移除规则/报告类原样透传)。
 *  2026-10-22 放开 material:资料/周期新建与编辑均可设 file_parsed 匹配词。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const invokeMock = vi.fn()
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/affairs/TaskFormModal.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { TaskFormModal } from '../src/features/project/affairs/TaskFormModal'
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

const base = { projectId: 'p1', stage: 'before' as const,
  onClose: vi.fn(), onSaved: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  invokeMock.mockResolvedValue({ task: {} })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('TaskFormModal', () => {
  it('creates milestone with title and optional due date', async () => {
    const { getByPlaceholderText, getByText, container } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '拜访客户' } })
    fireEvent.change(container.querySelector('input[type="date"]')!,
      { target: { value: '2026-10-20' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      projectId: 'p1', title: '拜访客户', taskType: 'material', dueDate: '2026-10-20',
    }))
    expect(base.onSaved).toHaveBeenCalled()
    expect(base.onClose).toHaveBeenCalled()
  })

  it('hides phase select on create (server derives from project stage)', () => {
    const { container } = render(<TaskFormModal {...base} initial={null} />)
    expect(container.textContent).not.toContain('affairs.form.phaseLabel')
  })

  it('rejects invalid due_day and submits valid recurrence on create', async () => {
    const { getByPlaceholderText, getByText, container } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '周报' } })
    fireEvent.change(getByText('affairs.type.material').closest('select')!,
      { target: { value: 'recurring' } })
    fireEvent.change(getByPlaceholderText('affairs.form.dueDayPlaceholder'),
      { target: { value: 'abc' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(container.textContent).toContain('affairs.form.dueDayInvalid')
    expect(invokeMock).not.toHaveBeenCalled()

    fireEvent.change(getByPlaceholderText('affairs.form.dueDayPlaceholder'),
      { target: { value: '20' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      recurrenceRule: { freq: 'monthly', due_day: 20 },
    }))
  })

  it('prefills from initial, disables type select, sends phase on edit', async () => {
    const { getByDisplayValue, getByText } = render(
      <TaskFormModal {...base} initial={task({ title: '尽调材料', phase: 'during', due_date: '2026-10-18' })} />)
    expect(getByDisplayValue('尽调材料')).toBeTruthy()
    const typeSelect = getByText('affairs.type.material').closest('select') as HTMLSelectElement
    expect(typeSelect.disabled).toBe(true)   // 仓内无 jest-dom,用 DOM 属性断言(计划缺陷修正)
    fireEvent.change(getByText('affairs.form.phaseLabel').closest('.fieldRow')!
      .querySelector('select')!, { target: { value: 'after' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', phase: 'after', dueDate: '2026-10-18',
    }))
  })

  it('prefills due date from calendar prefill prop', () => {
    const { container } = render(
      <TaskFormModal {...base} initial={null} prefillDueDate="2026-11-05" />)
    expect((container.querySelector('input[type="date"]') as HTMLInputElement).value).toBe('2026-11-05')
  })

  it('preserves recurrence_rule and due on editing non-recurring seeded task', async () => {
    // report+rule 种子(§3.3,如「产出年度项目服务报告」)经 RhythmCards「调整节奏」
    // 编辑:规则必须原样透传,回传 null 会抹规则致周期任务永久停止滚动(评审 C-1)
    const rule = { freq: 'yearly' as const, due_day: '12-31' }
    const { getByText } = render(
      <TaskFormModal {...base} initial={task({
        task_type: 'report', title: '产出年度项目服务报告',
        recurrence_rule: rule, due_date: '2026-12-31',
      })} />)
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', recurrenceRule: rule, dueDate: '2026-12-31',
    }))
  })

  it('keeps existing due_date when editing recurring rhythm', async () => {
    // roll 只补缺失 occ 不重算已存在实例:显式回传 null 会清空当期 due(评审 M-5)
    const { getByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={task({
        task_type: 'recurring', recurrence_rule: { freq: 'monthly', due_day: 20 },
        due_date: '2026-10-20',
      })} />)
    fireEvent.change(getByPlaceholderText('affairs.form.dueDayPlaceholder'),
      { target: { value: '25' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', recurrenceRule: { freq: 'monthly', due_day: 25 },
      dueDate: '2026-10-20',
    }))
  })

  it('rejects MM-DD input for monthly freq (freq-aware validation)', () => {
    // 表单与后端 parse_rule 同口径:monthly 不接受 MM-DD 格式(评审 W-3)
    const { getByPlaceholderText, getByText, container } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '周报' } })
    fireEvent.change(getByText('affairs.type.material').closest('select')!,
      { target: { value: 'recurring' } })
    fireEvent.change(getByPlaceholderText('affairs.form.dueDayPlaceholder'),
      { target: { value: '13-01' } })
    fireEvent.click(getByText('affairs.form.save'))
    expect(container.textContent).toContain('affairs.form.dueDayInvalid')
    expect(invokeMock).not.toHaveBeenCalled()
  })

  it('shows save-failed message and keeps modal open when invoke rejects', async () => {
    invokeMock.mockRejectedValueOnce(new Error('boom'))
    const { getByPlaceholderText, getByText, container } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '拜访客户' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(container.textContent).toContain('affairs.form.saveFailed')
    expect(base.onClose).not.toHaveBeenCalled()
  })

  it('creates recurring with match word as file_parsed auto-complete', async () => {
    const { getByPlaceholderText, getByText, queryByPlaceholderText } = render(
      <TaskFormModal {...base} initial={null} />)
    expect(queryByPlaceholderText('affairs.form.matchPlaceholder')).not.toBeNull()  // material 已同步放开(2026-10-22)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '企业月报' } })
    fireEvent.change(getByText('affairs.type.material').closest('select')!,
      { target: { value: 'recurring' } })
    fireEvent.change(getByPlaceholderText('affairs.form.matchPlaceholder'),
      { target: { value: ' 月报 ' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      taskType: 'recurring', autoComplete: { on: 'file_parsed', match: '月报' },   // trim
    }))
  })

  it('creates recurring with empty match as null (manual completion)', async () => {
    const { getByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '企业月报' } })
    fireEvent.change(getByText('affairs.type.material').closest('select')!,
      { target: { value: 'recurring' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      taskType: 'recurring', autoComplete: null,
    }))
  })

  it('clears stale match input when switching type (field stays for material)', async () => {
    // 切类型即清空输入(防残留误配):recurring 填词切回 material,字段保留但值已空
    const { getByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '拜访客户' } })
    const typeSelect = getByText('affairs.type.material').closest('select')!
    fireEvent.change(typeSelect, { target: { value: 'recurring' } })
    fireEvent.change(getByPlaceholderText('affairs.form.matchPlaceholder'),
      { target: { value: '月报' } })
    fireEvent.change(typeSelect, { target: { value: 'material' } })
    expect((getByPlaceholderText('affairs.form.matchPlaceholder') as HTMLInputElement).value).toBe('')
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      taskType: 'material', autoComplete: null,
    }))
  })

  it('edits file_parsed instance: prefills and rewords the match', async () => {
    const { getByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={task({
        task_type: 'material', auto_complete: { on: 'file_parsed', match: '月报' },
      })} />)
    const match = getByPlaceholderText('affairs.form.matchPlaceholder') as HTMLInputElement
    expect(match.value).toBe('月报')   // 预填
    fireEvent.change(match, { target: { value: '季报' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', autoComplete: { on: 'file_parsed', match: '季报' },
    }))
  })

  it('clearing the match on edit removes the auto-complete rule', async () => {
    const { getByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={task({
        task_type: 'material', auto_complete: { on: 'file_parsed', match: '月报' },
      })} />)
    fireEvent.change(getByPlaceholderText('affairs.form.matchPlaceholder'),
      { target: { value: '' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', autoComplete: null,
    }))
  })

  it('hides match field for report_completed instance and passes it through untouched', async () => {
    const { queryByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={task({
        task_type: 'report', auto_complete: { on: 'report_completed' },
      })} />)
    expect(queryByPlaceholderText('affairs.form.matchPlaceholder')).toBeNull()
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', autoComplete: { on: 'report_completed' },   // 原样透传不被抹(§6.4-1)
    }))
  })

  it('creates material with match word as file_parsed auto-complete (2026-10-22 放开)', async () => {
    // 手动新建资料任务也可配自动完成:与模板种子同链(后端 createTask 原样接收)
    const { getByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '收集尽调材料' } })
    fireEvent.change(getByPlaceholderText('affairs.form.matchPlaceholder'),
      { target: { value: ' 尽调 ' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      taskType: 'material', autoComplete: { on: 'file_parsed', match: '尽调' },   // trim
    }))
  })

  it('adds match rule when editing a manual material task without one', async () => {
    // 编辑态同步放开:无规则手动资料任务可补配自动完成(showMatch 覆盖全部 material)
    const { getByPlaceholderText, getByText } = render(
      <TaskFormModal {...base} initial={task({ task_type: 'material', auto_complete: null })} />)
    fireEvent.change(getByPlaceholderText('affairs.form.matchPlaceholder'),
      { target: { value: 'BP' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', autoComplete: { on: 'file_parsed', match: 'BP' },
    }))
  })

  it('hides match field for milestone (no file_parsed loop)', () => {
    // 节点类无自动完成闭环:切到 milestone 匹配字段隐藏
    const { getByText, queryByPlaceholderText } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByText('affairs.type.material').closest('select')!,
      { target: { value: 'milestone' } })
    expect(queryByPlaceholderText('affairs.form.matchPlaceholder')).toBeNull()
  })
})
