/** tests/TaskSettings.test.tsx — task_max_iterations 设置可见化回归（1.1 §9.8）
 *  背景：该键原为隐藏配置（task_agent 内联默认 25，不在设置界面），现已
 *  暴露为 TaskSettings 数字输入项；本测试锁定渲染与回写链路。
 */
import { describe, it, expect, vi } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('../src/features/settings/ExternalDepsSettings', () => ({
  ExternalDepsSettings: () => null,
}))
vi.mock('../src/features/settings/TaskSettings.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, fireEvent } from '@testing-library/react'
import { TaskSettings } from '../src/features/settings/TaskSettings'
import type { LLMSettings } from '../src/types'

/** 仅声明被测链路相关键，其余字段组件经 `??` 默认兜底 */
const settings = {
  task_timeout: 30,
  task_max_iterations: 25,
} as LLMSettings

describe('TaskSettings — task_max_iterations 可见化', () => {
  it('渲染迭代上限输入项（label + 默认 25）', () => {
    render(<TaskSettings settings={settings} />)
    expect(screen.getByText('settings.task.maxIterations')).toBeTruthy()
    expect(screen.getByText('settings.task.maxIterationsHint')).toBeTruthy()
    const input = screen.getByDisplayValue('25') as HTMLInputElement
    expect(input.type).toBe('number')
    expect(input.min).toBe('1')
    expect(input.max).toBe('200')
  })

  it('修改后按 task_max_iterations 键回写', () => {
    const onUpdate = vi.fn()
    render(<TaskSettings settings={settings} onUpdate={onUpdate} />)
    const input = screen.getByDisplayValue('25')
    fireEvent.change(input, { target: { value: '60' } })
    expect(onUpdate).toHaveBeenCalledWith('task_max_iterations', 60)
  })

  it('非法输入回退默认 25（parseInt || 25 契约）', () => {
    const onUpdate = vi.fn()
    render(<TaskSettings settings={settings} onUpdate={onUpdate} />)
    const input = screen.getByDisplayValue('25')
    fireEvent.change(input, { target: { value: '' } })
    expect(onUpdate).toHaveBeenCalledWith('task_max_iterations', 25)
  })
})
