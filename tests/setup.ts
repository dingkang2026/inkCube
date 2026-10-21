/** tests/setup.ts — vitest 全局测试装配（vite.config.ts test.setupFiles）
 *  RTL 自动 cleanup 依赖 vitest globals（项目未开启），必须显式注册：
 *  每个用例后卸载组件——否则组件实例跨用例泄漏，其全局事件监听
 *  （如 ActivityMonitor 的 visibilitychange）会跨用例响应并污染
 *  后续用例的 mock 计数（2026-10-05 ActivityMonitor 回归测试实测）。
 */
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// jsdom 无 ResizeObserver；渲染 App 的测试（如 AppSidebar → BrowserPanel）
// 与 WikiGraphView 均直接 new ResizeObserver，全局提供最小 stub（不驱动回调；
// 需要驱动回调的用例在各自文件里 vi.stubGlobal 覆盖，如 BrowserPanel.test.tsx）
vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
})

afterEach(() => {
  cleanup()
})
