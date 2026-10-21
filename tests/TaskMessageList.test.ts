/** tests/TaskMessageList.test.ts — 迭代分组回归测试（1.2 §4.1/§4.2 三区卡模型）
 *
 *  目标模型：iter 为阶段分割，卡内固定 推理→工具→分析 三区；分组数据驱动
 *  （有内容才建卡，当前轮与历史轮永不重叠）；历史编号每轮对话重置。
 *  回归背景：旧版以 maxStepIter 推断历史边界导致首轮渲染两遍；旧版靠
 *  thought 推进计数导致思考与工具分属两卡（错位一代）。
 */
import { describe, it, expect } from 'vitest'
import { buildStreamGroups } from '../src/features/task/TaskMessageList'
// buildHistoryItems 随历史回放渲染资产迁至 TaskHistoryReplay(共享出口)
import { buildHistoryItems } from '../src/features/task/TaskHistoryReplay'
import type { StreamStep, TaskMessage } from '../src/types'

const makeStep = (iteration: number, output?: string): StreamStep => ({
  type: 'action',
  tool: 'shell',
  input: { command: 'echo hi' },
  iteration,
  ...(output ? { output } : {}),
})

describe('buildStreamGroups — data-driven grouping', () => {
  it('thinking and its tool stay in the SAME card (iteration numbering fixed)', () => {
    // 轮 0：推理+分析已锁定（thought），该轮工具标记 iteration=0；轮 1 已开始
    const groups = buildStreamGroups(
      [makeStep(0, '{"exit_code":0}')],
      '', '', 1,
      new Map([[0, 'R0']]),
      new Map([[0, 'C0']]),
    )
    expect(groups).toHaveLength(1)
    expect(groups[0].iteration).toBe(0)
    expect(groups[0].reasoning).toBe('R0')
    expect(groups[0].thought).toBe('C0')
    expect(groups[0].actions).toHaveLength(1) // 同卡：思考 + 它发起的工具
    expect(groups[0].liveReasoning).toBeFalsy()
  })

  it('current iteration renders live streams (reasoning + analysis) with cursor flags', () => {
    const groups = buildStreamGroups(
      [makeStep(0, '{"exit_code":0}')],
      'R0 流式中', 'C0 流式中', 0,
      new Map(), new Map(),
    )
    expect(groups).toHaveLength(1)
    expect(groups[0].status).toBe('running')
    expect(groups[0].reasoning).toBe('R0 流式中')
    expect(groups[0].thought).toBe('C0 流式中')
    expect(groups[0].liveReasoning).toBe(true)
    expect(groups[0].liveThought).toBe(true)
    expect(groups[0].actions).toHaveLength(1)
  })

  it('text-only stream still shows a card (analysis section live)', () => {
    // slash 直发路径：无 iteration 事件、无 action，仅 answer_chunk 流式
    const groups = buildStreamGroups([], '', '最终回答流式', 0, new Map(), new Map())
    expect(groups).toHaveLength(1)
    expect(groups[0].liveThought).toBe(true)
    expect(groups[0].actions).toHaveLength(0)
  })

  it('first round without content (tool-only): single card, no duplication', () => {
    // 旧版 maxStepIter bug 场景：无 thought 事件、计数不推进 → 曾渲染两张 Iter 1
    const groups = buildStreamGroups([makeStep(0)], '', '', 0, new Map(), new Map())
    expect(groups).toHaveLength(1)
    expect(groups[0].actions).toHaveLength(1)
  })

  it('resume with sparse iteration numbers emits no empty cards', () => {
    const groups = buildStreamGroups(
      [makeStep(5, '{"exit_code":0}')], '', '', 5,
      new Map([[5, 'R5']]),
      new Map(),
    )
    expect(groups).toHaveLength(1)
    expect(groups[0].iteration).toBe(5)
  })

  it('nothing streamed: no groups', () => {
    expect(buildStreamGroups([], '', '', 0, new Map(), new Map())).toHaveLength(0)
  })
})

describe('buildHistoryItems — history grouping matches live rendering', () => {
  const msg = (
    role: TaskMessage['role'],
    content = '',
    metadata?: TaskMessage['metadata'],
  ): TaskMessage => ({
    id: `${role}-${Math.random().toString(36).slice(2)}`,
    task_id: 't',
    role,
    content,
    metadata,
    created_at: '',
  })

  it('iteration numbering restarts after each user message', () => {
    const items = buildHistoryItems([
      msg('user', '第一问'),
      msg('reasoning', 'R1'), msg('thought', 'C1'), msg('action'), msg('observation'),
      msg('reasoning', 'R2'), // 同轮第二个迭代
      msg('assistant', '答一'),
      msg('user', '第二问'),
      msg('reasoning', 'R3'),
    ])
    const iters = items.filter((i) => i.type === 'iter') as { type: 'iter'; group: { iteration: number } }[]
    // 第一轮：Iter 1(R1) + Iter 2(R2)；第二轮：Iter 1(R3) — 与即时渲染一致
    expect(iters.map((i) => i.group.iteration)).toEqual([0, 1, 0])
  })

  it('groups reasoning + analysis + tools into one card, observation skipped', () => {
    const items = buildHistoryItems([
      msg('user', '问'),
      msg('reasoning', 'R'),
      msg('thought', 'C'),
      msg('action', '', { tool_name: 'shell', tool_input: {} }),
      msg('observation', '', { tool_output: '{"exit_code":0}' }),
    ])
    expect(items).toHaveLength(2) // user bubble + 1 iteration card
    const iter = items[1] as {
      type: 'iter'
      group: { reasoning: string | null; thought: string | null; actions: unknown[] }
    }
    expect(iter.group.reasoning).toBe('R')
    expect(iter.group.thought).toBe('C')
    expect(iter.group.actions).toHaveLength(1)
  })

  it('action without preceding reasoning/thought creates its own card (not dropped)', () => {
    // 回归：模型直接出 tool_calls 时历史无 thought/reasoning，旧版静默丢弃
    // action 导致 Iter 卡消失（历史看不到工具步骤）
    const items = buildHistoryItems([
      msg('user', '/hello-world 回声测试'),
      msg('action', '', { tool_name: 'hello-world', tool_input: { command: 'echo --message 回声' } }),
      msg('observation', '', { tool_output: '{"exit_code":0}' }),
      msg('assistant', '已回声'),
    ])
    expect(items).toHaveLength(3) // user bubble + iteration card + assistant bubble
    const iter = items[1] as {
      type: 'iter'
      group: { iteration: number; reasoning: string | null; thought: string | null; actions: unknown[]; status: string }
    }
    expect(iter.group.iteration).toBe(0)
    expect(iter.group.reasoning).toBeNull()
    expect(iter.group.thought).toBeNull()
    expect(iter.group.actions).toHaveLength(1)
    expect(iter.group.status).toBe('ok')
  })

  it('consecutive orphan actions share one card; error output marks it error', () => {
    const items = buildHistoryItems([
      msg('user', '问'),
      msg('action', '', { tool_name: 'hello-world', tool_input: {} }),
      msg('action', '', { tool_name: 'shell', tool_input: {}, tool_output: '{"error":"boom"}' }),
      msg('assistant', '答'),
    ])
    expect(items).toHaveLength(3)
    const iter = items[1] as {
      type: 'iter'
      group: { actions: unknown[]; status: string }
    }
    expect(iter.group.actions).toHaveLength(2) // 同一轮多次工具调用归入同一卡
    expect(iter.group.status).toBe('error')
  })
})
