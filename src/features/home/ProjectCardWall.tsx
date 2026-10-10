/** src/features/home/ProjectCardWall.tsx — 首页项目卡片墙(3.0 §7.1:替代最近项目列表;
 *  每卡=健康色点+项目名+阶段 pill+下一事务日期。台账摘要为四期,不实现) */
import { useTranslation } from 'react-i18next'
import type { Project } from '../knowledge/types'
import { STAGE_MAP } from '../project/types'
import type { ProjectHealthSummary } from '../project/affairs/types'
import { HealthDot } from '../project/affairs/HealthDot'
import s from './ProjectCardWall.module.css'

interface ProjectCardWallProps {
  projects: Project[]
  healthData?: Record<string, ProjectHealthSummary>
  nextDueByProject?: Record<string, string | null>
  onSelect: (projectId: string) => void
}

export function ProjectCardWall({ projects, healthData, nextDueByProject, onSelect }: ProjectCardWallProps) {
  const { t } = useTranslation()

  const stageLabel = (p: Project): string | null => {
    if (!p.stage || p.stage === 'none') return null
    const key = STAGE_MAP[p.type ?? 'general'].find((x) => x.key === p.stage)?.labelKey ?? `project.stage.${p.stage}`
    return t(key)
  }

  if (projects.length === 0) return <div className={s.empty}>{t('home.noProjects')}</div>
  return (
    <div className={s.grid}>
      {projects.map((p) => {
        const sum = healthData?.[p.id]
        const nextDue = nextDueByProject?.[p.id]
        const label = stageLabel(p)
        return (
          <div key={p.id} className={s.card} onClick={() => onSelect(p.id)}>
            <div className={s.head}>
              {sum && <HealthDot summary={sum} />}
              <span className={s.name}>{p.name}</span>
            </div>
            <div className={s.meta}>
              {label && <span className={s.stage}>{label}</span>}
              {nextDue && (
                <span className={s.nextDue}>
                  {t('homeTodo.nextDue')} {nextDue.slice(5).replace('-', '/')}
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
