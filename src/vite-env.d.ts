/// <reference types="vite/client" />

interface ElectronAPI {
  invoke: (channel: string, ...args: unknown[]) => Promise<unknown>
  send: (channel: string, ...args: unknown[]) => void
  on: (channel: string, callback: (...args: unknown[]) => void) => () => void
  /** Electron 32+ 移除 File.path，拖放文件经 webUtils 换取真实磁盘路径 */
  getPathForFile: (file: File) => string
}

interface Window {
  electronAPI: ElectronAPI
}
