/**
 * tests/ScheduleFormModal.test.tsx — 订阅表单弹层单测（2.11 §12；2026-10-25 收敛
 * 后两类交互：类型由入口给定 createKind，不可中途切换）。
 *
 * 覆盖：
 * - 新建 rollup（createKind）：直达周期步（跳过模板步）、骨架预填默认模板（含
 *   {sections}）、汇总范围候选（排除 rollup 自身）+ 勾选 → 保存 payload 断言；
 * - 骨架被清空（无 {sections}）→ 保存禁用（后端 400 前置拦截）；
 * - 新建 intel 默认：payload scope/digestScheduleIds=null，语义零漂移；
 * - 编辑 rollup 回填：骨架/圈选回显 + update payload；
 * - 跨开合候选重拉（review m-1）：组件关闭不卸载，重开候选重新加载。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { ScheduleFormModal } from '../src/features/schedule/ScheduleFormModal'

vi.mock('react-i18next', () => ({
  // 默认骨架模板需返回含 {sections} 的真实形态（新建 rollup 预填断言依赖）
  useTranslation: () => ({
    t: (k: string) =>
      k === 'schedule.form.rollupPromptTemplate' ? '汇总各订阅期次\n{sections}' : k,
  }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => () => {},
}))

vi.mock('../src/features/task/useTaskTemplates', () => ({
  useTaskTemplates: () => ({ templates: [] }),
}))

vi.mock('../src/features/settings/useSettings', () => ({
  useSettings: () => ({ settings: {} }),
}))

// 情报订阅行基座（scope=NULL 两值语义收敛后）；rollup 行以 { ...ROW, scope: 'rollup' } 派生
const ROW = {
  id: 'sch_1', name: '华润舆情', prompt: '扫描并输出报告', category: 'corporate',
  interval_type: 'daily', interval_value: '09:00',
  target_type: null, target_id: null, auto_import: 1, enabled: 1,
  next_run_at: null, last_run_at: null, consecutive_failures: 0,
  scope: null, digest_schedule_ids: null,
  archive_last_run_id: null, archive_file_id: null, is_running: false,
  latest_done: null, created_at: '', updated_at: '',
}

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'schedules:list') {
      // 候选 id 用 sch_x：与编辑用例 editing.id(sch_1) 错开，防自聚合排除不误伤
      return {
        schedules: [
          { ...ROW, id: 'sch_x' },
          { ...ROW, id: 'sch_2', name: '组合周报甲', scope: 'rollup' },
        ],
        has_more: false,
      }
    }
    return { ok: true }
  })
  ;(window as { electronAPI?: unknown }).electronAPI = {
    invoke: invokeMock, on: () => () => {},
  }
})

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

const flush = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

/** 新建 intel 流走到第③步（自由输入 prompt → daily 默认周期） */
const renderToTargetStep = () => {
  render(<ScheduleFormModal open onClose={() => {}} onSaved={() => {}} />)
  fireEvent.click(screen.getByRole('tab', { name: 'schedule.form.freeInput' }))
  fireEvent.change(screen.getByLabelText('schedule.form.promptLabel'), {
    target: { value: '采集指令' },
  })
  fireEvent.click(screen.getByText('schedule.form.next'))
  fireEvent.click(screen.getByText('schedule.form.next'))
}

const lastInvoke = (channel: string) => {
  const calls = invokeMock.mock.calls.filter((c) => c[0] === channel)
  return calls[calls.length - 1]
}

