/**
 * usageBubble — 用量小字回归测试（2.10 §6.5 线框 2/3/4/6/§8）。
 *
 * UsageText 四态：正常渲染（紧凑值 + 千分位 tooltip + 多模型）、calls=0 不渲染、
 * usage 缺失不渲染、models 缺省（列表行聚合无 models 字段）。另附 format.ts
 * 纯函数紧凑化/千分位/归一断言。
 * i18n 未初始化 → t() 返回 key（同 StatusBar/UsageStats 手法）。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import React from 'react'

import { UsageText } from '../src/features/usage/UsageText'
import { compactTokens, thousands, modelsList, USAGE_SOURCE_LABEL_KEYS } from '../src/features/usage/format'
import type { UsageSummary } from '../src/types'

beforeEach(() => {
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: vi.fn() }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const FULL: UsageSummary = {
  prompt_tokens: 1500,
  completion_tokens: 500,
  total_tokens: 2000,
  calls: 3,
  models: 'gpt-x,gpt-mini',
}

describe('UsageText 渲染', () => {
  it('正常用量：紧凑 ↑↓ 正文 + tooltip 千分位完整值/次数/去重模型', () => {
    const { container } = render(<UsageText usage={FULL} />)
    expect(container.querySelector('[class*="usage"]')!.textContent).toBe('↑1.5k ↓500')
    const title = container.querySelector('[title]')!.getAttribute('title')!
    expect(title).toContain('usage.titleInput: 1,500')
    expect(title).toContain('usage.titleOutput: 500')
    expect(title).toContain('usage.titleTotal: 2,000 tokens')
    expect(title).toContain('usage.titleCalls')
    expect(title).toContain('gpt-x')
    expect(title).toContain('gpt-mini')
  })

  it('calls=0（空聚合）与 usage 缺失（历史消息无记录）均不渲染', () => {
    const { container: zero } = render(<UsageText usage={{ ...FULL, calls: 0 }} />)
    expect(zero.querySelector('[class*="usage"]')).toBeNull()
    const { container: none } = render(<UsageText usage={undefined} />)
    expect(none.querySelector('[class*="usage"]')).toBeNull()
  })

  it('列表行聚合无 models 字段：tooltip 不含模型段，正文正常', () => {
    const { container } = render(
      <UsageText usage={{ prompt_tokens: 999, completion_tokens: 1, total_tokens: 1000, calls: 1 }} />,
    )
    expect(container.querySelector('[class*="usage"]')!.textContent).toBe('↑999 ↓1')
    expect(container.querySelector('[title]')!.getAttribute('title')).not.toContain('undefined')
  })
})

describe('format 纯函数', () => {
  it('compactTokens：千位内原样 / k / M，整值去尾 .0', () => {
    expect(compactTokens(0)).toBe('0')
    expect(compactTokens(999)).toBe('999')
    expect(compactTokens(1000)).toBe('1k')
    expect(compactTokens(1500)).toBe('1.5k')
    expect(compactTokens(12345)).toBe('12.3k')
    expect(compactTokens(1_000_000)).toBe('1M')
    expect(compactTokens(3_400_000)).toBe('3.4M')
  })

  it('thousands 千分位完整值（tooltip/汇总卡精确值原则）', () => {
    expect(thousands(0)).toBe('0')
    expect(thousands(14000)).toBe('14,000')
    expect(thousands(1_234_567)).toBe('1,234,567')
  })

  it('modelsList：GROUP_CONCAT 逗号串 → 数组；空/数组形态容错', () => {
    expect(modelsList(undefined)).toEqual([])
    expect(modelsList('a')).toEqual(['a'])
    expect(modelsList('a,b')).toEqual(['a', 'b'])
    expect(modelsList(['a', 'b'])).toEqual(['a', 'b'])
  })

  it('来源枚举 → i18n 键映射（统计页来源表/徽章分拆共用）', () => {
    expect(USAGE_SOURCE_LABEL_KEYS.task).toBe('usage.srcTask')
    expect(USAGE_SOURCE_LABEL_KEYS.other).toBe('usage.srcOther')
    // vlm 保留作历史数据渲染；knowledge 为 2026-10-21 图片描述改走 LLM 链路后的新来源
    expect(USAGE_SOURCE_LABEL_KEYS.vlm).toBe('usage.srcVlm')
    expect(USAGE_SOURCE_LABEL_KEYS.knowledge).toBe('usage.srcKnowledge')
    expect(Object.keys(USAGE_SOURCE_LABEL_KEYS).sort()).toEqual(
      ['chat', 'form', 'knowledge', 'other', 'report', 'task', 'vlm', 'wiki'],
    )
  })
})
