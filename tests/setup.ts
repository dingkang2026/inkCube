/** tests/setup.ts — vitest 全局测试装配（vite.config.ts test.setupFiles）
 *  RTL 自动 cleanup 依赖 vitest globals（项目未开启），必须显式注册：
 *  每个用例后卸载组件——否则组件实例跨用例泄漏，其全局事件监听
 *  （如 ActivityMonitor 的 visibilitychange）会跨用例响应并污染
 *  后续用例的 mock 计数（2026-10-05 ActivityMonitor 回归测试实测）。
 */
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})
