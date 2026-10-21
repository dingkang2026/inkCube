/**
 * UsageStats 回归测试（2.10 §6.6 线框 7/§8）。
 *
 * 覆盖：范围切换请求参数（含 projectSignal 初始下钻）、空态、汇总卡×4、
 * SVG 堆叠柱渲染（每天 2 rect）、三明细表映射、项目行点击下钻与清除。
 * i18n 未初始化 → t() 返回 key（同 StatusBar 手法）。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import React from 'react'

import { UsageStats } from '../src/features/usage/UsageStats'

const invokeMock = vi.fn()

/** by_day 两天 × 双色 → 4 rect；range 20,000 = chat 14,000 + vlm 6,000 */
const DATA = {
  today: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, calls: 1 },
  total: { prompt_tokens: 9, completion_tokens: 9, total_tokens: 18, calls: 9, models_count: 3 },
  range: { prompt_tokens: 14000, completion_tokens: 6000, total_tokens: 20000, calls: 7 },
  range_key: '7d',
  by_day: [
    { grp: '2026-10-15', prompt_tokens: 9000, completion_tokens: 3000, total_tokens: 12000, calls: 4 },
    { grp: '2026-10-16', prompt_tokens: 5000, completion_tokens: 3000, total_tokens: 8000, calls: 3 },
  ],
  by_source: [
    { grp: 'chat', prompt_tokens: 10000, completion_tokens: 4000, total_tokens: 14000, calls: 5 },
    { grp: 'vlm', prompt_tokens: 4000, completion_tokens: 2000, total_tokens: 6000, calls: 2 },
  ],
  by_model: [
    { grp: 'gpt-x', prompt_tokens: 9000, completion_tokens: 3000, total_tokens: 12000, calls: 4 },
  ],
  by_project: [
    { grp: 'p1', project_name: '测试项目', prompt_tokens: 5000, completion_tokens: 3000, total_tokens: 8000, calls: 3 },
  ],
}

const EMPTY = { ...DATA, range: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, calls: 0 }, by_day: [] }

const SIGNAL = { projectId: 'p1' }

function summaryCalls(): Array<Record<string, unknown>> {
  return invokeMock.mock.calls
    .filter(([m]) => m === 'usage:summary')
    .map(([, payload]) => payload as Record<string, unknown>)
}

