/** src/features/home/StageLaneView.tsx — 首页组合泳道（V2-E4 §3.7 新增器官 A2）
 *  投资组合/在谈项目池的盘子总览：按 STAGE_COLUMNS 通用看板列（before/during/after/none）
 *  分列回答「我的盘子现在压在哪个阶段」。与卡片墙是同一数据快照的两种视图
 *  （纯前端派生：projects/nextDueByProject 现有快照重组，零新请求零新状态字段，§6.3 规则 9）；
 *  混型盘子同一列可含招商+投资项目，类型由卡上 chip 区分；空列折叠窄列（阶段语义仍可见）。 */
import { useTranslation } from 'react-i18next'
import type { Project } from '../knowledge/types'
import { STAGE_COLUMNS, PROJECT_TYPE_CONFIG } from '../project/types'
import { daysOverdue, type NextDue } from './ProjectCardWall'
import s from './StageLaneView.module.css'

interface StageLaneViewProps {
  projects: Project[]
  /** 下一事务行（与卡片墙同源：HomePage 从 dueData 派生下发） */
  nextDueByProject?: Record<string, NextDue>
  onSelect: (projectId: string) => void
  /** 盘子全空态引导（§3.7：全空 → 空态引导「新建项目」；HomePage 透传同款回调，缺省仅文案） */
  onCreateProject?: () => void
}

export function StageLaneView({ projects, nextDueByProject, onSelect, onCreateProject }: StageLaneViewProps) {
  const { t } = useTranslation()

  // 按 STAGE_COLUMNS 列序分组；stage 缺省/未知归「其他」列（§3.7 列语义）
  const byStage: Record<string, Project[]> = {}
  for (const col of STAGE_COLUMNS) byStage[col.key] = []
  for (const p of projects) {
    const key = p.stage && byStage[p.stage] ? p.stage : 'none'
    byStage[key].push(p)
  }

  if (projects.length === 0)
    return (
      <div className="empty-block">
        {t('home.noProjects')}
        {/* 盘子全空 → 引导新建项目（§3.7 空态规格；回调缺省时仅文案，与卡片墙空态同款） */}
        {onCreateProject && (
          <button type="button" className={s.emptyCta} onClick={() => onCreateProject()}>
            {t('home.quick.newProject')}
          </button>
        )}
      </div>
    )

  return (
    <div className={s.lanes}>
      {STAGE_COLUMNS.map((col) => {
        const items = byStage[col.key]
        return (
          <div key={col.key} className={`${s.lane} ${items.length === 0 ? s.laneEmpty : ''}`}>
            <div className={s.laneHead}>
              <span className={s.laneTitle}>{t(col.labelKey)}</span>
              <span className={s.laneCount}>{items.length}</span>
            </div>
            <div className={s.laneBody}>
              {items.map((p) => {
                const nextDue = nextDueByProject?.[p.id]
                const overdue = nextDue ? daysOverdue(nextDue.date) : 0
                const typeCfg = PROJECT_TYPE_CONFIG[p.type ?? 'general']
                return (
                  <div key={p.id} className={s.card} onClick={() => onSelect(p.id)}>
                    <div className={s.cardHead}>
                      {typeCfg && <span className={s.typeChip}>{t(typeCfg.labelKey)}</span>}
                      <span className={s.cardName}>{p.name}</span>
                    </div>
                    {/* AI 脉搏句（pulse_ai_status 随 projects 流，空不渲染） */}
                    {p.pulse_ai_status && <div className={s.pulseLine}>{p.pulse_ai_status}</div>}
                    {/* 下一事务/风险点行（与卡片墙同源；逾期红字=风险信号） */}
                    {nextDue && (
                      <div className={s.dueLine}>
                        {t('homeTodo.nextDue')} {nextDue.date.slice(5).replace('-', '/')}
                        {nextDue.title ? ` · ${nextDue.title}` : ''}
                        {overdue > 0 && (
                          <span className={s.dueOverdue}> {t('home.entry.overdue', { count: overdue })}</span>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
