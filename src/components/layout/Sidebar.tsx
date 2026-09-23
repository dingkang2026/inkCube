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
}

export function Sidebar({ width, activeNav, onNavChange, onOpenSettings, children, footer }: SidebarProps) {
  return (
    <aside className={styles.sidebar} style={{ width }}>
      <NavTabs activeNav={activeNav} onNavChange={onNavChange} />
      <div className={styles.content}>
        {children}
      </div>
      {footer}
      <UserBar onOpenSettings={onOpenSettings} />
    </aside>
  )
}
