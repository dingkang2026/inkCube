/**
 * StatusBar 回归测试（2.10 §6.2/§8）。
 *
 * 覆盖：五段渲染（今日/总计/活动数/模型数/版本）、点击导航回调、
 * `app:usage-changed` 事件 1.5s 去抖合并重取、窗口 focus 自愈重取。
 * ActivityContext 以模块 mock 注入（真实 Provider 自带轮询，与 fake timers 冲突）；
 * i18n 未初始化时 t() 返回 key 本身，断言以 key + 格式化值为准（同 ProjectChatPanel）。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import React from 'react'

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({
    summary: {
      active_files: 1, active_compiles: 0, generating_reports: 0,
      running_tasks: 2, downloading_models: 0,
      analyzing_form_templates: 0, filling_forms: 0,
      active_ppts: 1,
    },
  }),
}))

import { StatusBar } from '../src/features/usage/StatusBar'

const invokeMock = vi.fn()

const SUMMARY = {
  today: { prompt_tokens: 1500, completion_tokens: 500, total_tokens: 2000, calls: 3 },
  total: { prompt_tokens: 10000, completion_tokens: 4000, total_tokens: 14000, calls: 20, models_count: 2 },
  range: { prompt_tokens: 10000, completion_tokens: 4000, total_tokens: 14000, calls: 20 },
  range_key: 'all',
  by_day: [], by_source: [], by_model: [], by_project: [],
}

beforeEach(() => {
  vi.useFakeTimers()
  invokeMock.mockReset()
  invokeMock.mockImplementation((method: string) => {
    if (method === 'app:version') return Promise.resolve('1.2.3')
    if (method === 'usage:summary') return Promise.resolve(SUMMARY)
    return Promise.resolve(null)
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

async function renderBar() {
  const r = render(
    <StatusBar onOpenHome={vi.fn()} onOpenStats={vi.fn()} onShowAbout={vi.fn()} />,
  )
  await act(async () => {}) // flush 挂载期 invoke
  return r
}

function summaryCalls(): Array<[string, unknown]> {
  return invokeMock.mock.calls.filter(([m]) => m === 'usage:summary') as Array<[string, unknown]>
}

describe('StatusBar 渲染', () => {
  it('五段呈现：今日/总计紧凑值+tooltip 千分位/活动数求和/模型数/版本', async () => {
    await renderBar()
    // 今日：紧凑化 1500→1.5k、500 原样
    expect(screen.getByText(/usage\.today/).textContent).toBe('usage.today ↑1.5k ↓500')
    // 总计：行内紧凑值（§6.2 数字格式，14000→14k），完整千分位在 tooltip
    const totalSeg = screen.getByText(/usage\.total/)
    expect(totalSeg.textContent).toBe('usage.total 14k tokens · usage.titleCalls')
    expect(totalSeg.getAttribute('title')).toBe(
      'usage.titleTotal: 14,000 tokens · usage.titleCalls')
    // 活动数：八字段求和（1 + 2 + 1 = 4，含 2.3 PPT）
    expect(screen.getByText(/usage\.activeUnit/).textContent).toBe('4 usage.activeUnit')
    // 模型数 + 版本
    expect(screen.getByText(/usage\.modelsUnit/).textContent).toBe('2 usage.modelsUnit')
    expect(screen.getByText('v1.2.3')).toBeTruthy()
    // 挂载时恰一次 usage:summary + 一次 app:version
    expect(summaryCalls().length).toBe(1)
  })
})

describe('StatusBar 导航', () => {
  it('点击分段触发对应回调（今日/总计/模型 → 统计页，活动 → 首页，版本 → 关于）', async () => {
    const onOpenStats = vi.fn()
    const onOpenHome = vi.fn()
    const onShowAbout = vi.fn()
    render(<StatusBar onOpenHome={onOpenHome} onOpenStats={onOpenStats} onShowAbout={onShowAbout} />)
    await act(async () => {})
    fireEvent.click(screen.getByText(/usage\.today/))
    fireEvent.click(screen.getByText(/usage\.total/))
    fireEvent.click(screen.getByText(/usage\.modelsUnit/))
    expect(onOpenStats).toHaveBeenCalledTimes(3)
    fireEvent.click(screen.getByText(/usage\.activeUnit/))
    expect(onOpenHome).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('v1.2.3'))
    expect(onShowAbout).toHaveBeenCalledTimes(1)
  })
})

describe('StatusBar L2 刷新（§5.4）', () => {
  it('app:usage-changed 双事件 1.5s 去抖合并为一次重取', async () => {
    await renderBar()
    expect(summaryCalls().length).toBe(1)
    for (let i = 0; i < 2; i++) {
      window.dispatchEvent(new Event('app:usage-changed'))
    }
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(summaryCalls().length).toBe(1) // 去抖窗口内不重取
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(summaryCalls().length).toBe(2) // 1.5s 后恰一次
  })

  it('窗口 focus（可见态）立即自愈重取，覆盖跨天/长挂起过期', async () => {
    await renderBar()
    expect(summaryCalls().length).toBe(1)
    window.dispatchEvent(new Event('focus'))
    await act(async () => {})
    expect(summaryCalls().length).toBe(2)
  })
})
