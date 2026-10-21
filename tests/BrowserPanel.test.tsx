/** tests/BrowserPanel.test.tsx — 内嵌浏览器面板（2.8 §8，WCV 承载）
 *  V2.1：承载为主进程 WebContentsView——<webview> guest 的 CDP target
 *  type=webview，playwright 不识别（2026-10-14 运行时实证 + driver 源码）。
 *  渲染层只负责面板 chrome：矩形经 webfetch:set-bounds 上报（ResizeObserver
 *  → rAF 合帧，hidden 不上报防 -10000 污染），可见性经 webfetch:set-visible，
 *  页面事件经 webfetch:event 推回（title 跟随 / crashed 提示，自愈在主进程），
 *  导航经 webfetch:navigate。关闭 ≠ 销毁：占位 div 恒渲染（§8.3）。
 *  jsdom 无 ResizeObserver → stub 驱动；electronAPI 全 mock。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

import { BrowserPanel } from '../src/features/browser/BrowserPanel'
import { browserPanelStore } from '../src/features/browser/browserPanelStore'

// ── ResizeObserver stub：jsdom 无此 API；用例手动 emit 触发回调 ──
class ROStub {
  static instances: ROStub[] = []
  private cb: ResizeObserverCallback
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
    ROStub.instances.push(this)
  }
  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
  emit() {
    this.cb([] as unknown as ResizeObserverEntry[], this as unknown as ResizeObserver)
  }
}
vi.stubGlobal('ResizeObserver', ROStub)

// ── electronAPI mock：invoke/send 载荷分别捕获（语义对齐真实 preload：
// set-bounds/set-visible 是 ipcMain.on 通道必须走 send，navigate 是 ipcMain.handle 走 invoke）──
const invokeMock = vi.fn()
const sendMock = vi.fn()
let eventCb: ((payload: unknown) => void) | null = null
const onMock = vi.fn((channel: string, cb: (payload: unknown) => void) => {
  if (channel === 'webfetch:event') eventCb = cb
  return () => {
    if (eventCb === cb) eventCb = null
  }
})

beforeEach(() => {
  browserPanelStore.hide()
  ROStub.instances = []
  eventCb = null
  invokeMock.mockReset().mockResolvedValue(undefined)
  sendMock.mockReset()
  onMock.mockClear()
  Object.defineProperty(window, 'electronAPI', {
    configurable: true,
    value: { invoke: invokeMock, send: sendMock, on: onMock },
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const getHost = () => document.querySelector('[data-pool-host]') as HTMLElement
const getWrapper = () => document.querySelector('[data-panel]') as HTMLElement

/** host 矩形固定为 header 下方 360×426@(8,48)，供 bounds 载荷断言 */
const withHostRect = (run: () => void) => {
  vi.spyOn(getHost(), 'getBoundingClientRect').mockReturnValue(
    { x: 8, y: 48, width: 360, height: 426 } as DOMRect,
  )
  run()
}

/** 等真实 rAF 跑一帧（rAF 合帧逻辑的确定性驱动） */
const nextFrame = () =>
  act(async () => {
    await new Promise((r) => requestAnimationFrame(() => r(null)))
  })

