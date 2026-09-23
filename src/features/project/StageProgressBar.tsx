/** src/features/project/StageProgressBar.tsx — Project lifecycle stage progress bar */
import { useTranslation } from 'react-i18next'
import { ChevronRight } from 'lucide-react'
import type { ProjectType, ProjectStage } from './types'
import { STAGE_MAP } from './types'
import s from './StageProgressBar.module.css'

interface StageProgressBarProps {
  projectType: ProjectType
  currentStage: ProjectStage
  onAdvanceStage?: () => void
}

export function StageProgressBar({ projectType, currentStage, onAdvanceStage }: StageProgressBarProps) {
  const { t } = useTranslation()
  const stages = STAGE_MAP[projectType]

  if (stages.length === 0) return null

  const currentIndex = stages.findIndex((s) => s.key === currentStage)
  const canAdvance = currentIndex >= 0 && currentIndex < stages.length - 1

  return (
    <div className={s.bar}>
      <div className={s.stages}>
        {stages.map((stage, index) => {
          const isActive = stage.key === currentStage
          const isCompleted = index < currentIndex
          return (
            <div key={stage.key} className={s.stageItem}>
              {index > 0 && (
                <ChevronRight size={14} className={s.chevron} />
              )}
              <div className={`${s.dot} ${isActive ? s.dotActive : ''} ${isCompleted ? s.dotCompleted : ''}`} />
              <span className={`${s.label} ${isActive ? s.labelActive : ''} ${isCompleted ? s.labelCompleted : ''}`}>
                {t(stage.labelKey)}
              </span>
            </div>
          )
        })}
      </div>
      {canAdvance && onAdvanceStage && (
        <button className={s.advanceBtn} onClick={onAdvanceStage}>
          {t('project.advanceStage')} →
        </button>
      )}
    </div>
  )
}
