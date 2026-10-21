/** tests/OnboardingWizard.test.tsx — 首启向导流程连续性回归（0.4 §7.3 第一层）
 *  覆盖：「去配置 / 去下载」挂起（不写 onboarding_done、不触发 onFinish）；
 *  两个真终点（欢迎页跳过 / 完成页开始使用）写标记并结束；initialStep 恢复。
 *  背景 bug：向导内「去配置」曾复用 finish(section)，一步去设置即销毁整个向导，引导流程断裂。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/onboarding/OnboardingWizard.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { OnboardingWizard } from '../src/features/onboarding/OnboardingWizard'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockResolvedValue(undefined)
  ;(window as any).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  // vitest 未开 globals，RTL auto-cleanup 不生效，DOM 会跨用例累积导致多元素匹配
  cleanup()
  delete (window as any).electronAPI
})

function setup(over: { onSuspend?: (s: 'externalModel' | 'webSearch' | 'internalModel') => void; initialStep?: number } = {}) {
  const onFinish = vi.fn()
  render(
    <OnboardingWizard
      onFinish={onFinish}
      onSuspend={over.onSuspend}
      initialStep={over.initialStep}
    />,
  )
  return { onFinish }
}

describe('OnboardingWizard 流程连续性', () => {
  it('欢迎页「跳过」= 真终点：写 onboarding_done 并 onFinish', () => {
    const onSuspend = vi.fn()
    const { onFinish } = setup({ onSuspend })

    fireEvent.click(screen.getByText('onboarding.skip'))
    expect(invokeMock).toHaveBeenCalledWith('system:setOnboardingDone')
    expect(onSuspend).not.toHaveBeenCalled()
    // invoke 已 resolve（异步微任务）后再断言 onFinish
    return vi.waitFor(() => expect(onFinish).toHaveBeenCalledTimes(1))
  })

  it('step1「去配置」= 挂起：onSuspend 被调、不写标记、不结束向导', () => {
    const onSuspend = vi.fn()
    const { onFinish } = setup({ onSuspend })

    fireEvent.click(screen.getByText('onboarding.start'))
    fireEvent.click(screen.getByText('onboarding.llmAction'))

    expect(onSuspend).toHaveBeenCalledWith('externalModel')
    // 挂起不得写标记——否则下次启动不再弹，引导即断
    expect(invokeMock).not.toHaveBeenCalledWith('system:setOnboardingDone')
    expect(onFinish).not.toHaveBeenCalled()
  })

  it('step2「去配置」= 挂起：onSuspend 携带 webSearch（网络搜索步，可跳过）', () => {
    const onSuspend = vi.fn()
    setup({ onSuspend, initialStep: 2 })

    fireEvent.click(screen.getByText('onboarding.webSearchAction'))
    expect(onSuspend).toHaveBeenCalledWith('webSearch')
    expect(invokeMock).not.toHaveBeenCalledWith('system:setOnboardingDone')
  })

  it('step3「去下载」= 挂起：onSuspend 携带 internalModel', () => {
    const onSuspend = vi.fn()
    setup({ onSuspend, initialStep: 3 })

    fireEvent.click(screen.getByText('onboarding.modelAction'))
    expect(onSuspend).toHaveBeenCalledWith('internalModel')
    expect(invokeMock).not.toHaveBeenCalledWith('system:setOnboardingDone')
  })

  it('initialStep 恢复：App 挂起后传 3 → 直接渲染「下载内部模型」步', () => {
    setup({ initialStep: 3 })
    expect(screen.getByText('onboarding.modelTitle')).toBeTruthy()
    // 步骤指示器存在（4 点）
    expect(screen.queryByText('onboarding.welcomeTitle')).toBeNull()
  })

  it('完成页「开始使用」= 真终点：写标记并 onFinish（挂起恢复后的收尾路径）', async () => {
    const { onFinish } = setup({ initialStep: 4 })

    fireEvent.click(screen.getByText('onboarding.doneAction'))
    expect(invokeMock).toHaveBeenCalledWith('system:setOnboardingDone')
    await vi.waitFor(() => expect(onFinish).toHaveBeenCalledTimes(1))
  })
})
