/** tests/ConcurrencySettings.test.tsx — 处理并发设置（0.3 §4.4 项目组 2026-10-19）。
 *  三项并发自 GeneralSettings 迁入；锁定渲染与回写链路（原 GeneralSettings
 *  并发区块删除，此处为唯一承载）。
 *  2026-10-27 增补：事件驱动自动汇编开关（2.6 §4.1 路径 A）——默认开渲染、
 *  关闭态渲染、点击回写 onUpdate('auto_wiki_compile', checked)。 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/settings/routingFields.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/settings/GeneralSettings.module.css', () => ({
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
  auto_wiki_compile: true,
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

describe('ConcurrencySettings — 自动汇编开关（2.6 §4.1 路径 A）', () => {
  it('renders toggle checked by default with decision-point hint', () => {
    const { container } = render(<ConcurrencySettings form={baseForm} onUpdate={vi.fn()} />)
    // §2.10 免培训：标签 + hint（触发时机/LLM 消耗/停止冷却）必须随开关呈现
    expect(container.textContent).toContain('settings.concurrency.autoCompile')
    expect(container.textContent).toContain('settings.concurrency.autoCompileHint')
    const toggle = container.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(toggle).not.toBeNull()
    expect(toggle.checked).toBe(true)
  })

  it('renders unchecked when switch off', () => {
    const { container } = render(
      <ConcurrencySettings
        form={{ ...baseForm, auto_wiki_compile: false }}
        onUpdate={vi.fn()}
      />,
    )
    const toggle = container.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(toggle.checked).toBe(false)
  })

  it('emits onUpdate with checked state on toggle', () => {
    const onUpdate = vi.fn()
    const { container } = render(
      <ConcurrencySettings
        form={{ ...baseForm, auto_wiki_compile: false }}
        onUpdate={onUpdate}
      />,
    )
    fireEvent.click(container.querySelector('input[type="checkbox"]') as HTMLInputElement)
    expect(onUpdate).toHaveBeenCalledWith('auto_wiki_compile', true)
  })
})