describe('ScheduleFormModal（2.11 §12 收敛：两类订阅类型）', () => {
  it('新建 rollup（createKind）：跳过模板步、骨架预填含 {sections}、汇总范围排除 rollup；保存 payload 正确', async () => {
    render(<ScheduleFormModal open createKind="rollup" onClose={() => {}} onSaved={() => {}} />)
    // 直达周期步：来源步 tab 不在场，标题为组合周报
    expect(screen.queryByRole('tab', { name: 'schedule.form.freeInput' })).toBeNull()
    expect(screen.getByText('schedule.form.newRollupTitle')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 骨架预填默认模板（含 {sections}），保存可用
    const skeleton = screen.getByLabelText('schedule.form.rollupSkeletonLabel') as HTMLTextAreaElement
    expect(skeleton.value).toContain('{sections}')
    // 汇总范围候选：rollup 行被排除（防自聚合），仅情报订阅候选
    await waitFor(() => expect(screen.getByLabelText('华润舆情')).toBeTruthy())
    expect(screen.queryByLabelText('组合周报甲')).toBeNull()
    expect(lastInvoke('schedules:list')?.[1]).toMatchObject({ limit: 0 })
    fireEvent.click(screen.getByLabelText('华润舆情'))
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    const call = lastInvoke('schedules:create')
    expect(call).toBeTruthy()
    expect(call![1]).toMatchObject({
      scope: 'rollup',
      digestScheduleIds: ['sch_x'],
      category: 'general',
      targetType: null,
      targetId: null,
      autoImport: false,
    })
    expect((call![1] as { prompt: string }).prompt).toContain('{sections}')
  })

  it('骨架被清空（无 {sections}）→ 保存禁用', () => {
    render(<ScheduleFormModal open createKind="rollup" onClose={() => {}} onSaved={() => {}} />)
    fireEvent.click(screen.getByText('schedule.form.next'))
    const skeleton = screen.getByLabelText('schedule.form.rollupSkeletonLabel') as HTMLTextAreaElement
    fireEvent.change(skeleton, { target: { value: '占位符被我删了' } })
    expect((screen.getByText('schedule.form.save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('新建 intel 默认：payload scope/digestScheduleIds=null，去向语义零漂移', async () => {
    renderToTargetStep()
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    const call = lastInvoke('schedules:create')
    expect(call![1]).toMatchObject({
      scope: null,
      digestScheduleIds: null,
      category: 'corporate',
      prompt: '采集指令',
    })
  })

  it('编辑 rollup：骨架与圈选回填；保存走 update 且 payload 带现值', async () => {
    render(
      <ScheduleFormModal
        open
        editing={{
          ...ROW,
          name: '组合周报', scope: 'rollup',
          prompt: '骨架\n{sections}',
          digest_schedule_ids: ['sch_x'],
        } as never}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    // 编辑态直达周期步，从周期步前进
    fireEvent.click(screen.getByText('schedule.form.next'))
    const skeleton = screen.getByLabelText('schedule.form.rollupSkeletonLabel') as HTMLTextAreaElement
    expect(skeleton.value).toBe('骨架\n{sections}')
    await waitFor(() => expect(screen.getByLabelText('华润舆情')).toBeTruthy())
    expect((screen.getByLabelText('华润舆情') as HTMLInputElement).checked).toBe(true)
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    const call = lastInvoke('schedules:update')
    expect(call![1]).toMatchObject({
      id: 'sch_1', scope: 'rollup', digestScheduleIds: ['sch_x'],
    })
  })

  it('跨开合候选重拉（review m-1）：关闭重开后汇总范围候选重新加载', async () => {
    const { rerender } = render(
      <ScheduleFormModal open createKind="rollup" onClose={() => {}} onSaved={() => {}} />,
    )
    // 第一次：周期步前进到第③步，候选首拉（组件关闭不卸载，状态常驻）
    fireEvent.click(screen.getByText('schedule.form.next'))
    await waitFor(() => expect(screen.getByLabelText('华润舆情')).toBeTruthy())
    expect(invokeMock.mock.calls.filter((c) => c[0] === 'schedules:list').length).toBe(1)

    // 关闭 → 候选集变化（模拟会话中新建订阅）→ 重开走到第③步
    rerender(<ScheduleFormModal open={false} createKind="rollup" onClose={() => {}} onSaved={() => {}} />)
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:list') {
        return { schedules: [{ ...ROW, id: 'sch_new', name: '新增订阅' }], has_more: false }
      }
      return { ok: true }
    })
    rerender(<ScheduleFormModal open createKind="rollup" onClose={() => {}} onSaved={() => {}} />)
    // 新建分支重置 step='period'（rollup），再次前进到第③步
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 重拉后候选 = 新集合（旧候选不再残留，新增订阅入候选）
    await waitFor(() => expect(screen.getByLabelText('新增订阅')).toBeTruthy())
    expect(screen.queryByLabelText('华润舆情')).toBeNull()
    expect(invokeMock.mock.calls.filter((c) => c[0] === 'schedules:list').length).toBe(2)
  })
})
