import { useTranslation } from 'react-i18next'
import { Home, Wrench, BookOpen, Loader2 } from 'lucide-react'
import styles from './NavTabs.module.css'

interface NavTabsProps {
  activeNav: string
  onNavChange: (nav: string) => void
  waitingReviewCount?: number
  hasActiveIngest?: boolean
}

const TABS = [
  { key: 'home', labelKey: 'nav.home', Icon: Home, disabled: false },
  { key: 'tasks', labelKey: 'nav.tasks', Icon: Wrench, disabled: false },
  { key: 'projects', labelKey: 'nav.projects', Icon: BookOpen, disabled: false },
]

export function NavTabs({ activeNav, onNavChange, waitingReviewCount, hasActiveIngest }: NavTabsProps) {
  const { t } = useTranslation()

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
          {tab.key === 'projects' && hasActiveIngest && (
            <Loader2 className={styles.ingestSpinner} size={12} />
          )}
          {tab.key === 'projects' && (waitingReviewCount ?? 0) > 0 && (
            <span className={styles.reviewBadge} title={t('project.wiki.reviewWaitingNav', { defaultValue: '知识库等待审阅' })}>
              {waitingReviewCount}
            </span>
          )}
        </button>
      ))}
    </nav>
  )
}
