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
vi.mock('../src/features/browser/browserPanelStore', () => ({
  browserPanelStore: {
    showCompact: showCompactSpy,
    navigate: vi.fn(),
    expand: vi.fn(),
    collapse: vi.fn(),
    hide: vi.fn(),
  },
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
