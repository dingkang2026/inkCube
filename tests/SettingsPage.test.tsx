/** tests/SettingsPage.test.tsx — 设置页自动保存回归（0.3 §3.5 v0.8.0）
 *  背景：原「保存配置」按钮在内容区最底部，改字段需滚动保存，操作割裂。
 *  改为自动保存：改动后 800ms 静默期全量落盘；卸载 flush 兜底；保存回灌
 *  跳过自身快照（保存飞行中的新输入不被旧值回滚）。
 *  时序约定（同 tests/usePolling.test.ts）：fake timers + flush 排空 microtask。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/settings/SettingsNav', () => ({ SettingsNav: () => null }))

// GeneralSettings 桩暴露两个触发按钮（默认 section = general），可写入不同值
vi.mock('../src/features/settings/GeneralSettings', () => ({
  GeneralSettings: ({ onUpdate }: { onUpdate?: (key: string, value: unknown) => void }) => (
    <>
      <button data-testid="change-key" onClick={() => onUpdate?.('api_key', 'new-key')} />
      <button data-testid="change-key-2" onClick={() => onUpdate?.('api_key', 'newer-key')} />
    </>
  ),
}))
vi.mock('../src/features/settings/ExternalModelSettings', () => ({ ExternalModelSettings: () => null }))
vi.mock('../src/features/settings/InternalModelSettings', () => ({ InternalModelSettings: () => null }))
vi.mock('../src/features/settings/TaskSettings', () => ({ TaskSettings: () => null }))
vi.mock('../src/features/settings/SkillManagement', () => ({ SkillManagement: () => null }))
vi.mock('../src/features/settings/DataManagement', () => ({ DataManagement: () => null }))
vi.mock('../src/features/settings/SettingsPage.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { SettingsPage } from '../src/features/settings/SettingsPage'
import type { LLMSettings } from '../src/types'

const baseSettings = {
  api_url: 'https://x',
  api_key: 'old',
  model: 'm',
  temperature: 0.7,
  max_tokens: 4096,
} as LLMSettings

let saveCalls: Array<LLMSettings> = []
let rejectSave = false
const invokeMock = vi.fn()

/** 排空 microtask（doSave 为 async，fake timers 不自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  saveCalls = []
  rejectSave = false
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string, payload?: unknown) => {
    if (channel === 'settings:save') {
      saveCalls.push(payload as LLMSettings)
      if (rejectSave) throw new Error('disk full')
      return {}
    }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const onSave = (s: LLMSettings) => invokeMock('settings:save', s)

describe('SettingsPage auto-save', () => {
  it('debounces bursts into one save with the latest form and shows saved state', async () => {
    render(<SettingsPage settings={baseSettings} onSave={onSave} />)
    fireEvent.click(screen.getByTestId('change-key'))
    fireEvent.click(screen.getByTestId('change-key'))

    // 静默期内不落盘
    act(() => { vi.advanceTimersByTime(799) })
    expect(saveCalls).toHaveLength(0)

    // 静默期结束：合并为一次，且值为最新
    act(() => { vi.advanceTimersByTime(1) })
    await flush()
    expect(saveCalls).toHaveLength(1)
    expect(saveCalls[0].api_key).toBe('new-key')
    expect(screen.getByText('settings.autoSave.saved')).toBeTruthy()
  })

  it('flushes pending changes on unmount before the debounce elapses', () => {
    const { unmount } = render(<SettingsPage settings={baseSettings} onSave={onSave} />)
    fireEvent.click(screen.getByTestId('change-key'))
    unmount()
    expect(saveCalls).toHaveLength(1)
    expect(saveCalls[0].api_key).toBe('new-key')
  })

  it('shows failed state when save rejects', async () => {
    rejectSave = true
    render(<SettingsPage settings={baseSettings} onSave={onSave} />)
    fireEvent.click(screen.getByTestId('change-key'))
    act(() => { vi.advanceTimersByTime(800) })
    await flush()
    expect(screen.getByText(/settings\.autoSave\.failed/)).toBeTruthy()
    expect(screen.queryByText('settings.autoSave.saved')).toBeNull()
  })

  it('does not roll back in-flight input when the saved settings prop echoes back', async () => {
    let resolveSave!: () => void
    const slowSave = (s: LLMSettings) => {
      saveCalls.push(s)
      return new Promise<void>((resolve) => { resolveSave = resolve })
    }
    const { rerender } = render(<SettingsPage settings={baseSettings} onSave={slowSave} />)

    // 第一次改动 → 静默期结束 → 保存发起（挂起中）
    fireEvent.click(screen.getByTestId('change-key'))
    act(() => { vi.advanceTimersByTime(800) })
    await flush()
    expect(saveCalls).toHaveLength(1)

    // 保存飞行中用户继续输入
    fireEvent.click(screen.getByTestId('change-key-2'))

    // 保存完成 → 父级 setSettings 回灌（引用 = 保存快照）
    resolveSave()
    await flush()
    rerender(<SettingsPage settings={saveCalls[0]} onSave={slowSave} />)

    // 回灌不得重置 form：第二次改动仍在，静默期后按新值再次保存
    act(() => { vi.advanceTimersByTime(800) })
    await flush()
    expect(saveCalls).toHaveLength(2)
    expect(saveCalls[1].api_key).toBe('newer-key')
  })
})
