/// <reference types="vite/client" />

interface ElectronAPI {
  invoke: (channel: string, ...args: unknown[]) => Promise<unknown>
  send: (channel: string, ...args: unknown[]) => void
  on: (channel: string, callback: (...args: unknown[]) => void) => () => void
  getPythonBaseUrl: () => Promise<string>
}

interface Window {
  electronAPI: ElectronAPI
}

/** Electron <webview> 标签（2.8 §6.2 V2 BrowserPanel；lib.dom 不含）。
 *  事件（crash/page-title-updated）走 ref + addEventListener，不入 JSX 属性；
 *  preload 由主进程 will-attach-webview 注入，渲染层不写属性。 */
declare global {
  namespace JSX {
    interface IntrinsicElements {
      webview: React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string
        partition?: string
        allowpopups?: boolean
        useragent?: string
      }
    }
  }
}

declare namespace Electron {
  interface WebviewTag extends HTMLElement {
    loadURL(url: string): void
    reload(): void
    getURL(): string
    getTitle(): string
  }
}
