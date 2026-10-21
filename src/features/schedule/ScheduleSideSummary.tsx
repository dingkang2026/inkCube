/** src/features/schedule/ScheduleSideSummary.tsx — 任务侧栏「定时订阅」摘要行
 *  （2.11 E3 T6）。无订阅 → hintline（点击同样开管理台，新建入口在台内）；
 *  有订阅 → 「订阅 N · 新产出 M · 异常 K」（异常红显）。规模分层不变量：
 *  侧栏只承载此摘要行，绝不展开全量订阅列表（2.11 §8.1 规则 10）。 */
import { Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSchedules } from './useSchedules'
import s from './ScheduleSideSummary.module.css'

export function ScheduleSideSummary({ onOpenAdmin }: { onOpenAdmin: () => void }) {
  const { t } = useTranslation()
  const { totals } = useSchedules()
  if (!totals.n) {
    return (
      <button className={s.hint} onClick={onOpenAdmin}>
        <Clock size={14} />
        <span>{t('schedule.hintCreate')}</span>
      </button>
    )
  }
  return (
    <button className={s.row} onClick={onOpenAdmin}>
      <Clock size={14} />
      <span className={totals.abnormal > 0 ? `${s.text} ${s.abnormal}` : s.text}>
        {t('schedule.summaryLine', {
          n: totals.n,
          unread: totals.total,
          abnormal: totals.abnormal,
        })}
      </span>
    </button>
  )
}