beforeEach(() => {
  invokeMock.mockReset()
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

async function renderWith(payload: unknown, props?: { projectId?: string | null }) {
  invokeMock.mockImplementation(() => Promise.resolve(payload))
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
  const r = render(<UsageStats projectSignal={{ projectId: props?.projectId ?? null }} />)
  await act(async () => {})
  return r
}

describe('UsageStats 请求参数', () => {
  it('挂载默认 7d；projectSignal.projectId 透传为初始下钻', async () => {
    await renderWith(DATA, { projectId: 'p1' })
    expect(summaryCalls()[0]).toEqual({ rangeKey: '7d', projectId: 'p1' })
  })

  it('点击 30d 范围按钮 → 带新 rangeKey 重取，projectId 保持', async () => {
    const { container } = await renderWith(DATA, { projectId: 'p1' })
    fireEvent.click(screen.getByText('usage.range30d'))
    await act(async () => {})
    const calls = summaryCalls()
    expect(calls.length).toBe(2)
    expect(calls[1]).toEqual({ rangeKey: '30d', projectId: 'p1' })
    // 当前范围按钮高亮态存在
    expect(container.querySelector('[class*="rangeBtnActive"]')).toBeTruthy()
  })
})

describe('UsageStats 空态与汇总卡', () => {
  it('无任何用量 → usage.empty 空态，不渲染汇总卡与趋势', async () => {
    const { container } = await renderWith(EMPTY)
    expect(screen.getByText('usage.empty')).toBeTruthy()
    expect(container.querySelector('[class*="summaryCards"]')).toBeNull()
    expect(container.querySelectorAll('rect').length).toBe(0)
  })

  it('汇总卡×4：输入/输出/调用/模型数（千分位完整值）', async () => {
    const { container } = await renderWith(DATA)
    const values = Array.from(
      container.querySelectorAll('[class*="cardValue"]'),
    ).map((el) => el.textContent)
    expect(values).toEqual(['14,000', '6,000', '7', '3'])
    expect(container.querySelectorAll('[class*="cardLabel"]')[3].textContent).toBe('usage.sumModels')
  })

  it('加载中（invoke 未决）渲染骨架灰块，不闪「暂无用量数据」假空态', async () => {
    // 回归（2026-10-16 UI review）：loading 与空态严格区分——首次拉取/Python
    // 未就绪时（data=null）汇总卡/趋势/表格渲染骨架（§6.3/线框 7），empty 文案
    // 仅在确认无数据后出现
    invokeMock.mockImplementation(() => new Promise(() => {}))
    ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
    const { container } = render(<UsageStats />)
    await act(async () => {})
    expect(container.querySelectorAll('[class*="skeleton"]').length).toBeGreaterThanOrEqual(4)
    expect(screen.queryByText('usage.empty')).toBeNull()
  })
})

describe('UsageStats 趋势与三明细表', () => {
  it('SVG 堆叠柱：每天 2 rect（输入+输出），坐标按占比铺排', async () => {
    const { container } = await renderWith(DATA)
    const rects = container.querySelectorAll('rect')
    expect(rects.length).toBe(4)
    // 第一天输入 9000/12000 × 64 → 高 48（占比柱高按 total 归一）
    expect(rects[0].getAttribute('height')).toBe('48')
    // 日期轴首尾
    const axis = container.querySelector('[class*="trendAxis"]')!.textContent!
    expect(axis).toContain('2026-10-15')
    expect(axis).toContain('2026-10-16')
  })

  it('来源表映射 i18n 标签键 + 占比条；模型表/项目表渲染原始值', async () => {
    const { container } = await renderWith(DATA)
    // rowName 按文档序：来源两行 → 模型一行 → 项目一行（tableRow 父容器含嵌套，以 rowName 为准）
    const names = Array.from(container.querySelectorAll('[class*="rowName"]'))
      .map((el) => el.textContent)
    expect(names[0]).toBe('usage.srcChat')
    expect(names[1]).toBe('usage.srcVlm')
    expect(names[2]).toBe('gpt-x')
    expect(names[3]).toBe('测试项目')
    // 来源行首行数值段：紧凑值 + 调用次数
    const firstNums = container.querySelector('[class*="rowNums"]')!.textContent!
    expect(firstNums).toBe('↑10k ↓4k · 5')
    expect(container.querySelectorAll('[class*="shareBar"]').length).toBeGreaterThanOrEqual(4)
  })

  it('项目行点击下钻 → 带 projectId 重取；× 清除 → projectId 置空重取', async () => {
    const { container } = await renderWith(DATA, { projectId: null })
    // hook 以 { rangeKey, projectId } 形式传参；null 下钻经 ?? undefined 归一
    expect(summaryCalls()[0]).toEqual({ rangeKey: '7d', projectId: undefined })
    // 项目行在 by_project 卡片内（select 的 option 同名文本，不能全局 getByText）
    const namesAll = container.querySelectorAll('[class*="rowName"]')
    fireEvent.click(namesAll[3] as Element)
    await act(async () => {})
    expect(summaryCalls()[1]).toEqual({ rangeKey: '7d', projectId: 'p1' })
    // 清除筛选按钮出现并生效
    const clear = document.querySelector('[title="usage.clearFilter"]')
    expect(clear).toBeTruthy()
    fireEvent.click(clear as Element)
    await act(async () => {})
    expect(summaryCalls()[2]).toEqual({ rangeKey: '7d', projectId: undefined })
  })
})
