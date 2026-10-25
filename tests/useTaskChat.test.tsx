/** tests/useTaskChat.test.tsx — 任务事件流协议消费回归 + 流式累积回归（1.2 §2/§4.4）
 *
 *  流式协议组（迭代事件+三区卡大改时建立）：
 *  iteration（显式轮边界）→ reasoning_chunk（推理流式）→ answer_chunk（分析流式）
 *  → thought（锁定快照，不推进轮号）→ action/observation（归属当前轮号）
 *  → done（本地合成消息，不读 DB）。
 *
 *  流式累积组（实时渲染丢字 bug 回归）：
 *  回归背景：state→ref 回写 effect 在高频流式下用过期 state 回滚 ref，
 *  导致长回答中间增量永久丢失（done 合成的 assistant 比后端短百余字，
 *  截图表现为实时渲染内容残缺、历史渲染正常）。
 *  锁定行为：① 多 chunk 最终回答无损累积 ② `answer_reset` 清空旧内容
 *  （LLM 重试/失败兜底，与 `reasoning_reset` 对称）③ 后台路径同样生效。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
// V2 §8.2：browser-fetch 实时 action 弹 Compact 面板——spy 捕获，避免污染全局 store
const showCompactSpy = vi.hoisted(() => vi.fn())
// V2.1-R10：type 输入可见性——自动放大（action）+ 输入完成回小卡（observation）
const expandForInputSpy = vi.hoisted(() => vi.fn())
const collapseIfAutoSpy = vi.hoisted(() => vi.fn())
// 动作失败反馈（静默 catch 排查 2026-10-24）：pause/shell/skill/resume 失败 toast
const toastSpy = vi.hoisted(() => vi.fn())
vi.mock('../src/features/browser/browserPanelStore', () => ({
  browserPanelStore: {
    showCompact: showCompactSpy,
    expandForInput: expandForInputSpy,
    collapseIfAuto: collapseIfAutoSpy,
    navigate: vi.fn(),
    expand: vi.fn(),
    collapse: vi.fn(),
    hide: vi.fn(),
  },
}))
// 无 i18n 实例时 t 返回 key 本身——错误文案断言用 key 片段（见 T-2 用例）
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastSpy,
}))
import { useTaskChat } from '../src/features/task/useTaskChat'

type StreamHandler = (data: unknown) => void
let streamHandler: StreamHandler | null = null
const invoke = vi.fn(() => Promise.resolve({ messages: [] }))

const wireWindow = () => {
  streamHandler = null
  ;(window as { electronAPI?: unknown }).electronAPI = {
    invoke,
    send: () => {},
    on: (_channel: string, handler: StreamHandler) => {
      streamHandler = handler
      return () => { streamHandler = null }
    },
  }
}

const emit = async (type: string, data: unknown, taskId = 't1') => {
  await act(async () => {
    streamHandler?.({ taskId, type, data: JSON.stringify(data) })
  })
}

const settle = async () => {
  await act(async () => {
    await Promise.resolve()
  })
}

beforeEach(() => {
  invoke.mockClear()
  invoke.mockResolvedValue({ messages: [] })
  showCompactSpy.mockClear()
  expandForInputSpy.mockClear()
  collapseIfAutoSpy.mockClear()
  wireWindow()
})

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

describe('useTaskChat stream protocol', () => {
  it('full two-round flow: same-round thinking+tools, done synthesizes messages without DB', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle() // loadMessages microtask

    // 轮 0：推理流式 → 分析流式 → thought 锁定
    await emit('iteration', { iteration: 0 })
    await emit('reasoning_chunk', { delta: '思' })
    await emit('reasoning_chunk', { delta: '考' })
    await emit('answer_chunk', { content: '分析' })
    expect(result.current.streamReasoning).toBe('思考')
    expect(result.current.streamContent).toBe('分析')
    expect(result.current.streamIteration).toBe(0)
    await emit('thought', { content: '分析' })
    // thought 锁定后清空当前流，轮号不变（推进只由 iteration 事件负责）
    expect(result.current.streamReasoning).toBe('')
    expect(result.current.streamContent).toBe('')
    expect(result.current.streamIteration).toBe(0)

    // 轮 0 的工具（锁定后执行，仍归属轮 0 —— 思考与工具同卡）
    await emit('action', { tool: 'shell', input: { command: 'ls' } })
    expect(result.current.streamSteps[0].iteration).toBe(0)
    await emit('observation', { content: '{"exit_code":0}' })

    // 轮 1：显式边界推进
    await emit('iteration', { iteration: 1 })
    expect(result.current.streamIteration).toBe(1)

    // 最终回答流式 → done 转泡
    await emit('answer_chunk', { content: '最终回答' })
    await emit('done', {})

    expect(result.current.isGenerating).toBe(false)
    const roles = result.current.messages.map((m) => m.role)
    expect(roles).toEqual(['reasoning', 'thought', 'action', 'observation', 'assistant'])
    expect(result.current.messages[0].content).toBe('思考')
    expect(result.current.messages[1].content).toBe('分析')
    expect(result.current.messages[4].content).toBe('最终回答')
  })

  it('reasoning_reset clears partial reasoning for LLM retry', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()
    await emit('iteration', { iteration: 0 })
    await emit('reasoning_chunk', { delta: '旧流前半' })
    await emit('reasoning_reset', {})
    await emit('reasoning_chunk', { delta: '新流' })
    expect(result.current.streamReasoning).toBe('新流')
  })

  it('done keeps actions of a tool-only iteration (no map entry)', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()
    await emit('iteration', { iteration: 0 })
    await emit('action', { tool: 'file_ops', input: {} })
    await emit('observation', { content: '{"ok":true}' })
    await emit('done', {})
    const roles = result.current.messages.map((m) => m.role)
    expect(roles).toEqual(['action', 'observation'])
  })

  it('browser-fetch action pops the compact panel; other tools do not (V2 §8.2)', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    await emit('action', { tool: 'shell', input: {} })
    expect(showCompactSpy).not.toHaveBeenCalled()

    await emit('action', { tool: 'browser-fetch', input: { action: 'open', url: 'https://example.com' } })
    expect(showCompactSpy).toHaveBeenCalledTimes(1)
    // action 本身仍正常入流式步骤
    expect(result.current.streamSteps[result.current.streamSteps.length - 1].tool).toBe('browser-fetch')

    // skill_runner 备选路径（slash 调用）同样感知；无关 skill 不误弹
    await emit('action', { tool: 'skill_runner', input: { skill: 'browser-fetch' } })
    expect(showCompactSpy).toHaveBeenCalledTimes(2)
    await emit('action', { tool: 'skill_runner', input: { skill: 'wiki-compiler' } })
    expect(showCompactSpy).toHaveBeenCalledTimes(2)
  })

  it('type action auto-expands for input visibility; observation collapses back (V2.1-R10)', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    // 非 type 的 browser-fetch 只弹小卡，不放大
    await emit('action', { tool: 'browser-fetch', input: { command: 'open https://example.com' } })
    expect(expandForInputSpy).not.toHaveBeenCalled()

    // type 命令 → 自动放大（输入框必须看得见）
    await emit('action', { tool: 'browser-fetch', input: { command: 'type --selector "div > div > input" --text "上海旦迪" --submit' } })
    expect(expandForInputSpy).toHaveBeenCalledTimes(1)

    // observation 到达（输入完成）→ 回小卡
    await emit('observation', { content: '{"action":"type"}' })
    expect(collapseIfAutoSpy).toHaveBeenCalledTimes(1)

    // skill_runner 备选路径同样感知 type
    await emit('action', { tool: 'skill_runner', input: { skill: 'browser-fetch', input: { command: 'type --selector "input" --text "y"' } } })
    expect(expandForInputSpy).toHaveBeenCalledTimes(2)

    // action 本身仍正常入流式步骤（open / type / skill_runner-type 共 3 条）
    const actions = result.current.streamSteps.filter((s) => s.type === 'action')
    expect(actions).toHaveLength(3)
    expect(actions[2].tool).toBe('skill_runner')
  })
})

describe('useTaskChat — stream accumulation regression (drop-chars bug)', () => {
  it('multi-chunk final answer accumulates losslessly into the done message', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    // 第一轮：中间分析（thought 锁定）；第二轮：长最终回答（300 个 delta 模拟高频流式）
    await emit('iteration', { iteration: 0 })
    await emit('answer_chunk', { content: '中间分析' })
    await emit('thought', { content: '中间分析' })
    await emit('iteration', { iteration: 1 })
    const deltas = Array.from({ length: 300 }, (_, i) => `片段${i}，`)
    for (const d of deltas) await emit('answer_chunk', { content: d })
    await emit('done', {})

    const assistant = result.current.messages.find((m) => m.role === 'assistant')
    expect(assistant).toBeTruthy()
    const expected = deltas.join('')
    expect(assistant!.content).toBe(expected)
    expect(assistant!.content.length).toBe(expected.length)
  })

  it('answer_reset drops stale partial content (symmetry with reasoning_reset)', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    await emit('answer_chunk', { content: '部分旧内容' })
    await emit('answer_reset', {})
    await emit('answer_chunk', { content: '重试后的完整回答' })
    await emit('done', {})

    const assistant = result.current.messages.find((m) => m.role === 'assistant')
    expect(assistant!.content).toBe('重试后的完整回答')
  })

  it('background task answer_reset clears the bg content buffer', async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskChat(id),
      { initialProps: { id: 't2' as string | null } },
    )
    await settle()

    // t2 在前台建立流式缓存 → 切到 t1 使 t2 转后台 → 后台 reset + 继续累积
    await emit('answer_chunk', { content: 'T2部分' }, 't2')
    await act(async () => { rerender({ id: 't1' }) })
    await emit('answer_reset', {}, 't2')
    await emit('answer_chunk', { content: 'T2重试新内容' }, 't2')
    // 切回 t2，bg 缓存恢复
    await act(async () => { rerender({ id: 't2' }) })
    expect(result.current.streamContent).toBe('T2重试新内容')
  })
})

describe('useTaskChat — hint-type thought & resume banner (P2 exhaustion recovery)', () => {
  it('hint-type thought (no prior stream content) lands in the iteration card; later real thought appends', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    // 恢复场景：提示型 thought 先于首个 iteration 事件到达（此前被静默丢弃，
    //「流内提示」从未可见——CodeReview M1）
    const hint = '▶️ 从第 24 轮恢复执行（新一轮 25 轮预算）...'
    await emit('thought', { content: hint })
    expect(result.current.streamIterationThoughts.get(0)).toBe(hint)

    // 随后轮 0 正常分析到达：流式内容优先，与提示同卡（换行追加）
    await emit('iteration', { iteration: 0 })
    await emit('answer_chunk', { content: '分析内容' })
    await emit('thought', { content: '分析内容' })
    expect(result.current.streamIterationThoughts.get(0)).toBe(`${hint}\n分析内容`)
  })

  it('background path captures hint-type thought as well', async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskChat(id),
      { initialProps: { id: 't2' as string | null } },
    )
    await settle()

    // t2 前台仅建立轮边界（content 保持空）→ 切 t1 转后台 → 后台收到提示型 thought
    await emit('iteration', { iteration: 0 }, 't2')
    await act(async () => { rerender({ id: 't1' }) })
    await emit('thought', { content: '⏸️ 已暂停，可点击恢复继续' }, 't2')
    // 切回 t2，bg 缓存里的提示可见
    await act(async () => { rerender({ id: 't2' }) })
    expect(result.current.streamIterationThoughts.get(0)).toBe('⏸️ 已暂停，可点击恢复继续')
  })

  it('done refreshes canResume so the banner appears without switching tasks', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()
    expect(result.current.canResume).toBe(false)

    // 任务运行中断点产生（如迭代耗尽）：hasState 由 false 变 true
    invoke.mockImplementation((channel: string) =>
      channel === 'tasks:hasState'
        ? Promise.resolve({ has_state: true })
        : Promise.resolve({ messages: [] }),
    )

    await emit('iteration', { iteration: 0 })
    await emit('answer_chunk', { content: '已连续执行 25 轮迭代仍未完成任务...' })
    await emit('done', {})
    await settle() // refreshCanResume 异步查询

    // 此前 canResume 只在 loadMessages（切任务）时查询——前台看着任务耗尽
    // 时横幅不出现，文案指路的「恢复」按钮缺失（CodeReview M2）
    expect(result.current.canResume).toBe(true)
  })
})

describe('useTaskChat — 发送即建任务 override（0.5 §4.4）', () => {
  it('overrideTaskId 指定发送目标：tasks:chat payload 与 user 消息 task_id 均用 override', async () => {
    const { result } = renderHook(() => useTaskChat(null))
    await settle()
    await act(async () => { await result.current.handleSendMessage('收集企业信息', 't-new') })
    expect(invoke).toHaveBeenCalledWith('tasks:chat', { taskId: 't-new', message: '收集企业信息' })
    expect(result.current.messages[0].task_id).toBe('t-new')
  })

  it('override 同步归属 ref：新 id 事件走前台流、其它 id 不串，done 合成消息落新 id', async () => {
    // 回归背景：创建任务后立即发送时 activeTaskId prop/ref 尚未经渲染回流，
    // 若不同步 ref，新任务的流事件会误走后台路径被丢弃，done 合成消息 task_id 为空
    const { result } = renderHook(() => useTaskChat(null))
    await settle()
    await act(async () => { await result.current.handleSendMessage('第一条', 't-new') })
    await emit('answer_chunk', { content: '新任务流' }, 't-new')
    expect(result.current.streamContent).toBe('新任务流')
    await emit('answer_chunk', { content: '别任务' }, 't-other')
    expect(result.current.streamContent).toBe('新任务流')
    await emit('done', {}, 't-new')
    const assistant = result.current.messages.find((m) => m.role === 'assistant')
    expect(assistant?.task_id).toBe('t-new')
  })

  it('零任务且无 override：静默不发送（原门控回归）', async () => {
    const { result } = renderHook(() => useTaskChat(null))
    await settle()
    await act(async () => { await result.current.handleSendMessage('x') })
    expect(invoke).not.toHaveBeenCalledWith('tasks:chat', expect.anything())
  })

  it('override 发送后的 activeTaskId prop 回流不重置生成态、不覆盖乐观消息（切换竞态回归）', async () => {
    // 回归背景：handleCreateTask 的 setActiveTaskId 与发送的 setIsGenerating(true)
    // 同批提交，切换 effect（null → t-new）以 else 分支重置 isGenerating（流式卡/
    // 停止钮消失、sendingRef 提前释放），并经 loadMessages 用后端空列表覆盖乐观 user 消息
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskChat(id),
      { initialProps: { id: null as string | null } },
    )
    await settle()
    await act(async () => { await result.current.handleSendMessage('第一条', 't-new') })
    expect(result.current.isGenerating).toBe(true)

    // beforeEach 的 invoke mock 对 tasks:getMessages 恒返回空列表 = 模拟后端尚未入库
    await act(async () => { rerender({ id: 't-new' }) })
    expect(result.current.isGenerating).toBe(true)
    expect(result.current.messages.some((m) => m.role === 'user' && m.task_id === 't-new')).toBe(true)

    // 后续流事件继续正常驱动（ref 归属未被打断）
    await emit('answer_chunk', { content: '流式内容' }, 't-new')
    expect(result.current.streamContent).toBe('流式内容')
  })

  it('suppress 一次性：override 回流跳过后，切走再切回仍走正常 bg 保存/恢复', async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskChat(id),
      { initialProps: { id: null as string | null } },
    )
    await settle()
    await act(async () => { await result.current.handleSendMessage('第一条', 't-new') })
    await act(async () => { rerender({ id: 't-new' }) })   // 冗余回流被抑制（标记消费）
    await emit('answer_chunk', { content: '已生成一半' }, 't-new')

    // 切走（t-new → null）：bg 保存生成态；null 分支清空 UI 态
    await act(async () => { rerender({ id: null }) })
    expect(result.current.isGenerating).toBe(false)

    // 切回 t-new：bg 恢复生成态与流内容（既有机制不受 suppress 影响）
    await act(async () => { rerender({ id: 't-new' }) })
    expect(result.current.isGenerating).toBe(true)
    expect(result.current.streamContent).toBe('已生成一半')
  })
})

describe('useTaskChat — resume 早退与消息加载竞态回归（review 2026-10-20 T-2/T-3）', () => {
  it('resume 返回 200+error body：isGenerating 复位并呈现错误，不卡死输入框（T-2）', async () => {
    // tasks:chat 桥把 200 响应体当 SSE 流解析，dict 错误不抛错——
    // 旧实现 await 正常返回且无 done 事件，isGenerating 恒 true
    invoke.mockImplementation((channel: string) =>
      channel === 'tasks:chat'
        ? Promise.resolve({ error: '没有可恢复的任务状态', code: 'NO_RESUMABLE_STATE' })
        : Promise.resolve({ messages: [], has_state: false }),
    )
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    await act(async () => { await result.current.handleResumeTask() })

    expect(result.current.isGenerating).toBe(false)
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.role).toBe('assistant')
    // NO_RESUMABLE_STATE 命中 i18n 映射：t 恒等返回 key → 断言映射 key 片段
    expect(last.content).toContain('noResumableState')
  })

  it('send 返回 200+error body：同样复位生成态（同族路径一次性防护）', async () => {
    invoke.mockImplementation((channel: string) =>
      channel === 'tasks:chat'
        ? Promise.resolve({ error: '配额耗尽', code: 'QUOTA' })
        : Promise.resolve({ messages: [], has_state: false }),
    )
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    await act(async () => { await result.current.handleSendMessage('你好') })

    expect(result.current.isGenerating).toBe(false)
    const last = result.current.messages[result.current.messages.length - 1]
    expect(last.role).toBe('assistant')
    expect(last.content).toContain('配额耗尽')
  })

  it('快速切任务后，旧任务的慢响应不覆盖新任务消息列表（T-3）', async () => {
    let resolveT1: (v: unknown) => void = () => {}
    const t2Msg = { id: 'm2', task_id: 't2', role: 'user', content: 'T2 消息', created_at: '' }
    invoke.mockImplementation((channel: string, taskId?: string) => {
      if (channel === 'tasks:getMessages' && taskId === 't1') {
        return new Promise((resolve) => { resolveT1 = resolve })
      }
      if (channel === 'tasks:getMessages' && taskId === 't2') {
        return Promise.resolve({ messages: [t2Msg] })
      }
      return Promise.resolve({ messages: [], has_state: false })
    })
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskChat(id),
      { initialProps: { id: 't1' as string | null } },
    )
    await settle() // t1 的 getMessages 仍挂起

    await act(async () => { rerender({ id: 't2' }) })
    await settle()
    expect(result.current.messages.some((m) => m.task_id === 't2')).toBe(true)

    // t1 的旧响应此时才到达：必须被归属守卫丢弃
    const t1Msg = { id: 'm1', task_id: 't1', role: 'user', content: 'T1 消息', created_at: '' }
    await act(async () => {
      resolveT1({ messages: [t1Msg] })
      await Promise.resolve()
    })
    expect(result.current.messages.some((m) => m.task_id === 't1')).toBe(false)
    expect(result.current.messages.some((m) => m.task_id === 't2')).toBe(true)
  })

  it('canResume 查询乱序：切任务后旧任务的 hasState 响应不写入（同一竞态窗口）', async () => {
    let resolveT1State: (v: unknown) => void = () => {}
    invoke.mockImplementation((channel: string, taskId?: string) => {
      if (channel === 'tasks:hasState' && taskId === 't1') {
        return new Promise((resolve) => { resolveT1State = resolve })
      }
      if (channel === 'tasks:hasState' && taskId === 't2') {
        return Promise.resolve({ has_state: false })
      }
      return Promise.resolve({ messages: [] })
    })
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskChat(id),
      { initialProps: { id: 't1' as string | null } },
    )
    await settle()

    await act(async () => { rerender({ id: 't2' }) })
    await settle()
    expect(result.current.canResume).toBe(false)

    // t1 的 hasState=true 晚到：不得把 t2 的恢复横幅点亮
    await act(async () => {
      resolveT1State({ has_state: true })
      await Promise.resolve()
    })
    expect(result.current.canResume).toBe(false)
  })
})

describe('useTaskChat — 确认弹窗倒计时（timeout 载荷同步，1.1 §7.1/§7.2）', () => {
  it('confirm_shell/skill 携带 timeout：expiresAt = now + timeout*1000；缺失时为 0（防御旧事件）', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    const t0 = Date.now()
    await emit('confirm_shell', { command: 'rm -rf /', task_id: 't1', timeout: 120 })
    const shell = result.current.pendingShellConfirm
    expect(shell?.command).toBe('rm -rf /')
    expect(shell!.expiresAt).toBeGreaterThanOrEqual(t0 + 120_000)
    expect(shell!.expiresAt).toBeLessThanOrEqual(Date.now() + 120_000)

    await emit('confirm_skill', { skill_name: 'web-scraper', task_id: 't1', timeout: 180 })
    const skill = result.current.pendingSkillConfirm
    expect(skill?.skillName).toBe('web-scraper')
    expect(skill!.expiresAt).toBeGreaterThanOrEqual(t0 + 180_000)
    expect(skill!.expiresAt).toBeLessThanOrEqual(Date.now() + 180_000)

    // timeout 缺失（异常载荷）：0 = 卡片无倒计时，与历史行为一致
    await emit('confirm_shell', { command: 'ls', task_id: 't1' })
    expect(result.current.pendingShellConfirm?.expiresAt).toBe(0)
  })

  it('后台任务确认存入 bgStates 携带 expiresAt，切回保持同一绝对时间戳（剩余不重置）', async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useTaskChat(id),
      { initialProps: { id: 't2' as string | null } },
    )
    await settle()

    // t2 前台建立缓存 → 切 t1 转后台 → 后台收到 skill 确认请求
    await emit('iteration', { iteration: 0 }, 't2')
    await act(async () => { rerender({ id: 't1' }) })
    await emit('confirm_skill', { skill_name: 'web-scraper', task_id: 't2', timeout: 180 }, 't2')

    // 切回 t2：bg 恢复的 expiresAt 必须是同一绝对时间戳（倒计时跨切换继续走）
    await act(async () => { rerender({ id: 't2' }) })
    expect(result.current.pendingSkillConfirm?.skillName).toBe('web-scraper')
    const remainSec = Math.ceil(((result.current.pendingSkillConfirm?.expiresAt ?? 0) - Date.now()) / 1000)
    expect(remainSec).toBeGreaterThan(0)
    expect(remainSec).toBeLessThanOrEqual(180)
  })

  it('dismissPendingConfirm 仅本地清理，不发拒绝 POST（超时语义归后端留痕）', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()

    await emit('confirm_shell', { command: 'rm -rf /', task_id: 't1', timeout: 120 })
    expect(result.current.pendingShellConfirm).not.toBeNull()
    await act(async () => { result.current.dismissPendingConfirm('shell') })
    expect(result.current.pendingShellConfirm).toBeNull()

    await emit('confirm_skill', { skill_name: 'x', task_id: 't1', timeout: 180 })
    await act(async () => { result.current.dismissPendingConfirm('skill') })
    expect(result.current.pendingSkillConfirm).toBeNull()

    // 到期清理路径不得发 confirm 拒绝（后端 timed_out 分支才发 confirm_expired）
    expect(invoke).not.toHaveBeenCalledWith('tasks:confirmShell', expect.anything())
    expect(invoke).not.toHaveBeenCalledWith('tasks:confirmSkill', expect.anything())
  })
})

describe('useTaskChat 动作失败反馈（静默 catch 排查 2026-10-24）', () => {
  beforeEach(() => { toastSpy.mockClear() })

  it('pause 失败 → task.pauseFailed toast（用户不得误以为已暂停）', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()
    invoke.mockImplementation((channel: string) =>
      channel === 'tasks:pause' ? Promise.reject(new Error('boom')) : Promise.resolve({}))
    await act(async () => { await result.current.handlePauseTask() })
    expect(toastSpy).toHaveBeenCalledWith('task.pauseFailed', 'error')
  })

  it('shell 确认失败 → task.confirmFailed toast（弹窗已清，后端等到超时）', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()
    invoke.mockImplementation((channel: string) =>
      channel === 'tasks:confirmShell' ? Promise.reject(new Error('boom')) : Promise.resolve({}))
    await act(async () => { await result.current.handleShellConfirm(true) })
    expect(toastSpy).toHaveBeenCalledWith('task.confirmFailed', 'error')
  })

  it('skill 确认失败 → task.confirmFailed toast', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()
    invoke.mockImplementation((channel: string) =>
      channel === 'tasks:confirmSkill' ? Promise.reject(new Error('boom')) : Promise.resolve({}))
    await act(async () => { await result.current.handleSkillConfirm(true) })
    expect(toastSpy).toHaveBeenCalledWith('task.confirmFailed', 'error')
  })

  it('resume 失败 → task.resumeFailed toast 且 isGenerating 复位（canResume 已提前置 false，失败必须可感知）', async () => {
    const { result } = renderHook(() => useTaskChat('t1'))
    await settle()
    invoke.mockImplementation((channel: string) =>
      channel === 'tasks:chat' ? Promise.reject(new Error('boom')) : Promise.resolve({ messages: [] }))
    await act(async () => { await result.current.handleResumeTask() })
    expect(toastSpy).toHaveBeenCalledWith('task.resumeFailed', 'error')
    expect(result.current.isGenerating).toBe(false)
  })
})
