/** browserPanelStore（2.8 §8.1）——内嵌浏览器面板全局状态。
 *  零依赖模块级 store（useSyncExternalStore）；写入方三处：
 *  useTaskChat action 流（showCompact）/ 任务横幅与设置页（navigate）。 */
import { useSyncExternalStore } from 'react'

export interface BrowserPanelState {
  visible: boolean
  expanded: boolean
  /** 登录导航目标（面板 header 呈现；null = 未指定，保持当前页） */
  currentUrl: string | null
}

let state: BrowserPanelState = { visible: false, expanded: false, currentUrl: null }
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
  /** LLM 开始驱动（action 流感知）→ 浮现小卡；不改导航位置（§8.2） */
  showCompact() { setState({ visible: true, expanded: false }) },
  expand() { setState({ visible: true, expanded: true }) },
  collapse() { setState({ visible: true, expanded: false }) },
  /** 关闭 ≠ 中断：仅隐藏，webview 存活、导航状态保留（§8.3） */
  hide() { setState({ visible: false }) },
  /** 登录入口：打开面板并导航（任务横幅 / 设置页两处调用，§4.1 写路径） */
  navigate(url: string) { setState({ visible: true, currentUrl: url }) },
}

export function useBrowserPanel(): BrowserPanelState {
  return useSyncExternalStore(browserPanelStore.subscribe, browserPanelStore.getState)
}
