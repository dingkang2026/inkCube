/**
 * UsageRounds 回归测试（2.10 §5.2/§6.3/§8）。
 *
 * usage:taskDetail 孤儿通道补全的前端消费面：点击展开经 IPC 取数、轮次行与
 * 合计渲染、加载/空/失败态、再点收起（收起即弃，重开重新取数）、usage 缺失
 * 不渲染、触发器 stopPropagation。i18n 未初始化 → t() 返回 key（同 UsageStats
 * / StatusBar 手法，插值参数被忽略）。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import React from 'react'

import { UsageRounds } from '../src/features/usage/UsageRounds'
import type { UsageSummary } from '../src/types'

const invokeMock = vi.fn()

const USAGE: UsageSummary = {
  prompt_tokens: 14000,
  completion_tokens: 6000,
  total_tokens: 20000,
  calls: 7,
}
const ROUNDS = [
  {
    round_id: 'r1',
    started_at: '2026-10-16T10:00:00',
    prompt_tokens: 9000,
    completion_tokens: 3000,
    total_tokens: 12000,
    calls: 4,
    models: 'gpt-x',
  },
  {
    round_id: 'r2',
    started_at: '2026-10-16T10:05:00',
    prompt_tokens: 5000,
    completion_tokens: 3000,
    total_tokens: 8000,
    calls: 3,
    models: 'gpt-x,qwen-y',
  },
]

function trigger() {
  return screen.getByRole('button')
}

function detailCalls(): number {
  return invokeMock.mock.calls.filter(([m]) => m === 'usage:taskDetail').length
}

async function renderWith(usage: UsageSummary | null = USAGE) {
  invokeMock.mockImplementation(() =>
    Promise.resolve({ rounds: ROUNDS, has_more: false, total: ROUNDS.length }))
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
  return render(<UsageRounds taskId="t1" usage={usage} />)
}

beforeEach(() => {
  invokeMock.mockReset()
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('UsageRounds 渲染守卫', () => {
  it('usage 缺失或零调用不渲染任何内容（历史任务无记录）', () => {
    const { container: c1 } = render(<UsageRounds taskId="t1" usage={null} />)
    expect(c1.textContent).toBe('')
    cleanup()
    const { container: c2 } = render(<UsageRounds taskId="t1" usage={{ ...USAGE, calls: 0 }} />)
    expect(c2.textContent).toBe('')
  })

  it('默认折叠：只渲染触发器小字，不渲染面板、不 invoke', async () => {
    const { container } = await renderWith()
    expect(trigger().textContent).toBe('↑14k ↓6k')
    expect(container.querySelector('[class*="panel"]')).toBeNull()
    expect(invokeMock).not.toHaveBeenCalled()
  })
})

describe('UsageRounds 展开取数', () => {
  it('点击展开 → invoke("usage:taskDetail", "t1")，渲染轮次行与合计', async () => {
    const { container } = await renderWith()
    fireEvent.click(trigger())
    await act(async () => {})
    expect(invokeMock).toHaveBeenCalledWith(
      'usage:taskDetail', { taskId: 't1', limit: 20 })
    // 合计行同时带 row 类，排除后仅剩两条轮次行
    const rows = container.querySelectorAll('[class*="row"]:not([class*="totalRow"])')
    expect(rows.length).toBe(2)
    expect(rows[0].textContent).toContain('#1')
    expect(rows[0].textContent).toContain('↑9k ↓3k')
    expect(rows[0].textContent).toContain('gpt-x')
    expect(rows[1].textContent).toContain('#2')
    expect(rows[1].textContent).toContain('qwen-y')
    const total = container.querySelector('[class*="totalRow"]')!
    expect(total.textContent).toContain('usage.roundsTotal')
    expect(total.textContent).toContain('↑14k ↓6k')
  })

  it('空轮次 → usage.roundsEmpty 空态，不渲染合计', async () => {
    await renderWith()
    invokeMock.mockImplementation(() =>
      Promise.resolve({ rounds: [], has_more: false, total: 0 })) // 点击前重设（renderWith 内部会置默认实现）
    fireEvent.click(trigger())
    await act(async () => {})
    expect(screen.getByText('usage.roundsEmpty')).toBeTruthy()
    expect(document.querySelector('[class*="totalRow"]')).toBeNull()
  })

  it('invoke 失败 → usage.roundsError；再点触发重试并恢复', async () => {
    await renderWith()
    invokeMock.mockRejectedValueOnce(new Error('boom'))
    fireEvent.click(trigger())
    await act(async () => {})
    expect(screen.getByText('usage.roundsError')).toBeTruthy()
    expect(detailCalls()).toBe(1)
    fireEvent.click(trigger()) // 重试：第二次调用回落到默认实现（ROUNDS）
    await act(async () => {})
    expect(detailCalls()).toBe(2)
    expect(screen.getByText('#1')).toBeTruthy()
    expect(screen.queryByText('usage.roundsError')).toBeNull()
  })

  it('再点收起：面板消失、数据弃置；重开重新取数（收起即弃）', async () => {
    await renderWith()
    fireEvent.click(trigger())
    await act(async () => {})
    expect(screen.getByText('#1')).toBeTruthy()
    fireEvent.click(trigger())
    await act(async () => {})
    expect(screen.queryByText('#1')).toBeNull()
    expect(detailCalls()).toBe(1)
    fireEvent.click(trigger()) // 重开 → 重新 invoke（§5.4 列表刷新后数据新鲜）
    await act(async () => {})
    expect(detailCalls()).toBe(2)
    expect(screen.getByText('#1')).toBeTruthy()
  })

  it('展开中触发器进入高亮态（expanded）', async () => {
    const { container } = await renderWith()
    expect(container.querySelector('[class*="usageBtnOpen"]')).toBeNull()
    fireEvent.click(trigger())
    await act(async () => {})
    expect(container.querySelector('[class*="usageBtnOpen"]')).toBeTruthy()
  })

  it('触发器 stopPropagation：点击不冒泡到外层（防 TaskItem 卡片导航）', async () => {
    const outerClick = vi.fn()
    await renderWith()
    document.body.addEventListener('click', outerClick)
    fireEvent.click(trigger())
    await act(async () => {})
    expect(outerClick).not.toHaveBeenCalled()
    document.body.removeEventListener('click', outerClick)
  })
})
