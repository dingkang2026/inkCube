/** src/features/usage/StatusBar.tsx — App 级常驻底栏（2.10 §6.2 分段规格/线框 1）。
 *  纯全局分段：今日 / 总计 / 活动数 / 模型数 / 版本。用量走 useUsageSummary（L2
 *  事件驱动刷新）；活动数直连 useActivity() 既有心跳（零新增轮询）。挂载于 App
 *  根 flex（.app-layout 之后），全屏/沉浸模式下保持可见（底栏属于应用 chrome）。 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useActivity } from '../activity/ActivityContext'
import { useUsageSummary } from './useUsageSummary'
import { compactTokens, thousands } from './format'
import s from './StatusBar.module.css'

interface StatusBarProps {
  onOpenHome: () => void
  onOpenStats: () => void
  onShowAbout: () => void
}

export function StatusBar({ onOpenHome, onOpenStats, onShowAbout }: StatusBarProps) {
  const { t } = useTranslation()
  const { data } = useUsageSummary('all')
  const { summary } = useActivity()
  const [version, setVersion] = useState('')

  useEffect(() => {
    window.electronAPI.invoke('app:version')
      .then((v) => setVersion(String(v ?? '')))
      .catch(() => {})
  }, [])

  const activeCount =
    summary.active_files +
    summary.active_compiles +
    summary.generating_reports +
    summary.running_tasks +
    summary.downloading_models +
    summary.analyzing_form_templates +
    summary.filling_forms +
    summary.active_ppts

  const today = data?.today
  const total = data?.total

  return (
    <footer className={s.bar}>
      <button
        className={s.seg}
        onClick={onOpenStats}
        title={`${t('usage.titleInput')}: ${thousands(today?.prompt_tokens ?? 0)} · ${t('usage.titleOutput')}: ${thousands(today?.completion_tokens ?? 0)}`}
      >
        {t('usage.today')} ↑{compactTokens(today?.prompt_tokens ?? 0)} ↓{compactTokens(today?.completion_tokens ?? 0)}
      </button>
      <button
        className={s.seg}
        onClick={onOpenStats}
        title={`${t('usage.titleTotal')}: ${thousands(total?.total_tokens ?? 0)} tokens · ${t('usage.titleCalls', { count: total?.calls ?? 0 })}`}
      >
        {t('usage.total')} {compactTokens(total?.total_tokens ?? 0)} tokens · {t('usage.titleCalls', { count: total?.calls ?? 0 })}
      </button>
      <button className={s.seg} onClick={onOpenHome} title={t('usage.tipActivities')}>
        {activeCount} {t('usage.activeUnit')}
      </button>
      <button className={s.seg} onClick={onOpenStats} title={t('usage.tipModels')}>
        {total?.models_count ?? 0} {t('usage.modelsUnit')}
      </button>
      <span className={s.version} onClick={onShowAbout} title={t('menu.about')}>
        {version ? `v${version}` : ''}
      </span>
    </footer>
  )
}
