/** tests/BrowserPanel.test.tsx — 内嵌浏览器面板（2.8 §8）
 *  覆盖：三态切换 / 关闭保状态（webview 不卸载）/ crash 自愈 /
 *  navigate 导航 / webview partition·待命 src；preload 由主进程
 *  will-attach-webview 注入，渲染层不写 preload 属性（防回归断言）。
 *  jsdom 无真实 webview——渲染为自定义元素，断言属性与事件回调。
 *  事件绑定走 ref + addEventListener（Ruling 4：React 对未知标签的
 *  on<hyphen> 属性绑定不可靠）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

import { BrowserPanel } from '../src/features/browser/BrowserPanel'
import { browserPanelStore } from '../src/features/browser/browserPanelStore'

beforeEach(() => {
  browserPanelStore.hide()
})

afterEach(() => {
  cleanup()
})

const getWebview = () => document.querySelector('webview') as HTMLElement
const getWrapper = () => document.querySelector('[data-panel]') as HTMLElement

describe('BrowserPanel (2.8 §8)', () => {
  it('hidden by default: wrapper rendered (webview alive) but moved off-viewport', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    expect(getWrapper().getAttribute('data-visible')).toBe('false')
    expect(getWebview()).toBeTruthy()  // 关闭 ≠ 销毁（§8.3）
  })

  it('showCompact → visible small card; expand/collapse toggle; hide keeps webview', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    act(() => browserPanelStore.showCompact())
    expect(getWrapper().getAttribute('data-visible')).toBe('true')
    expect(getWrapper().getAttribute('data-expanded')).toBe('false')
    fireEvent.click(screen.getByTitle('browserPanel.expand'))
    expect(getWrapper().getAttribute('data-expanded')).toBe('true')
    fireEvent.click(screen.getByTitle('browserPanel.collapse'))
    expect(getWrapper().getAttribute('data-expanded')).toBe('false')
    fireEvent.click(screen.getByTitle('browserPanel.hide'))
    expect(getWebview()).toBeTruthy()  // 状态保留
  })

  it('webview carries partition + standby src; preload stays off the renderer', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    const wv = getWebview()
    expect(wv.getAttribute('partition')).toBe('persist:webfetch')
    expect(wv.getAttribute('src')).toContain('inkcube-fetch-pool')
    // preload 不在渲染层（webPreferences 创建时锁定，异步写属性是 no-op）：
    // 由主进程 will-attach-webview 注入（main.js，2.8 §6.2）
    expect(wv.getAttribute('preload')).toBeNull()
  })

  it('navigate(url) drives webview loadURL and shows panel', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    const loadURISpy = vi.fn()
    Object.defineProperty(getWebview(), 'loadURL', { value: loadURISpy, configurable: true })
    act(() => browserPanelStore.navigate('https://gitlab.corp.cn'))
    expect(getWrapper().getAttribute('data-visible')).toBe('true')
    await vi.waitFor(() => expect(loadURISpy).toHaveBeenCalledWith('https://gitlab.corp.cn'))
  })

  it('crash event → reload webview (§6.2 自愈)', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    const wv = getWebview()
    const reloadSpy = vi.fn()
    Object.defineProperty(wv, 'reload', { value: reloadSpy, configurable: true })
    fireEvent(wv, new Event('crash'))
    await vi.waitFor(() => expect(reloadSpy).toHaveBeenCalled())
  })

  it('page-title-updated → header shows page title', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    // Electron 真实签名 (event, title)；jsdom 用 CustomEvent detail 等价驱动
    //（组件 handler 双通道兼容：title 参优先，detail 兼底）
    fireEvent(getWebview(), new CustomEvent('page-title-updated', { detail: 'GitLab · Dashboard' }))
    await screen.findByText('GitLab · Dashboard')
  })
})
