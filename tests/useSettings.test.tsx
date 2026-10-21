/** tests/useSettings.test.tsx — 设置加载基底合并回归（2026-10-15 设置页 JS 错误事故）
 *
 * 事故：dev 模式后端进程无热重载，database.py defaults 新增的
 * context_window/max_tokens 两键在进程重启前不在 settings:get 响应里；
 * 原实现 `setSettings(res as LLMSettings)` 直接断言 → form 字段 undefined
 * → React 判定输入框非受控，用户首次输入即报
 * "A component is changing an uncontrolled input to be controlled"。
 * 修复：加载响应以 DEFAULT_SETTINGS 为基底合并，缺键回退默认值。
 */
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useSettings } from '../src/features/settings/useSettings'

type ElectronLike = { electronAPI?: { invoke: unknown } }

afterEach(() => {
  vi.restoreAllMocks()
  delete (window as ElectronLike).electronAPI
})

describe('useSettings — 加载响应以 DEFAULT_SETTINGS 为基底合并', () => {
  it('响应缺新增键时回退默认值（旧后端进程时序回归）', async () => {
    // 旧后端进程响应形态：无 max_tokens / context_window
    const invoke = vi.fn().mockResolvedValue({
      api_url: 'https://example.com',
      api_key: 'sk-old',
      model: 'glm-4-flash',
      temperature: 0.7,
      top_p: 0.9,
    })
    ;(window as ElectronLike).electronAPI = { invoke }

    const { result } = renderHook(() => useSettings())
    await waitFor(() => expect(result.current.settingsLoaded).toBe(true))

    expect(result.current.loadFailed).toBe(false)
    expect(result.current.settings.context_window).toBe(131072)
    expect(result.current.settings.max_tokens).toBe(32768)
    // 存量字段原值透传，不被默认值覆盖
    expect(result.current.settings.api_url).toBe('https://example.com')
    expect(result.current.settings.api_key).toBe('sk-old')
  })

  it('响应键全时原值优先，默认值不覆盖存量配置', async () => {
    const invoke = vi.fn().mockResolvedValue({
      context_window: 131072,
      max_tokens: 32768,
    })
    ;(window as ElectronLike).electronAPI = { invoke }

    const { result } = renderHook(() => useSettings())
    await waitFor(() => expect(result.current.settingsLoaded).toBe(true))

    expect(result.current.settings.context_window).toBe(131072)
    expect(result.current.settings.max_tokens).toBe(32768)
  })

  it('加载失败时停留默认基底并置 loadFailed（D8）', async () => {
    const invoke = vi.fn().mockRejectedValue(new Error('backend down'))
    ;(window as ElectronLike).electronAPI = { invoke }

    const { result } = renderHook(() => useSettings())
    await waitFor(() => expect(result.current.settingsLoaded).toBe(true))

    expect(result.current.loadFailed).toBe(true)
    expect(result.current.settings.max_tokens).toBe(32768)
    expect(result.current.settings.context_window).toBe(131072)
  })
})
