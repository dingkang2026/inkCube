/**
 * tests/RunReplay.test.tsx — 2.11 E3 T8: RunReplay 执行回放单测（§8.3 档案语义）。
 *
 * 覆盖：标题「{订阅名} · M/D」；消息流经 tasks:getMessages 拉取（只读复用
 * TaskMessageList，isGenerating=false）；无输入区（无 textarea——规则 6 只读，
 * 输入区属 TaskPage，不在本组件）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { RunReplay } from '../src/features/schedule/RunReplay'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

// TaskMessageList 打桩：RunReplay 单元只验证透传契约（只读态 + 消息数据）
const replayListState = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }))
vi.mock('../src/features/task/TaskMessageList', () => ({
  TaskMessageList: (props: Record<string, unknown>) => {
    replayListState.props = props
    const messages = (props.messages ?? []) as { content: string }[]
    return (
      <div data-testid="replay-messages" data-generating={String(props.isGenerating)}>
        {messages.map((m, i) => (
          <p key={i}>{m.content}</p>
        ))}
      </div>
    )
  },
}))

const MESSAGES = [
  { id: 'm1', role: 'user', content: '第一期订阅指令', created_at: '2026-10-19T09:00:00' },
  { id: 'm2', role: 'assistant', content: '已生成报告', created_at: '2026-10-19T09:01:00' },
]

const wireInvoke = () =>
  vi.fn((channel: string) => {
    if (channel === 'tasks:getMessages') return Promise.resolve({ messages: MESSAGES })
    if (channel === 'schedules:readReport') {
      return Promise.resolve({
        run: { id: 'run_1', status: 'done', started_at: '2026-10-19T09:00:00', error: null },
        schedule: { id: 'sch_1', name: '华润舆情' },
        content: '',
      })
    }
    return Promise.resolve({ ok: true })
  })

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
  replayListState.props = null
})

describe('RunReplay（2.11 §8.3 只读回放）', () => {
  it('标题含订阅名与只读标记，消息流经 tasks:getMessages 拉取', async () => {
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    const { container } = render(<RunReplay runId="run_1" taskId="t1" />)
    await flush()
    expect(screen.getByText(/华润舆情/)).toBeTruthy()   // 标题含订阅名
    expect(screen.getByText(/schedule\.replay\.title/)).toBeTruthy()  // 标题含只读标记
    expect(invoke).toHaveBeenCalledWith('tasks:getMessages', 't1')
    expect(screen.getByText('第一期订阅指令')).toBeTruthy()
    expect(replayListState.props?.isGenerating).toBe(false) // 档案语义：恒非生成态
    expect(container.querySelector('textarea')).toBeNull() // 只读：无输入区
  })
})
