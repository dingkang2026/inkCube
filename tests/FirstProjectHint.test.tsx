import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FirstProjectHint } from '../src/features/project/FirstProjectHint'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

const KEY = 'inkcube.onboarding.firstProject'

describe('FirstProjectHint (0.5 E1 T6)', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('localStorage 无键时渲染提示条与三个动作', () => {
    render(<FirstProjectHint onUploadMaterials={() => {}} onStartTask={() => {}} onAskAssistant={() => {}} />)
    expect(screen.getByText('guide.step3')).toBeTruthy()
    expect(screen.getByText('guide.afterWiki')).toBeTruthy()
    expect(screen.getByText('guide.afterMaterials')).toBeTruthy()
    expect(screen.getByText('guide.afterAsk')).toBeTruthy()
  })

  it('localStorage 已有键时不渲染（一次性）', () => {
    window.localStorage.setItem(KEY, '1')
    render(<FirstProjectHint onUploadMaterials={() => {}} onStartTask={() => {}} onAskAssistant={() => {}} />)
    expect(screen.queryByText('guide.step3')).toBeNull()
  })

  it.each([
    ['guide.afterWiki', 'onUploadMaterials'],
    ['guide.afterMaterials', 'onStartTask'],
    ['guide.afterAsk', 'onAskAssistant'],
  ] as const)('点击 %s → 回调 %s 并写 localStorage 永不再现', (label, cbName) => {
    const callbacks = {
      onUploadMaterials: vi.fn(),
      onStartTask: vi.fn(),
      onAskAssistant: vi.fn(),
    }
    render(<FirstProjectHint {...callbacks} />)
    fireEvent.click(screen.getByText(label))
    expect(callbacks[cbName]).toHaveBeenCalledTimes(1)
    expect(window.localStorage.getItem(KEY)).toBe('1')
    expect(screen.queryByText('guide.step3')).toBeNull()
  })

  it('点击关闭按钮：只写 localStorage，不触发任何动作', () => {
    const callbacks = {
      onUploadMaterials: vi.fn(),
      onStartTask: vi.fn(),
      onAskAssistant: vi.fn(),
    }
    render(<FirstProjectHint {...callbacks} />)
    fireEvent.click(screen.getByRole('button', { name: 'window.close' }))
    expect(callbacks.onUploadMaterials).not.toHaveBeenCalled()
    expect(callbacks.onStartTask).not.toHaveBeenCalled()
    expect(callbacks.onAskAssistant).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(KEY)).toBe('1')
  })
})
