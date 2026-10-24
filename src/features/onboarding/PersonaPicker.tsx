/**
 * PersonaPicker（0.6 §3.8 V2-E1a）：首启身份选择全屏层。
 * 判定在 App（settingsLoaded 后 !persona_prompted → 显示，新老用户统一）；
 * 选择/跳过均由 App 持久化 persona + persona_prompted=true 后关闭本层。
 * 新用户顺序：Persona → OnboardingWizard → 首页——本层与向导同 z 层，
 * DOM 后序在上（App 渲染顺序保证），选完自然露出向导。
 * 视觉：E1a 克制对齐现状 token，六原则重排在 E1b。
 */
import { useTranslation } from 'react-i18next'
import { Compass, TrendingUp, Layers, Shapes } from 'lucide-react'
import s from './PersonaPicker.module.css'

export type Persona = 'business_attraction' | 'investment' | 'both' | 'other'

interface PersonaPickerProps {
  /** 选中身份卡（App 持久化 persona + prompted 后关闭本层） */
  onSelect: (persona: Persona) => void
  /** 跳过（App 持久化 persona='' + prompted 后关闭本层） */
  onSkip: () => void
}

const CARDS: Array<{ key: Persona; Icon: typeof Compass }> = [
  { key: 'business_attraction', Icon: Compass },
  { key: 'investment', Icon: TrendingUp },
  { key: 'both', Icon: Layers },
  { key: 'other', Icon: Shapes },
]

export function PersonaPicker({ onSelect, onSkip }: PersonaPickerProps) {
  const { t } = useTranslation()
  return (
    <div className={s.overlay} role="dialog" aria-modal="true">
      <div className={s.panel}>
        <h1 className={s.title}>{t('onboarding.persona.title')}</h1>
        <p className={s.subtitle}>{t('onboarding.persona.subtitle')}</p>
        <div className={s.grid}>
          {CARDS.map(({ key, Icon }) => (
            <button
              key={key}
              type="button"
              className={s.card}
              data-testid={`persona-${key}`}
              onClick={() => onSelect(key)}
            >
              <span className={s.icon}><Icon size={28} /></span>
              <span className={s.label}>{t(`onboarding.persona.${key}.label`)}</span>
              <span className={s.desc}>{t(`onboarding.persona.${key}.desc`)}</span>
            </button>
          ))}
        </div>
        <button type="button" className={s.skip} data-testid="persona-skip" onClick={onSkip}>
          {t('onboarding.persona.skip')}
        </button>
      </div>
    </div>
  )
}
