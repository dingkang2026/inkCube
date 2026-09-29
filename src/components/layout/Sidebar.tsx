import type { ReactNode } from 'react'
import { NavTabs } from './NavTabs'
import { UserBar } from './UserBar'
import styles from './Sidebar.module.css'

interface SidebarProps {
  width: number
  activeNav: string
  onNavChange: (nav: string) => void
  onOpenSettings: () => void
  children: ReactNode
  /** Permanent content pinned between scrollable area and UserBar */
  footer?: ReactNode
  /** Number of projects with wiki compile waiting for review */
  waitingReviewCount?: number
  /** Whether any project has an active wiki compile */
  hasActiveIngest?: boolean
}

export function Sidebar({ width, activeNav, onNavChange, onOpenSettings, children, footer, waitingReviewCount, hasActiveIngest }: SidebarProps) {
  return (
    <aside className={styles.sidebar} style={{ width }}>
      <NavTabs activeNav={activeNav} onNavChange={onNavChange} waitingReviewCount={waitingReviewCount} hasActiveIngest={hasActiveIngest} />
      <div className={styles.content}>
        {children}
      </div>
      {footer}
      <UserBar onOpenSettings={onOpenSettings} />
    </aside>
  )
}
