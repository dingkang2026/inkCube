import { useTranslation } from 'react-i18next'
import { Home, Wrench, BookOpen } from 'lucide-react'
import styles from './NavTabs.module.css'

interface NavTabsProps {
  activeNav: string
  onNavChange: (nav: string) => void
}

const TABS = [
  { key: 'home', labelKey: 'nav.home', Icon: Home, disabled: false },
  { key: 'tasks', labelKey: 'nav.tasks', Icon: Wrench, disabled: false },
  { key: 'projects', labelKey: 'nav.projects', Icon: BookOpen, disabled: false },
]

export function NavTabs({ activeNav, onNavChange }: NavTabsProps) {
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
        </button>
      ))}
    </nav>
  )
}
