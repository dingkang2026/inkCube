import { useTranslation } from 'react-i18next'
import { Home, Wrench, BookOpen, Radar, Loader2 } from 'lucide-react'
import { useActivity } from '../../features/activity/ActivityContext'
import { useSchedules } from '../../features/schedule/useSchedules'
import styles from './NavTabs.module.css'

interface NavTabsProps {
  activeNav: string
  onNavChange: (nav: string) => void
}

const TABS = [
  { key: 'home', labelKey: 'nav.home', Icon: Home, disabled: false },
  { key: 'tasks', labelKey: 'nav.tasks', Icon: Wrench, disabled: false },
  { key: 'projects', labelKey: 'nav.projects', Icon: BookOpen, disabled: false },
  { key: 'intel', labelKey: 'nav.intel', Icon: Radar, disabled: false },
]

/** L1 徽章（0.4 §5.3）：任务 Tab 运行数徽章 / 项目 Tab 活跃呼吸图标 + 待审阅数字，
 *  数据统一来自 ActivityContext（替代原 Sidebar props 下传）；
 *  情报 Tab 未读徽章（0.6 §2.1 V2-E1a，数据源复用 useSchedules totals——
 *  与 NavTabs 自含拉取 + 终态边沿重拉同模式，零新增轮询）；
 *  通知铃铛已迁 TitleBar 右上角（2.11 §6.1，2026-10-26） */
export function NavTabs({ activeNav, onNavChange }: NavTabsProps) {
  const { t } = useTranslation()
  const { summary, activeProjectIds } = useActivity()
  const { totals } = useSchedules()

  return (
    <nav className={styles.nav}>
      {TABS.map((tab) => (
        <button
          key={tab.key}
          className={`${styles.tab} ${activeNav === tab.key ? styles.active : ''} ${tab.disabled ? styles.disabled : ''}`}
          onClick={() => !tab.disabled && onNavChange(tab.key)}
        >
          <tab.Icon className={styles.icon} size={18} strokeWidth={1.8} />
          <span className={styles.label}>{t(tab.labelKey)}</span>
          {tab.key === 'tasks' && summary.running_tasks > 0 && (
            <span className={styles.taskBadge} title={t('activity.badge.runningTasks')}>
              {summary.running_tasks}
            </span>
          )}
          {tab.key === 'projects' && activeProjectIds.size > 0 && (
            <Loader2 className={styles.ingestSpinner} size={12} />
          )}
          {tab.key === 'intel' && totals.total > 0 && (
            <span className={styles.taskBadge} title={t('schedule.badge.unread')}>
              {totals.total > 99 ? '99+' : totals.total}
            </span>
          )}
        </button>
      ))}
    </nav>
  )
}
