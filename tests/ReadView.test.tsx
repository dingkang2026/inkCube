/**
 * tests/ReadView.test.tsx — 2.11 E3 T8: ReadView 期次阅读页单测（§8.2）。
 *
 * 覆盖：挂载即 readReport + markRead（打开即已读，规则 10）；摘要卡（run.summary
 * 直读）+ Markdown 全文；失败态（error 文案 + 重跑 → schedules:runNow）；
 * 头部动作条（返回 → onBack 关抽屉 / 执行过程 → onReplay，2026-10-22 上移自底部）；
 * 底部动作条（订阅设置 → 编辑弹层）；已沉淀态
 * （target 绑定 → 「已沉淀至 {目标名}·档案」，目标名经列表通道解析）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { ReadView } from '../src/features/schedule/ReadView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
// Toast 捕获（R3-P2-4：重跑 deferred 分级断言用；options 留存供 action.onClick 触发）
const toastState = vi.hoisted(() => ({
  calls: [] as Array<[string, string]>,
  options: [] as Array<{ action?: { label: string; onClick: () => void } }>,
}))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => (msg: string, kind?: string, options?: { action?: { label: string; onClick: () => void } }) => {
    toastState.calls.push([msg, kind ?? ''])
    toastState.options.push(options ?? {})
  },
}))

// PromptDialog 打桩（存为线索已退役 2026-10-25，本文件无 usePrompt 消费；保留 mock 防
// 组件树内其它 PromptDialog 使用方意外发真实弹层）
const promptState = vi.hoisted(() => ({ fn: vi.fn(() => Promise.resolve(null)) }))
vi.mock('../src/components/PromptDialog', () => ({
  usePrompt: () => promptState.fn,
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
  toastState.options = []
  promptState.fn.mockReset()
  promptState.fn.mockImplementation(() => Promise.resolve(null))
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

  it('已绑定项目去向 → 已沉淀态显示目标名（列表通道解析），点击跳容器', async () => {
    const invoke = wireInvoke({
      schedule: { ...SCHEDULE, target_type: 'project', target_id: 'p1' },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    const events: CustomEvent[] = []
    const listener = (e: Event) => events.push(e as CustomEvent)
    window.addEventListener('app:navigate-project-tab', listener)
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByTestId('read-archived')).toBeTruthy()
    expect(screen.getByText('项目甲')).toBeTruthy()
    expect(invoke).toHaveBeenCalledWith('knowledge:listProjects', { limit: 0 })
    fireEvent.click(screen.getByTestId('read-archived'))
    window.removeEventListener('app:navigate-project-tab', listener)
    expect(events).toHaveLength(1)
    expect(events[0].detail).toEqual({ projectId: 'p1', container: 'project' })
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
    const events: CustomEvent[] = []
    const listener = (e: Event) => events.push(e as CustomEvent)
    window.addEventListener('app:navigate-project-tab', listener)
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByText('自定义库')).toBeTruthy()
    // 基础库去向分流：container='baselib'（App 导航分流原生支持）
    fireEvent.click(screen.getByTestId('read-archived'))
    window.removeEventListener('app:navigate-project-tab', listener)
    expect(events).toHaveLength(1)
    expect(events[0].detail).toEqual({ projectId: 'bl-1', container: 'baselib' })
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

  it('running run → 执行中态占位、不调 markRead、回放入口可达（2026-10-23）', async () => {
    const invoke = wireInvoke({
      run: { ...RUN_DONE, status: 'running', report_path: null, summary: null, finished_at: null },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByText('schedule.read.state.running')).toBeTruthy()
    expect(screen.getByText('schedule.read.runningHint')).toBeTruthy()
    // 仅 done 标已读：running 不调 markRead（否则吞掉终态后的未读点与新产出计数）
    expect(invoke.mock.calls.some((c) => c[0] === 'schedules:markRead')).toBe(false)
    // 无失败卡；头部「执行过程」（task_id 存在）可达 → 实时回放
    expect(screen.queryByText('schedule.read.rerun')).toBeNull()
    expect(screen.getByTestId('read-open-replay')).toBeTruthy()
  })

  it('聚合简报期次 → 标题行「聚合简报」徽标（2.11 §12.4）；情报订阅无', async () => {
    const invoke = wireInvoke({ schedule: { ...SCHEDULE, scope: 'rollup' } })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    const { unmount } = render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByTestId('read-rollup-tag').textContent).toBe('schedule.typeRollup')
    unmount()

    const invoke2 = wireInvoke({ schedule: { ...SCHEDULE, scope: null } })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: invoke2, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.queryByTestId('read-rollup-tag')).toBeNull()
  })

  it('头部动作条：「执行过程」回调 onReplay(taskId)（期次行点击统一进报告页后，回放唯一入口）', async () => {
    const onReplay = vi.fn()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={onReplay} />)
    await flush()
    fireEvent.click(screen.getByTestId('read-open-replay'))
    expect(onReplay).toHaveBeenCalledWith('t1')
  })

  it('头部动作条：「返回」回调 onBack（关闭期次抽屉）', async () => {
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

  it('Ctrl+F 打开文档内查找，命中计数与 Esc 关闭（2026-10-23）', async () => {
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
    fireEvent.keyDown(window, { ctrlKey: true, key: 'f' })
    const input = screen.getByPlaceholderText('find.placeholder')
    // 命中计数：摘要卡不计（查找范围限 .report 正文块）；fixture 正文含「要点」的
    // 块仅 li 要点A（要点B 只在 summary 数据不在 CONTENT）→ FindBar「当前/总数」= 1/1
    fireEvent.change(input, { target: { value: '要点' } })
    expect(screen.getByText('1/1')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
  })

  it('running 态 Ctrl+F 不打开查找（无正文可查，2026-10-23）', async () => {
    const invoke = wireInvoke({
      run: { ...RUN_DONE, status: 'running', report_path: null, summary: null, finished_at: null },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    fireEvent.keyDown(window, { ctrlKey: true, key: 'f' })
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
  })

  // ── 线索动线（0.6 §3.3/§6.1 V2-E2）：提取线索唯一入口（存为线索 2026-10-25 退役）──

  it('done 期次头部只有「提取线索」（存为线索已退役不渲染）；running 无（无报告不可提取）', async () => {
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
    const { unmount } = render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.getByTestId('read-extract-lead')).toBeTruthy()
    expect(screen.queryByTestId('read-save-lead')).toBeNull()
    unmount()

    const invokeRunning = wireInvoke({
      run: { ...RUN_DONE, status: 'running', report_path: null, summary: null, finished_at: null },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: invokeRunning, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.queryByTestId('read-extract-lead')).toBeNull()
  })

  it('提取线索：点击打开 LeadExtractModal（extract runId 契约）', async () => {
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.queryByTestId('lead-extract-modal')).toBeNull()
    fireEvent.click(screen.getByTestId('read-extract-lead'))
    await flush()
    expect(screen.getByTestId('lead-extract-modal')).toBeTruthy()
  })

  it('挂载默认不开提取模态（initialAction 直达动线已随情报流退役，2026-10-25）', async () => {
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke: wireInvoke(), on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    expect(screen.queryByTestId('lead-extract-modal')).toBeNull()
  })

  it('skipped run（rollup 空窗口）→ 留痕原因可读 + 重跑可用（CodeReview m-3）', async () => {
    const invoke = wireInvoke({
      run: { ...RUN_DONE, status: 'skipped', error: '窗口内无期次',
             report_path: null, summary: null, task_id: null },
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ReadView runId="run_1" onBack={onBack} onReplay={() => {}} />)
    await flush()
    // 只显示「已跳过」无解释不可接受（G4 留痕不静默）：error 原因必须可见
    expect(screen.getByText('窗口内无期次')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.read.rerun'))
    await flush()
    expect(invoke).toHaveBeenCalledWith('schedules:runNow', 'sch_1')
  })
})
