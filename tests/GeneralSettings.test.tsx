/** tests/GeneralSettings.test.tsx — 常规区身份选择器回归（0.6 §3.8 设置页身份入口，2026-10-25）
 *  覆盖：身份下拉回显当前值 + 五选项（未设置 + 四身份）；改选回调
 *  onUpdate('persona', …)（走设置页自动保存链）；hint 随选中身份切换
 *  （选中消费 onboarding.persona.*.desc，未设置显示通用说明）。
 *  i18n mock 透传 key（SettingsPage.test 同款）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN', changeLanguage: vi.fn() },
  }),
}))

vi.mock('../src/features/settings/GeneralSettings.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { GeneralSettings } from '../src/features/settings/GeneralSettings'
import type { LLMSettings } from '../src/types'

const baseSettings = { persona: 'business_attraction' } as LLMSettings

afterEach(() => cleanup())

describe('GeneralSettings 身份选择器', () => {
  // getByDisplayValue 在本环境对 select 失效（读 attribute 而非 property），
  // 统一经 role=combobox 取元素后按 value 过滤（语言/身份两个 select 共存）
  const identitySelect = () => {
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[]
    return selects.find((x) =>
      Array.from(x.options).some((o) => o.value === 'business_attraction'),
    ) as HTMLSelectElement
  }

  it('回显当前身份且共五个选项（未设置 + 四身份）', () => {
    render(<GeneralSettings settings={baseSettings} onUpdate={vi.fn()} />)
    const select = identitySelect()
    expect(select).toBeTruthy()
    expect(select.value).toBe('business_attraction')
    expect(select.options).toHaveLength(5)
  })

  it('改选身份回调 onUpdate("persona", 新值)', () => {
    const onUpdate = vi.fn()
    render(<GeneralSettings settings={baseSettings} onUpdate={onUpdate} />)
    fireEvent.change(identitySelect(), {
      target: { value: 'investment' },
    })
    expect(onUpdate).toHaveBeenCalledWith('persona', 'investment')
  })

  it('hint 选中态消费当前身份 desc，未设置态显示通用说明', () => {
    const { rerender } = render(<GeneralSettings settings={baseSettings} onUpdate={vi.fn()} />)
    expect(screen.getByText('onboarding.persona.business_attraction.desc')).toBeTruthy()

    rerender(
      <GeneralSettings
        settings={{ ...baseSettings, persona: '' } as LLMSettings}
        onUpdate={vi.fn()}
      />,
    )
    expect(screen.getByText('settings.identityUnsetHint')).toBeTruthy()
    expect(screen.queryByText('onboarding.persona.business_attraction.desc')).toBeNull()
  })

  it('settings 未传入时按未设置渲染（不崩）', () => {
    // 页面存在两个 select（语言/身份），空值场景用 hint 文案断言而非 getByDisplayValue
    render(<GeneralSettings />)
    expect(screen.getByText('settings.identityUnsetHint')).toBeTruthy()
  })

  it('persona 脏值（白名单外）归一为未设置渲染（review S1 回归）', () => {
    // 后端 schemas.py persona 为裸 str 无枚举校验：脏值不得渲染裸 i18n key 或空白 select
    render(
      <GeneralSettings
        settings={{ ...baseSettings, persona: 'bogus' } as LLMSettings}
        onUpdate={vi.fn()}
      />,
    )
    expect(identitySelect().value).toBe('')
    expect(screen.getByText('settings.identityUnsetHint')).toBeTruthy()
  })
})