describe('BrowserPanel (2.8 §8, WebContentsView 承载)', () => {
  it('mounts host placeholder; <webview> is gone; initial hidden state pushed', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    expect(getHost()).toBeTruthy()
    // V2.1 回归：webview 承载不得复活（复活即 CDP 不可见 bug 回归）
    expect(document.querySelector('webview')).toBeNull()
    await vi.waitFor(() => expect(sendMock).toHaveBeenCalledWith('webfetch:set-visible', false))
  })

  it('showCompact → set-visible(true) + bounds push from host rect', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    invokeMock.mockClear()
    sendMock.mockClear()
    withHostRect(() => act(() => browserPanelStore.showCompact()))
    await vi.waitFor(() => expect(sendMock).toHaveBeenCalledWith('webfetch:set-visible', true))
    await vi.waitFor(() =>
      expect(sendMock).toHaveBeenCalledWith('webfetch:set-bounds', {
        x: 8, y: 48, width: 360, height: 426,
      }),
    )
  })

  it('ResizeObserver → rAF-debounced bounds push while visible', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    act(() => browserPanelStore.showCompact())
    await vi.waitFor(() => expect(ROStub.instances.length).toBeGreaterThan(0))
    const ro = ROStub.instances[0]
    expect(ro.observe).toHaveBeenCalledWith(getHost())
    sendMock.mockClear()
    withHostRect(() => act(() => ro.emit()))
    await vi.waitFor(() =>
      expect(sendMock).toHaveBeenCalledWith('webfetch:set-bounds', {
        x: 8, y: 48, width: 360, height: 426,
      }),
    )
  })

  it('bounds not pushed while hidden (off-viewport rect must not leak)', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    await vi.waitFor(() => expect(ROStub.instances.length).toBeGreaterThan(0))
    sendMock.mockClear()
    act(() => ROStub.instances[0].emit())
    await nextFrame()
    expect(sendMock).not.toHaveBeenCalledWith('webfetch:set-bounds', expect.anything())
  })

  it('window resize while visible → bounds re-pushed (compact 固定尺寸 RO 盲区，2026-10-14)', async () => {
    // compact 面板 fixed 360×480：窗口 resize 时 host 尺寸不变 → ResizeObserver
    // 不触发，而 chrome 的 right 锚点已随窗口移动 → WCV 停旧坐标 = 错位。
    // window resize 事件必须补报。
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    act(() => browserPanelStore.showCompact())
    await vi.waitFor(() => expect(sendMock).toHaveBeenCalledWith('webfetch:set-bounds', expect.anything()))
    sendMock.mockClear()
    withHostRect(() =>
      act(() => {
        window.dispatchEvent(new Event('resize'))
      }),
    )
    await nextFrame()
    expect(sendMock).toHaveBeenCalledWith('webfetch:set-bounds', {
      x: 8, y: 48, width: 360, height: 426,
    })
  })

  it('window resize while hidden → no push (防 -10000 泄漏，与 RO 同规)', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    sendMock.mockClear()
    act(() => {
      window.dispatchEvent(new Event('resize'))
    })
    await nextFrame()
    expect(sendMock).not.toHaveBeenCalledWith('webfetch:set-bounds', expect.anything())
  })

  it('hide → set-visible(false); host stays mounted (承载不销毁, §8.3)', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    act(() => browserPanelStore.showCompact())
    await vi.waitFor(() => expect(sendMock).toHaveBeenCalledWith('webfetch:set-visible', true))
    sendMock.mockClear()
    act(() => browserPanelStore.hide())
    await vi.waitFor(() => expect(sendMock).toHaveBeenCalledWith('webfetch:set-visible', false))
    expect(getHost()).toBeTruthy()
  })

  it('navigate(url) → webfetch:navigate push + panel shows', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    act(() => browserPanelStore.navigate('https://gitlab.corp.cn'))
    await vi.waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('webfetch:navigate', 'https://gitlab.corp.cn'),
    )
    expect(getWrapper().getAttribute('data-visible')).toBe('true')
  })

  it('IPC semantics: set-bounds/set-visible go through send (ipcMain.on); only navigate invokes (ipcMain.handle)', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    invokeMock.mockClear()
    sendMock.mockClear()
    withHostRect(() => act(() => browserPanelStore.navigate('https://x.cn')))
    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalledWith('webfetch:navigate', 'https://x.cn'))
    // set-bounds/set-visible 注册在 ipcMain.on——invoke 会因无 handler 必 reject
    // 且被吞掉（2026-10-14 dev 日志实证：承载永不可见）。此断言防语义回退。
    expect(invokeMock).not.toHaveBeenCalledWith('webfetch:set-bounds', expect.anything())
    expect(invokeMock).not.toHaveBeenCalledWith('webfetch:set-visible', expect.anything())
    expect(sendMock).toHaveBeenCalledWith('webfetch:set-visible', true)
    expect(sendMock).toHaveBeenCalledWith('webfetch:set-bounds', { x: 8, y: 48, width: 360, height: 426 })
  })

  it('webfetch:event crashed → hint; title → header title + hint cleared', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    expect(eventCb).toBeTruthy()
    act(() => eventCb?.({ type: 'crashed' }))
    await screen.findByText('browserPanel.crashReload')
    act(() => eventCb?.({ type: 'title', title: 'GitLab · Dashboard' }))
    await screen.findByText('GitLab · Dashboard')
    expect(screen.queryByText('browserPanel.crashReload')).toBeNull()
  })

  it('expand/collapse toggle unchanged (三态 UI)', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    act(() => browserPanelStore.showCompact())
    expect(getWrapper().getAttribute('data-expanded')).toBe('false')
    fireEvent.click(screen.getByTitle('browserPanel.expand'))
    expect(getWrapper().getAttribute('data-expanded')).toBe('true')
    fireEvent.click(screen.getByTitle('browserPanel.collapse'))
    expect(getWrapper().getAttribute('data-expanded')).toBe('false')
  })
})
