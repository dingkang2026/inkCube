/** tests/ConcurrencySettings.test.tsx — 处理并发设置（0.3 §4.4 项目组 2026-10-19）。
 *  三项并发自 GeneralSettings 迁入；锁定渲染与回写链路（原 GeneralSettings
 *  并发区块删除，此处为唯一承载）。 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/settings/routingFields.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { ConcurrencySettings } from '../src/features/settings/ConcurrencySettings'
import type { LLMSettings } from '../src/types'

afterEach(() => cleanup())

const baseForm = {
  max_concurrent_file_parsing: 2,
  max_concurrent_image_description: 2,
  max_compile_concurrency: 2,
} as LLMSettings

describe('ConcurrencySettings — 三项并发迁入', () => {
  it('renders three number inputs with defaults', () => {
    const { container } = render(<ConcurrencySettings form={baseForm} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.concurrency.title')
    expect(container.textContent).toContain('settings.concurrency.fileParsing')
    expect(container.textContent).toContain('settings.concurrency.imageDescription')
    expect(container.textContent).toContain('settings.concurrency.compile')
    const inputs = container.querySelectorAll('input[type="number"]')
    expect(inputs).toHaveLength(3)
    expect((inputs[0] as HTMLInputElement).value).toBe('2')
    expect((inputs[2] as HTMLInputElement).max).toBe('5')
  })

  it('emits onUpdate with parsed value on change', () => {
    const onUpdate = vi.fn()
    const { container } = render(<ConcurrencySettings form={baseForm} onUpdate={onUpdate} />)
    const inputs = container.querySelectorAll('input[type="number"]')
    fireEvent.change(inputs[0], { target: { value: '4' } })
    expect(onUpdate).toHaveBeenCalledWith('max_concurrent_file_parsing', 4)
    fireEvent.change(inputs[2], { target: { value: '3' } })
    expect(onUpdate).toHaveBeenCalledWith('max_compile_concurrency', 3)
  })
})
