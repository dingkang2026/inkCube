/** tests/TaskHistoryReplay.test.tsx — 历史回放渲染资产用例（1.2 §4.1 渲染共享出口）
 *  覆盖：buildHistoryItems 分组（气泡/迭代卡/observation 跳过/多轮 iter 重置/无前置
 *  reasoning 的防御分组）、enrichMessages 配对（action_id 优先 + 顺序回退）、
 *  TaskHistoryReplay 渲染（气泡可见、迭代卡默认折叠、点击展开工具行、错误状态点）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? key,
    i18n: { language: 'zh-CN' },
  }),
}))

vi.mock('../src/features/task/TaskMessageList.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/components/Markdown', () => ({
  Markdown: ({ content }: { content: string }) => <div data-testid="md">{content}</div>,
}))

vi.mock('../src/features/usage/UsageText', () => ({
  UsageText: () => null,
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { TaskHistoryReplay, buildHistoryItems, enrichMessages } from '../src/features/task/TaskHistoryReplay'
import type { TaskMessage } from '../src/types'

let seq = 0
function msg(partial: Partial<TaskMessage> & Pick<TaskMessage, 'role' | 'content'>): TaskMessage {
  seq += 1
  return {
    id: `m${seq}`,
    task_id: 't1',
    created_at: '2025-01-01T00:00:00',
    ...partial,
  }
}

afterEach(() => { cleanup(); seq = 0 })

describe('buildHistoryItems', () => {
  it('groups reasoning/thought/action into an iteration card and bubbles user/assistant', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', content: '帮我做个品牌' }),
      msg({ role: 'reasoning', content: '先分析素材' }),
      msg({ role: 'thought', content: '计划：读文件' }),
      msg({ role: 'action', content: '', metadata: { tool_name: 'file_ops' } }),
      msg({ role: 'assistant', content: 'brief 如下' }),
    ])
    expect(items).toHaveLength(3)
    expect(items[0]).toMatchObject({ type: 'bubble' })
    expect(items[1]).toMatchObject({ type: 'iter', isStreaming: false })
    const group = (items[1] as any).group
    expect(group.iteration).toBe(0)
    expect(group.reasoning).toBe('先分析素材')
    expect(group.thought).toBe('计划：读文件')
    expect(group.actions).toHaveLength(1)
    expect(items[2]).toMatchObject({ type: 'bubble' })
  })

  it('skips observations and resets iteration numbering after each user message', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', content: '第一轮' }),
      msg({ role: 'reasoning', content: 'r1' }),
      msg({ role: 'action', content: '', metadata: { tool_name: 'shell' } }),
      msg({ role: 'observation', content: '', metadata: { tool_output: '{"ok":1}' } }),
      msg({ role: 'user', content: '第二轮' }),
      msg({ role: 'reasoning', content: 'r2' }),
    ])
    // observation 不产生渲染项（输出已并入 action）
    expect(items).toHaveLength(4)
    expect((items[1] as any).group.iteration).toBe(0)
    // 第二轮 user 后 iter 编号重置
    expect((items[3] as any).group.iteration).toBe(0)
  })

  it('creates a defensive group for actions without preceding reasoning and flags error status', () => {
    const items = buildHistoryItems([
      msg({ role: 'action', content: '', metadata: { tool_name: 'shell', tool_output: '{"exit_code": 1}' } }),
    ])
    expect(items).toHaveLength(1)
    const group = (items[0] as any).group
    expect(group.iteration).toBe(0)
    expect(group.actions).toHaveLength(1)
    expect(group.status).toBe('error')
  })
})

describe('enrichMessages', () => {
  it('merges observation metadata into its action by action_id', () => {
    const enriched = enrichMessages([
      msg({ id: 'a1', role: 'action', content: '', metadata: { tool_name: 'shell' } }),
      msg({ id: 'o1', role: 'observation', content: '', metadata: { action_id: 'a1', tool_output: '{"exit_code":0}' } }),
    ])
    expect(enriched[0].metadata?.tool_output).toBe('{"exit_code":0}')
  })

  it('falls back to positional pairing when observations lack action_id', () => {
    const enriched = enrichMessages([
      msg({ id: 'a1', role: 'action', content: '' }),
      msg({ id: 'a2', role: 'action', content: '' }),
      msg({ id: 'o1', role: 'observation', content: '', metadata: { tool_output: 'out1' } }),
      msg({ id: 'o2', role: 'observation', content: '', metadata: { tool_output: 'out2' } }),
    ])
    expect(enriched[0].metadata?.tool_output).toBe('out1')
    expect(enriched[1].metadata?.tool_output).toBe('out2')
  })
})

describe('TaskHistoryReplay', () => {
  it('renders bubbles and collapsed iteration cards; expands on click', () => {
    const { container } = render(
      <TaskHistoryReplay
        messages={[
          msg({ role: 'user', content: '帮我做个品牌' }),
          msg({ role: 'reasoning', content: '思考中' }),
          msg({ role: 'action', content: '', metadata: { tool_name: 'shell', tool_input: { command: 'ls' } } }),
          msg({ role: 'assistant', content: 'brief 如下' }),
        ]}
      />,
    )
    // 气泡正文直接可见
    expect(container.textContent).toContain('帮我做个品牌')
    expect(container.textContent).toContain('brief 如下')
    // 迭代卡头部（Iter 1）可见，默认折叠：迭代卡体（推理块/工具行）不可见
    expect(container.textContent).toContain('Iter 1')
    expect(container.textContent).not.toContain('思考中')
    expect(container.textContent).not.toContain('ls')
    // 点击展开后工具行出现（工具摘要与 i18n 标签；推理块仍二级折叠，历史回放默认收起）
    fireEvent.click(container.querySelector('.iterHeader')!)
    expect(container.textContent).toContain('task.tools.shell')
    expect(container.textContent).toContain('ls')
    expect(container.textContent).not.toContain('思考中')
  })

  it('marks errored iterations with the error status dot', () => {
    const { container } = render(
      <TaskHistoryReplay
        messages={[
          msg({ role: 'action', content: '', metadata: { tool_name: 'shell', tool_output: '{"exit_code": 2}' } }),
        ]}
      />,
    )
    expect(container.querySelector('.statusError')).toBeTruthy()
    expect(container.querySelector('.statusOk')).toBeNull()
  })

  it('renders nothing for empty message list', () => {
    const { container } = render(<TaskHistoryReplay messages={[]} />)
    expect(container.querySelector('.replay')!.children).toHaveLength(0)
  })
})
