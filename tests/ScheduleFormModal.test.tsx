/**
 * tests/ScheduleFormModal.test.tsx — 订阅表单弹层单测（2.11 §12；2026-10-25 收敛
 * 后两类交互：类型由入口给定 createKind，不可中途切换）。
 *
 * 覆盖：
 * - 新建 rollup（createKind）：直达周期步（跳过模板步）、骨架不渲染（治理二次
 *   收敛：说明文案+追加框）、追加要求拼接、汇总范围候选（排除 rollup 自身）
 *   + 勾选 → 保存 payload 断言；
 * - 追加框降高样式类（extrasArea）；前端 {sections} 校验退役（后端 400 纵深防御）；
 * - 新建 intel 默认：payload scope/digestScheduleIds=null，语义零漂移；
 * - prefill 模板化（0.6 §3.2/§6.1，2026-10-25）：落模板页签预选企业动态跟踪、
 *   {企业名} 预填线索名，payload 分类静态映射 corporate；
 * - 编辑 rollup 回填：圈选回显 + update payload；骨架解析（骨架治理）：拼接形态
 *   剥离回填追加框（payload 拼接还原），不匹配存量无损原样保存；
 * - 跨开合候选重拉（review m-1）：组件关闭不卸载，重开候选重新加载。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { ScheduleFormModal } from '../src/features/schedule/ScheduleFormModal'
import zhCN from '../src/i18n/locales/zh-CN.json'
import enUS from '../src/i18n/locales/en-US.json'
import { uniquePlaceholderNames } from '../src/features/task/promptTemplate'

vi.mock('react-i18next', () => ({
  // 骨架/引导语需返回真实形态（新建 rollup 预填断言 + 编辑态拼接解析依赖）
  useTranslation: () => ({
    t: (k: string) =>
      k === 'schedule.form.rollupPromptTemplate' ? '汇总各订阅期次\n{sections}'
        : k === 'schedule.form.rollupExtrasPrefix' ? '此外，用户特别要求：'
        : k,
  }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => () => {},
}))

// 模板列表提供 builtin_competitor（企业动态跟踪）：prefill 模板化用例依赖
// selectedTpl 渲染占位符表单；其余用例不点模板卡，不受影响
vi.mock('../src/features/task/useTaskTemplates', () => ({
  useTaskTemplates: () => ({
    templates: [{
      id: 'builtin_competitor', builtin: true, category: 'mon',
      title: 'task.tpl.competitor.title', desc: 'task.tpl.competitor.desc',
      prompt: '跟踪「{企业名}」近一个月的动态', pinned: false,
    }],
  }),
}))

vi.mock('../src/features/settings/useSettings', () => ({
  useSettings: () => ({ settings: {} }),
}))

// 情报订阅行基座（scope=NULL 两值语义收敛后）；rollup 行以 { ...ROW, scope: 'rollup' } 派生
const ROW = {
  id: 'sch_1', name: '华润舆情', prompt: '扫描并输出报告', category: 'corporate',
  interval_type: 'daily', interval_value: '09:00',
  target_type: null, target_id: null, enabled: 1,
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
          { ...ROW, id: 'sch_2', name: '聚合简报甲', scope: 'rollup' },
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
    // 直达周期步：来源步 tab 不在场，标题为聚合简报
    expect(screen.queryByRole('tab', { name: 'schedule.form.freeInput' })).toBeNull()
    expect(screen.getByText('schedule.form.newRollupTitle')).toBeTruthy()
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 骨架不渲染（骨架治理二次收敛）：无骨架文本框，说明文案交代产出形态
    expect(screen.queryByLabelText('schedule.form.rollupSkeletonLabel')).toBeNull()
    expect(screen.getByText('schedule.form.rollupBaseHint')).toBeTruthy()
    // 汇总范围候选：rollup 行被排除（防自聚合），仅情报订阅候选
    await waitFor(() => expect(screen.getByLabelText('华润舆情')).toBeTruthy())
    expect(screen.queryByLabelText('聚合简报甲')).toBeNull()
    expect(lastInvoke('schedules:list')?.[1]).toMatchObject({ limit: 0 })
    fireEvent.click(screen.getByLabelText('华润舆情'))
    // 追加要求拼接（骨架治理）：prompt = 骨架 + 空行 + 引导语 + 追加
    fireEvent.change(screen.getByLabelText('schedule.form.rollupExtrasLabel'), {
      target: { value: '重点关注融资' },
    })
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
    })
    expect((call![1] as { prompt: string }).prompt)
      .toBe('汇总各订阅期次\n{sections}\n\n此外，用户特别要求：\n重点关注融资')
  })

  it('追加框降高样式类（骨架不渲染后唯一多行输入），独立可填，保存恒可用', () => {
    render(<ScheduleFormModal open createKind="rollup" onClose={() => {}} onSaved={() => {}} />)
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 骨架治理二次收敛：骨架 textarea 不渲染，追加框独立可编辑
    const extras = screen.getByLabelText('schedule.form.rollupExtrasLabel') as HTMLTextAreaElement
    expect(extras.className).toContain('extrasArea')
    fireEvent.change(extras, { target: { value: '结尾增加风险提示' } })
    expect(extras.value).toBe('结尾增加风险提示')
    // 前端 {sections} 校验退役（骨架用户不可达），保存恒可用
    expect((screen.getByText('schedule.form.save') as HTMLButtonElement).disabled).toBe(false)
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

  it('prefill 模板化（0.6 §3.2/§6.1）：落模板页签预选企业动态跟踪 + {企业名} 预填，分类映射 corporate', async () => {
    render(
      <ScheduleFormModal
        open
        prefill={{ name: '华润置地', tplId: 'builtin_competitor', phValues: { 企业名: '华润置地' } }}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    // 直落模板页签（免培训 §3.9-②：产出要点自解释，不以隐形模板串伪装自由输入）
    expect(screen.getByRole('tab', { name: 'schedule.form.fromTemplate' })
      .getAttribute('aria-selected')).toBe('true')
    // 预选模板的占位符表单已渲染且预填线索名
    expect((screen.getByLabelText('schedule.ph.企业名') as HTMLInputElement).value).toBe('华润置地')
    fireEvent.click(screen.getByText('schedule.form.next'))
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 订阅名预填线索名
    expect((screen.getByLabelText('schedule.form.nameLabel') as HTMLInputElement).value).toBe('华润置地')
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    const call = lastInvoke('schedules:create')
    expect(call![1]).toMatchObject({
      name: '华润置地',
      // mon→corporate 静态映射（INTEL_TEMPLATES 同步查找，不依赖模板列表异步加载）
      category: 'corporate',
      // prompt = 模板 applyValues 固化快照
      prompt: '跟踪「华润置地」近一个月的动态',
      scope: null,
    })
  })

  it('升级订阅动线前提（review 守护）：competitor prompt 双语均恰含单个占位符「企业名/company」（prefill 仅填线索名一值）', () => {
    expect(uniquePlaceholderNames(zhCN.task.tpl.competitor.prompt)).toEqual(['企业名'])
    expect(uniquePlaceholderNames(enUS.task.tpl.competitor.prompt)).toEqual(['company'])
  })

  it('编辑 rollup：骨架与圈选回填；保存走 update 且 payload 带现值', async () => {
    render(
      <ScheduleFormModal
        open
        editing={{
          ...ROW,
          name: '聚合简报', scope: 'rollup',
          prompt: '骨架\n{sections}',
          digest_schedule_ids: ['sch_x'],
        } as never}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    // 编辑态直达周期步，从周期步前进
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 骨架不渲染：存量与模板不匹配（手改）走无损形态——追加框留空
    expect((screen.getByLabelText('schedule.form.rollupExtrasLabel') as HTMLTextAreaElement).value).toBe('')
    await waitFor(() => expect(screen.getByLabelText('华润舆情')).toBeTruthy())
    expect((screen.getByLabelText('华润舆情') as HTMLInputElement).checked).toBe(true)
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    const call = lastInvoke('schedules:update')
    expect(call![1]).toMatchObject({
      id: 'sch_1', scope: 'rollup', digestScheduleIds: ['sch_x'],
    })
    // 不匹配存量原样保存（无损，零改写）
    expect((call![1] as { prompt: string }).prompt).toBe('骨架\n{sections}')
  })

  it('编辑 rollup 骨架解析（骨架治理 2026-10-25）：拼接形态剥离回填；不匹配存量无损转只读骨架', async () => {
    // 形态 A：骨架+引导语+追加 → 精确剥离回填（零猜测）
    const { unmount } = render(
      <ScheduleFormModal
        open
        editing={{
          ...ROW,
          name: '聚合简报', scope: 'rollup',
          prompt: '汇总各订阅期次\n{sections}\n\n此外，用户特别要求：\n重点关注融资',
        } as never}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 骨架不渲染：解析经追加框回填间接可观测
    expect((screen.getByLabelText('schedule.form.rollupExtrasLabel') as HTMLTextAreaElement).value)
      .toBe('重点关注融资')
    // 保存 payload 拼接还原原 prompt（数据流零漂移）
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    expect((lastInvoke('schedules:update')![1] as { prompt: string }).prompt)
      .toBe('汇总各订阅期次\n{sections}\n\n此外，用户特别要求：\n重点关注融资')
    unmount()
    // 形态 D：不匹配（手改过/跨语言）→ 存量全文无损转只读骨架，追加框留空
    render(
      <ScheduleFormModal
        open
        editing={{
          ...ROW,
          name: '聚合简报', scope: 'rollup',
          prompt: '完全自定义的历史骨架 {sections}',
        } as never}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    )
    fireEvent.click(screen.getByText('schedule.form.next'))
    expect((screen.getByLabelText('schedule.form.rollupExtrasLabel') as HTMLTextAreaElement).value)
      .toBe('')
    // 保存 payload 原样保留（存量全文无损）
    fireEvent.click(screen.getByText('schedule.form.save'))
    await flush()
    expect((lastInvoke('schedules:update')![1] as { prompt: string }).prompt)
      .toBe('完全自定义的历史骨架 {sections}')
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
