/** tests/OnboardingFinish.test.tsx — 0.5 E2 T8:Onboarding 收尾衔接。
 *  零项目完成向导 → 导航 projects(项目欢迎页 ProjectWelcomeGuide 已引导建第一个项目);
 *  已有项目 → 不切换导航。App 接线见 App.tsx OnboardingWizard onFinish。 */
import { describe, it, expect } from 'vitest'
import { navAfterOnboarding } from '../src/features/onboarding/finishNav'

describe('navAfterOnboarding (0.5 E2 T8)', () => {
  it('零项目 → 导航 projects(欢迎页引导建第一个项目)', () => {
    expect(navAfterOnboarding(0)).toBe('projects')
  })

  it('已有项目 → 不切换(null)', () => {
    expect(navAfterOnboarding(1)).toBeNull()
    expect(navAfterOnboarding(5)).toBeNull()
  })
})
