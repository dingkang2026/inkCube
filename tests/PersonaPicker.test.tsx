/** tests/PersonaPicker.test.tsx — 首启身份选择层单测（V2-E1a T4）
 *
 * 覆盖：四张身份卡 + 跳过链接渲染；点卡回调对应 persona；跳过回调 onSkip。
 * i18n mock 透传 key（AppSidebar.test 同款，t 返回 key 文本即可断言结构）。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PersonaPicker } from '../src/features/onboarding/PersonaPicker'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

describe('PersonaPicker', () => {
  it('渲染四张身份卡与跳过链接', () => {
    render(<PersonaPicker onSelect={vi.fn()} onSkip={vi.fn()} />)
    expect(screen.getByTestId('persona-business_attraction')).toBeTruthy()
    expect(screen.getByTestId('persona-investment')).toBeTruthy()
    expect(screen.getByTestId('persona-both')).toBeTruthy()
    expect(screen.getByTestId('persona-other')).toBeTruthy()
    expect(screen.getByTestId('persona-skip')).toBeTruthy()
  })

  it('点击身份卡回调对应 persona', () => {
    const onSelect = vi.fn()
    render(<PersonaPicker onSelect={onSelect} onSkip={vi.fn()} />)
    fireEvent.click(screen.getByTestId('persona-investment'))
    expect(onSelect).toHaveBeenCalledWith('investment')
  })

  it('点击跳过回调 onSkip', () => {
    const onSkip = vi.fn()
    render(<PersonaPicker onSelect={vi.fn()} onSkip={onSkip} />)
    fireEvent.click(screen.getByTestId('persona-skip'))
    expect(onSkip).toHaveBeenCalledTimes(1)
  })
})
