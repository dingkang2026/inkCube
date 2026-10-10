/** src/features/project/affairs/HealthDot.tsx — 项目健康色点(3.0 §7.2)
 *  口径:红=有逾期;橙=本周待办或待处理发现项;绿=两无。props 传 summary,
 *  组件内部算档位与 title 文案,消费方(侧栏/卡片墙)零重复。 */
import { useTranslation } from 'react-i18next'
import { healthLevel, type ProjectHealthSummary } from './types'
import s from './HealthDot.module.css'

export function HealthDot({ summary, className }: { summary: ProjectHealthSummary; className?: string }) {
  const { t } = useTranslation()
  const level = healthLevel(summary)
  const title = summary.overdue > 0
    ? t('homeTodo.healthRed', { count: summary.overdue })
    : summary.weekDue > 0 || summary.reviewsPending > 0
      ? t('homeTodo.healthOrange', { count: summary.weekDue, reviews: summary.reviewsPending })
      : t('homeTodo.healthGreen')
  return <span className={`${s.dot} ${s[level]}${className ? ` ${className}` : ''}`} title={title} aria-label={title} />
}
