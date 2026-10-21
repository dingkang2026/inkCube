// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { handleWindowOpen, sanitizeUserAgent } from '../electron/fetch-pool-window'

// window-open 收编决策（2.8 §6.2 V2.1-R5）：deny 弹窗 + 同承载导航。
// 天眼查任务（2026-10-14）实证：target=_blank 详情页放行为独立窗口 →
// agent POOL page 快照零变化 → LLM 误判「未跳转」连走 4 步弯路。

const DENY = { action: 'deny' }

function fakeView(loadURL: (url: string) => Promise<void>) {
  return { webContents: { loadURL } }
}

function flushMicrotasks() {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

describe('handleWindowOpen — window-open 收编（2.8 §6.2 V2.1-R5）', () => {
  it('https URL：deny + 同承载 loadURL 导航', () => {
    const loadURL = vi.fn().mockResolvedValue(undefined)
    const view = fakeView(loadURL)
    expect(handleWindowOpen(view as never, 'https://www.tianyancha.com/company/123')).toEqual(DENY)
    expect(loadURL).toHaveBeenCalledTimes(1)
    expect(loadURL).toHaveBeenCalledWith('https://www.tianyancha.com/company/123')
  })

  it('about:blank 占位弹窗：deny 且不导航（保住原页面状态）', () => {
    const loadURL = vi.fn().mockResolvedValue(undefined)
    const view = fakeView(loadURL)
    expect(handleWindowOpen(view as never, 'about:blank')).toEqual(DENY)
    expect(loadURL).not.toHaveBeenCalled()
  })

  it('空 url：deny 且不导航', () => {
    const loadURL = vi.fn().mockResolvedValue(undefined)
    const view = fakeView(loadURL)
    expect(handleWindowOpen(view as never, '')).toEqual(DENY)
    expect(loadURL).not.toHaveBeenCalled()
  })

  it('view 为 null（承载已销毁防御）：deny 且不抛', () => {
    expect(handleWindowOpen(null, 'https://example.com')).toEqual(DENY)
  })

  it('loadURL 失败（含 SSRF cancel）：catch 内消化，不抛异常逃逸', async () => {
    const loadURL = vi.fn().mockRejectedValue(new Error('ERR_BLOCKED_BY_CLIENT'))
    const view = fakeView(loadURL)
    expect(() => handleWindowOpen(view as never, 'https://example.com')).not.toThrow()
    await flushMicrotasks()
  })
})

describe('sanitizeUserAgent — 环境伪装清洗（2.8 §6.2 V2.1-R6）', () => {
  // 2026-10-14 天眼查登录页 CDP 实测 UA：拼图验证不放行的 UA 根因
  const DIRTY_UA =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) inkcube/0.1.0 Chrome/130.0.6723.191 ' +
    'Electron/33.4.11 Safari/537.36'
  const CLEAN_UA =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) Chrome/130.0.6723.191 Safari/537.36'

  it('剥除 inkcube/<v> 与 Electron/<v> 两段，其余逐字符保留', () => {
    expect(sanitizeUserAgent(DIRTY_UA)).toBe(CLEAN_UA)
  })

  it('幂等：已清洗 UA 再洗一遍不变', () => {
    expect(sanitizeUserAgent(CLEAN_UA)).toBe(CLEAN_UA)
  })

  it('不含应用/框架段的 UA 原样返回', () => {
    expect(sanitizeUserAgent(CLEAN_UA)).toBe(CLEAN_UA)
  })

  it('非字符串输入原样返回（防御）', () => {
    expect(sanitizeUserAgent(undefined as never)).toBe(undefined)
    expect(sanitizeUserAgent(null as never)).toBe(null)
  })
})
