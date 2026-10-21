/**
 * browserPanelStore 三态语义（2.8 §8.1/§8.3）。
 * V2.1-R4 产品语义澄清：小卡（compact）= 跟看 LLM 驱动过程；大面板（expanded）
 * = 人工接管交互（输入帐号密码/验证码/2FA）——登录入口 navigate() 必须直接
 * 以大面板打开，360×480 小卡无法完成登录表单操作（2026-10-14 用户反馈实证）。
 *
 * V2.1-R10 type 输入可见性（2026-10-14 用户澄清：手动放大已覆盖接管场景，
 * 缺的是「agent 输入时用户看得见输入框」）：type 命令 → expandForInput()
 * 自动放大并标记 autoExpanded；observation 到达 → collapseIfAuto() 仅对
 * auto 放大回小卡；用户手动 expand/collapse 一律解除 auto（手动优先）。
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { browserPanelStore } from '../src/features/browser/browserPanelStore'

beforeEach(() => {
  browserPanelStore.hide()
  // hide 不重置 expanded/currentUrl（关闭 ≠ 销毁），显式归位到隐匿默认态
  browserPanelStore.collapse()
  browserPanelStore.hide()
})

describe('browserPanelStore（2.8 §8.1 三态 + §8.3 登录入口）', () => {
  it('navigate（登录入口）直接以大面板打开并携带导航目标', () => {
    browserPanelStore.navigate('https://example.com/login')
    const s = browserPanelStore.getState()
    expect(s.visible).toBe(true)
    expect(s.expanded).toBe(true)
    expect(s.currentUrl).toBe('https://example.com/login')
  })

  it('showCompact（LLM 驱动感知）只浮现小卡，不动导航位置（§8.2）', () => {
    const urlBefore = browserPanelStore.getState().currentUrl
    browserPanelStore.showCompact()
    const s = browserPanelStore.getState()
    expect(s.visible).toBe(true)
    expect(s.expanded).toBe(false)
    expect(s.currentUrl).toBe(urlBefore)
  })

  it('expand / collapse 切换尺寸态且保持可见', () => {
    browserPanelStore.showCompact()
    browserPanelStore.expand()
    expect(browserPanelStore.getState()).toMatchObject({ visible: true, expanded: true })
    browserPanelStore.collapse()
    expect(browserPanelStore.getState()).toMatchObject({ visible: true, expanded: false })
  })

  it('hide 仅隐藏（关闭 ≠ 销毁）：expanded 与 currentUrl 保留，复现即恢复', () => {
    browserPanelStore.navigate('https://example.com/login')
    browserPanelStore.hide()
    const s = browserPanelStore.getState()
    expect(s.visible).toBe(false)
    expect(s.expanded).toBe(true)
    expect(s.currentUrl).toBe('https://example.com/login')
  })

  it('订阅通知：写入触发监听，退订后不再触发', () => {
    let notified = 0
    const unsub = browserPanelStore.subscribe(() => { notified += 1 })
    browserPanelStore.showCompact()
    unsub()
    browserPanelStore.showCompact()
    expect(notified).toBe(1)
  })
})

describe('browserPanelStore — type 输入可见性自动放大（V2.1-R10）', () => {
  it('expandForInput 在 hidden 态不动作（浮现小卡仍是 showCompact 的职责）', () => {
    expect(browserPanelStore.getState().visible).toBe(false)
    browserPanelStore.expandForInput()
    expect(browserPanelStore.getState()).toMatchObject({ visible: false, expanded: false, autoExpanded: false })
  })

  it('compact 下 expandForInput 放大并标记 auto；collapseIfAuto 回小卡', () => {
    browserPanelStore.showCompact()
    browserPanelStore.expandForInput()
    expect(browserPanelStore.getState().expanded).toBe(true)
    expect(browserPanelStore.getState().autoExpanded).toBe(true)
    browserPanelStore.collapseIfAuto()
    expect(browserPanelStore.getState()).toMatchObject({ visible: true, expanded: false, autoExpanded: false })
  })

  it('collapseIfAuto 仅对 auto 放大生效——用户手动 expand 后不缩', () => {
    browserPanelStore.showCompact()
    browserPanelStore.expand() // 用户手动放大
    expect(browserPanelStore.getState().autoExpanded).toBe(false)
    browserPanelStore.collapseIfAuto()
    expect(browserPanelStore.getState().expanded).toBe(true) // 保持用户选择
  })

  it('type 执行中用户手动缩小 → auto 解除，collapseIfAuto 不再放大', () => {
    browserPanelStore.showCompact()
    browserPanelStore.expandForInput()
    browserPanelStore.collapse() // 用户手动缩回小卡
    browserPanelStore.collapseIfAuto()
    expect(browserPanelStore.getState()).toMatchObject({ visible: true, expanded: false, autoExpanded: false })
  })

  it('showCompact 清除 auto 标记（下一个非 type action 回跟看态）', () => {
    browserPanelStore.showCompact()
    browserPanelStore.expandForInput()
    browserPanelStore.showCompact() // click/open 等 action 到达
    browserPanelStore.collapseIfAuto()
    expect(browserPanelStore.getState().expanded).toBe(false) // 已被 showCompact 缩回
  })

  it('面板关闭后来 type：showCompact 先浮现 → expandForInput 照常放大（输入不因关闭而不可见）', () => {
    // 真实调用顺序 = useTaskChat action handler：showCompact 在前、
    // expandForInput 在后——关闭只是暂隐到下一个动作，承载存活、驱动不停
    browserPanelStore.hide() // 用户已关闭面板
    browserPanelStore.showCompact() // type 命令到达（每个 browser-fetch action 都浮现）
    browserPanelStore.expandForInput()
    expect(browserPanelStore.getState()).toMatchObject({ visible: true, expanded: true, autoExpanded: true })
  })
})
