/**
 * tests/ReadView.test.tsx — 2.11 E3 T8: ReadView 期次阅读页单测（§8.2）。
 *
 * 覆盖：挂载即 readReport + markRead（打开即已读，规则 10）；摘要卡（run.summary
 * 直读）+ Markdown 全文；失败态（error 文案 + 重跑 → schedules:runNow）；
 * 头部动作条（返回 → onBack / 执行过程 → onReplay，2026-10-22 上移自底部）；
 * 底部动作条（订阅设置 → 编辑弹层）；已沉淀态
 * （target 绑定 → 「已沉淀至 {目标名}·档案」，目标名经列表通道解析）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { ReadView } from '../src/features/schedule/ReadView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
// Toast 捕获（R3-P2-4：重跑 deferred 分级断言用）
const toastState = vi.hoisted(() => ({ calls: [] as Array<[string, string]> }))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => (msg: string, kind?: string) => {
    toastState.calls.push([msg, kind ?? ''])
  },
}))

// ScheduleFormModal 打桩：「订阅设置」按钮 → open + editing（订阅行）props 断言
const formModalState = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }))
vi.mock('../src/features/schedule/ScheduleFormModal', () => ({
  ScheduleFormModal: (props: Record<string, unknown>) => {
    formModalState.props = props
    return props.open ? <div data-testid="schedule-form-modal" /> : null
  },
}))

const SCHEDULE = {
  id: 'sch_1', name: '华润舆情', prompt: '扫描并输出报告', category: 'corporate',
  interval_type: 'daily', interval_value: '09:00',
  target_type: null, target_id: null, auto_import: 1, enabled: 1,
  next_run_at: null, last_run_at: null, consecutive_failures: 0,
  archive_last_run_id: null, archive_file_id: null, is_running: false,
  latest_done: null, created_at: '', updated_at: '',
}
const RUN_DONE = {
  id: 'run_1', schedule_id: 'sch_1', task_id: 't1', status: 'done',
  report_path: 'x.md', summary: ['要点A', '要点B'], read_at: null,
  started_at: '2026-10-19T09:00:00', finished_at: '2026-10-19T09:02:00',
  schedule_name: '华润舆情', archive_last_run_id: null, error: null,
}
const CONTENT = '## 摘要\n- 要点A\n## 正文\n报告全文段落\n## 来源\n1. 源\n'

const wireInvoke = (over: { run?: Record<string, unknown>; schedule?: Record<string, unknown> } = {}) =>
  vi.fn((channel: string) => {
    if (channel === 'schedules:readReport') {
      return Promise.resolve({
        run: over.run ?? RUN_DONE,
        schedule: over.schedule ?? SCHEDULE,
        content: CONTENT,
      })
    }
    if (channel === 'knowledge:listProjects') {
      return Promise.resolve({
        projects: [{ id: 'p1', name: '项目甲', type: 'general', file_count: 0 }],
      })
    }
    if (channel === 'baseLib:list') return Promise.resolve({ base_libs: [] })
    return Promise.resolve({ ok: true })
  })

/** 排空 microtask（挂载 readReport / 目标名解析均为 async 链） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const onBack = vi.fn()

beforeEach(() => {
  formModalState.props = null
  toastState.calls = []
  onBack.mockClear()
})

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

describe('ReadView（2.11 §8.2）', () => {
  it('渲染摘要卡与 Markdown 全文，挂载即 readReport + markRead 一次', async () => {
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    // 摘要卡与 Markdown 正文同文本（mock content 与 summary 重叠）→ 计数断言
    expect(screen.getAllByText('要点A').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('要点B').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('报告全文段落')).toBeTruthy()
    const marks = invoke.mock.calls.filter((c) => c[0] === 'schedules:markRead')
    expect(marks).toHaveLength(1)
    expect(marks[0][1]).toBe('run_1')
  })

  it('摘要缺失（未按模板产出）→ 显示占位文案不阻塞阅读（§8.3）', async () => {
    const invoke = wireInvoke({ run: { ...RUN_DONE, summary: null } })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByText('schedule.read.noSummary')).toBeTruthy()
    expect(screen.getByText('报告全文段落')).toBeTruthy()
  })

  it('已绑定项目去向 → 已沉淀态显示目标名（列表通道解析）', async () => {
    const invoke = wireInvoke({
      schedule: { ...SCHEDULE, target_type: 'project', target_id: 'p1' },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByTestId('read-archived')).toBeTruthy()
    expect(screen.getByText('项目甲')).toBeTruthy()
    expect(invoke).toHaveBeenCalledWith('knowledge:listProjects', { limit: 0 })
  })

  it('已绑定基础库去向 → 目标名经 baseLib:list 解析', async () => {
    const invoke = vi.fn((channel: string) => {
      if (channel === 'schedules:readReport') {
        return Promise.resolve({
          run: RUN_DONE,
          schedule: { ...SCHEDULE, target_type: 'baselib', target_id: 'bl-1' },
          content: CONTENT,
        })
      }
      if (channel === 'baseLib:list') {
        return Promise.resolve({ base_libs: [{ id: 'bl-1', name: '自定义库' }] })
      }
      return Promise.resolve({ ok: true })
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByText('自定义库')).toBeTruthy()
  })

  it('失败 run → 显示失败原因与重跑按钮，点击重跑调 runNow', async () => {
    const invoke = wireInvoke({
      run: { ...RUN_DONE, status: 'failed', error: '模型超时', report_path: null, summary: null },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByText('模型超时')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.read.rerun'))
    await flush()
    expect(invoke).toHaveBeenCalledWith('schedules:runNow', 'sch_1')
  })

  it('重跑忙时让路（deferred）→ 分级提示，不假报已排队（R3-P2-4）', async () => {
    const invoke = vi.fn((channel: string) => {
      if (channel === 'schedules:readReport') {
        return Promise.resolve({
          run: { ...RUN_DONE, status: 'failed', error: '模型超时', report_path: null, summary: null },
          schedule: SCHEDULE,
          content: '',
        })
      }
      if (channel === 'schedules:runNow') return Promise.resolve({ ok: false, deferred: true })
      return Promise.resolve({ ok: true })
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    fireEvent.click(screen.getByText('schedule.read.rerun'))
    await flush()
    const msgs = toastState.calls.map(([m]) => m)
    expect(msgs).toContain('schedule.read.rerunDeferred')
    expect(msgs).not.toContain('schedule.read.rerunQueued')
  })

  it('失败 run 无原因 → 显示兜底文案', async () => {
    const invoke = wireInvoke({
      run: { ...RUN_DONE, status: 'failed', error: null, report_path: null, summary: null },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByText('schedule.read.failedNoReason')).toBeTruthy()
  })

  it('头部动作条：「执行过程」回调 onReplay(taskId)（期次行点击统一进报告页后，回放唯一入口）', async () => {
    const onReplay = vi.fn()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={onReplay} />)
    await flush()
    fireEvent.click(screen.getByTestId('read-open-replay'))
    expect(onReplay).toHaveBeenCalledWith('t1')
  })

  it('头部动作条：「返回」回调 onBack（宿主页分发在 App）', async () => {
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    fireEvent.click(screen.getByTestId('read-back-btn'))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('「订阅设置」打开编辑弹层（editing = 订阅行）', async () => {
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.queryByTestId('schedule-form-modal')).toBeNull()
    fireEvent.click(screen.getByText('schedule.read.settings'))
    await flush()
    expect(formModalState.props?.open).toBe(true)
    expect((formModalState.props?.editing as { id?: string }).id).toBe('sch_1')
  })
})
