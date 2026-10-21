/** src/features/usage/ProjectUsageBadge.tsx — 项目详情页头部用量徽章（2.10 §6.1/线框 5）。
 *  静态看累计规模（↑in ↓out）；hover 出 by_source 分拆悬浮层（同一次请求，零额外
 *  请求；不含任务——任务不归属项目，§6.1 维度口径不变量）；点击跳设置-用量统计
 *  并自动筛选该项目。数据走 useUsageSummary('all', projectId)（L2 事件驱动刷新）。 */
import { useTranslation } from 'react-i18next'
import { useUsageSummary } from './useUsageSummary'
import { compactTokens, USAGE_SOURCE_LABEL_KEYS } from './format'
import s from './ProjectUsageBadge.module.css'

export function ProjectUsageBadge({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const { data } = useUsageSummary('all', projectId)
  if (!data) return null

  const openStats = () => {
    window.dispatchEvent(new CustomEvent('app:navigate-settings', {
      detail: { section: 'usage', projectId },
    }))
  }

  const sources = data.by_source ?? []
  const total = data.total
  const today = data.today

  return (
    <span className={s.badgeWrap}>
      <button
        className={s.badge}
        onClick={openStats}
      >
        ↑{compactTokens(total?.prompt_tokens ?? 0)} ↓{compactTokens(total?.completion_tokens ?? 0)}
      </button>
      <div className={s.popover}>
        <div className={s.popHead}>
          {t('usage.today')} ↑{compactTokens(today?.prompt_tokens ?? 0)} ↓{compactTokens(today?.completion_tokens ?? 0)}
          {' · '}
          {t('usage.badgeTotal')} ↑{compactTokens(total?.prompt_tokens ?? 0)} ↓{compactTokens(total?.completion_tokens ?? 0)}
        </div>
        {sources.length === 0 ? (
          <div className={s.popEmpty}>{t('usage.empty')}</div>
        ) : (
          sources.map((row) => (
            <div key={row.grp} className={s.popRow}>
              <span className={s.rowLabel}>{t(USAGE_SOURCE_LABEL_KEYS[row.grp] ?? 'usage.srcOther')}</span>
              <span>
                ↑{compactTokens(row.prompt_tokens ?? 0)} ↓{compactTokens(row.completion_tokens ?? 0)}
                {' · '}
                {t('usage.titleCalls', { count: row.calls ?? 0 })}
              </span>
            </div>
          ))
        )}
      </div>
    </span>
  )
}
