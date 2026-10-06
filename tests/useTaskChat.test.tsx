/** tests/useTaskChat.test.tsx — 任务事件流协议消费回归（1.2 §2/§4.4）
 *
 *  新协议：iteration（显式轮边界）→ reasoning_chunk（推理流式）→ answer_chunk
 *  （分析流式）→ thought（锁定快照，不推进轮号）→ action/observation（归属
 *  当前轮号）→ done（本地合成消息，不读 DB）。
 *  覆盖：思考与工具同轮、reasoning_reset 重试清空、done 合成保留 tool-only
 *  轮的 action、最终回答转 assistant 泡。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useTaskChat } from '../src/features/task/useTaskChat'

type StreamHandler = (data: unknown) => void
let streamHandler: StreamHandler | null = null
const invoke = vi.fn(() => Promise.resolve({ messages: [] }))

const wireWindow = () => {
  streamHandler = null
  ;(window as { electronAPI?: unknown }).electronAPI = {
    invoke,
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
})
