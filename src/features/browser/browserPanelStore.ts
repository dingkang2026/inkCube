/** browserPanelStore（2.8 §8.1）——内嵌浏览器面板全局状态。
 *  零依赖模块级 store（useSyncExternalStore）；写入方三处：
 *  useTaskChat action 流（showCompact，小卡跟看过程；expandForInput，
 *  type 输入可见性自动放大）/ 任务横幅与设置页（navigate，大面板人工
 *  交互）。语义分工：小卡 = 跟看；大面板 = 接管；type 期间自动放大 =
 *  输入可见（V2.1-R10）。 */
import { useSyncExternalStore } from 'react'

export interface BrowserPanelState {
  visible: boolean
  expanded: boolean
  /** 当前 expanded 是否由 type 自动触发（V2.1-R10）：observation 到达时
   *  仅 auto 放大才回小卡；用户手动 expand/collapse/showCompact 置 false
   *  （手动优先，自动不覆盖人的选择）。 */
  autoExpanded: boolean
  /** 登录导航目标（面板 header 呈现；null = 未指定，保持当前页） */
  currentUrl: string | null
}

let state: BrowserPanelState = { visible: false, expanded: false, autoExpanded: false, currentUrl: null }
const listeners = new Set<() => void>()

function setState(patch: Partial<BrowserPanelState>) {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

export const browserPanelStore = {
  subscribe(l: () => void) {
    listeners.add(l)
    return () => { listeners.delete(l) }
  },
  getState: (): BrowserPanelState => state,
  /** LLM 开始驱动（action 流感知）→ 浮现小卡；不改导航位置（§8.2）。
   *  同时解除 auto 标记：type 完成后下一个 action 回跟看态。 */
  showCompact() { setState({ visible: true, expanded: false, autoExpanded: false }) },
  expand() { setState({ visible: true, expanded: true, autoExpanded: false }) },
  collapse() { setState({ visible: true, expanded: false, autoExpanded: false }) },
  /** type 输入可见性（V2.1-R10，useTaskChat action 流感知 type 命令）：
   *  自动放大让用户看清输入框与键入内容；hidden 态不动作（浮现仍归
   *  showCompact）。auto 标记使 observation 到达时能区分回缩对象。 */
  expandForInput() {
    if (!state.visible) return
    setState({ expanded: true, autoExpanded: true })
  },
  /** type 完成（observation 到达）→ 仅 auto 放大回小卡；用户手动放大
   *  或已手动缩回时为空操作（幂等）。 */
  collapseIfAuto() {
    if (state.autoExpanded) setState({ expanded: false, autoExpanded: false })
  },
  /** 关闭 ≠ 中断：仅隐藏，主进程承载存活、导航状态保留（§8.3） */
  hide() { setState({ visible: false, autoExpanded: false }) },
  /** 登录入口：打开面板并导航（任务横幅 / 设置页两处调用，§4.1 写路径）。
   *  直接以大面板（expanded）打开：登录是人工交互场景（输入帐号密码/
   *  验证码/2FA），360×480 小卡无法完成表单操作（§8.3，V2.1-R4）。 */
  navigate(url: string) { setState({ visible: true, expanded: true, autoExpanded: false, currentUrl: url }) },
}

export function useBrowserPanel(): BrowserPanelState {
  return useSyncExternalStore(browserPanelStore.subscribe, browserPanelStore.getState)
}
