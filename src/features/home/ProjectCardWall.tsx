/** src/features/home/ProjectCardWall.tsx — 首页项目卡片墙(3.0 §7.1:替代最近项目列表;
 *  每卡=健康色点+项目名+阶段 pill(含 n/m 序数,V2-E4 §3.1⑤)+下一事务(日期·事务名+逾期天数)
 *  +AI 脉搏句行(pulse_ai_status 非空渲染,V2-E4)+最新台账摘要行(3.0 四期 T9,
 *  HomePage 从 ledger:listLatest 快照下发,空不渲染)+矛盾/缺口计数行(reviews_counts 分型,V2-E4)) */
import { useTranslation } from 'react-i18next'
import type { Project } from '../knowledge/types'
import { STAGE_MAP } from '../project/types'
import type { ProjectHealthSummary } from '../project/affairs/types'
import { HealthDot } from '../project/affairs/HealthDot'
import s from './ProjectCardWall.module.css'

/** 卡片墙「下一事务」(HomePage 从 dueData 派生;listDue 已限逾期+7 日内) */
export interface NextDue {
  date: string
  title: string | null
}

/** 最新台账条目(ledger:listLatest 返回行的消费子集,3.0 四期 T9) */
export interface LatestEntry {
  project_id: string
  page_key: string
  entry_date: string | null
  content: string
}

interface ProjectCardWallProps {
  projects: Project[]
  healthData?: Record<string, ProjectHealthSummary>
  nextDueByProject?: Record<string, NextDue>
  latestEntryByProject?: Record<string, LatestEntry>
  /** 24h 内新产出计数(useRecentOutputs 派生,0.5 E2 T7);>0 时卡片右上角角标,点击即原有进项目行为 */
  newOutputsByProject?: Record<string, number>
  onSelect: (projectId: string) => void
}

/** 逾期天数(≥1;当日/未来到期返回 0;本地时区口径同 affairs/types localISODate,禁 toISOString;
 *  导出供 StageLaneView 风险点行复用,V2-E4 §3.7 同源同口径) */
export function daysOverdue(date: string): number {
  const dueMs = new Date(`${date.slice(0, 10)}T00:00:00`).getTime()
  if (Number.isNaN(dueMs)) return 0
  const now = new Date()
  const todayMs = new Date(
    `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}T00:00:00`,
  ).getTime()
  return Math.max(0, Math.round((todayMs - dueMs) / 86400000))
}

export function ProjectCardWall({ projects, healthData, nextDueByProject, latestEntryByProject, newOutputsByProject, onSelect }: ProjectCardWallProps) {
  const { t } = useTranslation()

  const stageInfo = (p: Project): { label: string; ordinal: string | null } | null => {
    if (!p.stage || p.stage === 'none') return null
    const stages = STAGE_MAP[p.type ?? 'general'] ?? []   // 未知 type 脏数据守卫(与 StageLaneView chip 守卫同口径,CodeReview Minor #3)
    const idx = stages.findIndex((x) => x.key === p.stage)
    const key = idx >= 0 ? stages[idx].labelKey : `project.stage.${p.stage}`
    return {
      label: t(key),
      // 阶段进度序数(V2-E4 §3.1⑤):STAGE_MAP 序位/总长;general 无阶段集不显示
      ordinal: idx >= 0 && stages.length > 1 ? `${idx + 1}/${stages.length}` : null,
    }
  }

  if (projects.length === 0) return <div className="empty-block">{t('home.noProjects')}</div>
  return (
    <div className={s.grid}>
      {projects.map((p) => {
        const sum = healthData?.[p.id]
        const nextDue = nextDueByProject?.[p.id]
        const overdue = nextDue ? daysOverdue(nextDue.date) : 0
        const entry = latestEntryByProject?.[p.id]
        const newOutputs = newOutputsByProject?.[p.id] ?? 0
        const stage = stageInfo(p)
        return (
          <div key={p.id} className={s.card} onClick={() => onSelect(p.id)}>
            <div className={s.head}>
              {sum && <HealthDot summary={sum} />}
              <span className={s.name}>{p.name}</span>
              {newOutputs > 0 && (
                <span className={s.newBadge} title={t('home.newOutputs', { count: newOutputs })}>
                  {newOutputs}
                </span>
              )}
            </div>
            <div className={s.meta}>
              {stage && (
                <span className={s.stage}>
                  {stage.label}
                  {stage.ordinal && <span className={s.stageOrdinal}>{stage.ordinal}</span>}
                </span>
              )}
              {nextDue && (
                <span className={s.nextDue}>
                  {t('homeTodo.nextDue')} {nextDue.date.slice(5).replace('-', '/')}
                  {nextDue.title ? ` · ${nextDue.title}` : ''}
                  {overdue > 0 ? ` ${t('home.entry.overdue', { count: overdue })}` : ''}
                </span>
              )}
            </div>
            {/* AI 脉搏句行(V2-E4 §3.1⑤):pulse_ai_status 随 projects 流,空不渲染 */}
            {p.pulse_ai_status && <div className={s.pulseLine}>{p.pulse_ai_status}</div>}
            {/* 矛盾/缺口计数行(V2-E4 §3.1⑤):reviews_counts 分型,>0 才渲染对应项 */}
            {sum && (sum.contradiction > 0 || sum.gap > 0) && (
              <div className={s.reviewLine}>
                {sum.contradiction > 0 && (
                  <span className={s.reviewErr}>{t('home.wall.contradiction', { count: sum.contradiction })}</span>
                )}
                {sum.gap > 0 && (
                  <span className={s.reviewWarn}>{t('home.wall.gap', { count: sum.gap })}</span>
                )}
              </div>
            )}
            {entry && (
              <div className={s.entryLine}>
                <span className={s.entryDate}>{entry.entry_date ?? ''}</span>
                <span className={s.entryContent}>{entry.content}</span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
