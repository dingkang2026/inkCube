/** src/features/onboarding/OnboardingWizard.tsx — 首次启动引导向导（0.4lifecycle-design.md §7.3 第一层）。
 *
 * 挂载判定由 App.tsx 完成（老用户检测：无 api_key && 无内部模型 && 无项目 && 无任务 && 未写 onboarding_done）。
 * 流程：欢迎 → ① 配置 LLM → ② 下载内部模型 → 完成，三步是连续流程：
 * 「去配置 / 去下载」挂起向导跳设置锚点（onSuspend，不写标记），App 在设置页关闭后
 * 带 initialStep 恢复并推进下一步——保证引导不会在中间步断开；
 * 仅欢迎页「跳过」与完成页「开始使用」两个真终点写 onboarding_done（settings 表 kv），此后不再弹。
 */
import { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { PenLine } from 'lucide-react'
import s from './OnboardingWizard.module.css'

const TOTAL_STEPS = 3 // 1: LLM  2: 内部模型  3: 完成（欢迎页不计步）

interface OnboardingWizardProps {
  onFinish: () => void
  /** 流程内步骤「去配置 / 去下载」（§7.3 ①②）：挂起向导跳设置锚点，不写 onboarding_done；
   *  App 在设置页关闭后以 initialStep 恢复向导并推进下一步（挂起-恢复模型） */
  onSuspend?: (section: 'externalModel' | 'internalModel') => void
  /** 恢复进入的步骤（默认 0 = 欢迎页；App 挂起恢复时传下一步序号） */
  initialStep?: number
}

export function OnboardingWizard({ onFinish, onSuspend, initialStep = 0 }: OnboardingWizardProps) {
  const { t } = useTranslation()
  // step: 0 欢迎页（无步骤指示），1 配置 LLM，2 下载模型，3 完成
  const [step, setStep] = useState(initialStep)
  const [finishing, setFinishing] = useState(false)

  /** 真终点（欢迎页跳过 / 完成页开始使用）：写 onboarding_done（失败不阻断——大不了下次再弹） */
  const finish = useCallback(() => {
    setFinishing(true)
    void window.electronAPI
      .invoke('system:setOnboardingDone')
      .catch(() => undefined)
      .finally(onFinish)
  }, [onFinish])

  const stepDots = (
    <div className={s.stepIndicator}>
      {Array.from({ length: TOTAL_STEPS }, (_, i) => (
        <span key={i} className={`${s.dot} ${step === i + 1 ? s.dotActive : ''}`} />
      ))}
    </div>
  )

  return (
    <div className={s.overlay}>
      <div className={s.wizard} role="dialog" aria-modal="true">
        {step === 0 && (
          <>
            <div className={s.logo}>
              <PenLine size={22} />
            </div>
            <h3 className={s.title}>{t('onboarding.welcomeTitle')}</h3>
            <p className={s.desc}>{t('onboarding.welcomeDesc')}</p>
            <div className={s.actions}>
              <button className={s.skipLink} onClick={finish}>
                {t('onboarding.skip')}
              </button>
              <button className={s.primaryBtn} onClick={() => setStep(1)}>
                {t('onboarding.start')}
              </button>
            </div>
          </>
        )}

        {step === 1 && (
          <>
            {stepDots}
            <h3 className={s.title}>{t('onboarding.llmTitle')}</h3>
            <p className={s.desc}>{t('onboarding.llmDesc')}</p>
            <div className={s.actions}>
              <button className={s.secondaryBtn} onClick={() => setStep(2)}>
                {t('onboarding.skipStep')}
              </button>
              {/* 挂起而非 finish：不写 onboarding_done，App 在设置页关闭后恢复到 ②（§7.3 流程连续性） */}
              <button className={s.primaryBtn} onClick={() => onSuspend?.('externalModel')}>
                {t('onboarding.llmAction')}
              </button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            {stepDots}
            <h3 className={s.title}>{t('onboarding.modelTitle')}</h3>
            <p className={s.desc}>{t('onboarding.modelDesc')}</p>
            <div className={s.actions}>
              <button className={s.secondaryBtn} onClick={() => setStep(3)}>
                {t('onboarding.skipStep')}
              </button>
              {/* 挂起而非 finish：不写 onboarding_done，App 在设置页关闭后恢复到 ③ */}
              <button className={s.primaryBtn} onClick={() => onSuspend?.('internalModel')}>
                {t('onboarding.modelAction')}
              </button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            {stepDots}
            <h3 className={s.title}>{t('onboarding.doneTitle')}</h3>
            <p className={s.desc}>{t('onboarding.doneDesc')}</p>
            <div className={s.actions}>
              <button className={s.primaryBtn} disabled={finishing} onClick={finish}>
                {t('onboarding.doneAction')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
