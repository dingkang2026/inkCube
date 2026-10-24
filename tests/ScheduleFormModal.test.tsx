/**
 * tests/ScheduleFormModal.test.tsx — 订阅表单弹层单测（2.11 §12 V2.1-T2）。
 *
 * 覆盖（scope 三型交互，2.11 §12.4）：
 * - 新建 rollup：第③步三选切组合周报 → 去向区隐藏、骨架预填默认模板（含
 *   {sections}）、汇总范围候选（排除 rollup 自身）+ 勾选 → 保存 payload 断言；
 * - 骨架被清空（无 {sections}）→ 保存禁用（后端 400 前置拦截）；
 * - 默认 focus：payload scope/digestScheduleIds=null 原语义零漂移；
 * - 编辑 rollup 回填：骨架/圈选回显 + update payload。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { ScheduleFormModal } from '../src/features/schedule/ScheduleFormModal'

vi.mock('react-i18next', () => ({
  // 默认骨架模板需返回含 {sections} 的真实形态（switchScope 预填值断言依赖）
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

const ROW = {
  id: 'sch_1', name: '华润舆情', prompt: '扫描并输出报告', category: 'corporate',
  interval_type: 'daily', interval_value: '09:00',
  target_type: null, target_id: null, auto_import: 1, enabled: 1,
  next_run_at: null, last_run_at: null, consecutive_failures: 0,
  scope: 'focus', digest_schedule_ids: null,
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

/** 新建流走到第③步（自由输入 prompt → daily 默认周期） */
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

describe('ScheduleFormModal（2.11 §12 scope 三型）', () => {
  it('新建 rollup：骨架预填含 {sections}、去向区隐藏、汇总范围排除 rollup；保存 payload 正确', async () => {
    renderToTargetStep()
    // 默认 focus：去向 radioCol 在场；切 rollup 后隐藏
    expect(screen.getByText('schedule.form.target.none')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.scope.rollup'))
    expect(screen.queryByText('schedule.form.target.none')).toBeNull()
    // 骨架预填默认模板（含 {sections}），保存可用
    const skeleton = screen.getByLabelText('schedule.form.rollupSkeletonLabel') as HTMLTextAreaElement
    expect(skeleton.value).toContain('{sections}')
    // 汇总范围候选：rollup 行被排除（防自聚合），仅 focus 候选
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
      targetType: null,
      targetId: null,
      autoImport: false,
    })
    expect((call![1] as { prompt: string }).prompt).toContain('{sections}')
  })

  it('骨架被清空（无 {sections}）→ 保存禁用', () => {
    renderToTargetStep()
    fireEvent.click(screen.getByText('schedule.scope.rollup'))
    const skeleton = screen.getByLabelText('schedule.form.rollupSkeletonLabel') as HTMLTextAreaElement
    fireEvent.change(skeleton, { target: { value: '占位符被我删了' } })
    expect((screen.getByText('schedule.form.save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('默认 focus 保存：payload scope=focus、digestScheduleIds=null，去向语义零漂移', async () => {
    renderToTargetStep()
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    const call = lastInvoke('schedules:create')
    expect(call![1]).toMatchObject({
      scope: 'focus',
      digestScheduleIds: null,
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
    // 编辑态跳过第①步，从周期步前进
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
})
