import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Bell } from 'lucide-react'
import { useActivity } from '../../features/activity/ActivityContext'
import { buildMenuDef, type MenuItemDef } from './menuDefinition'
import s from './TitleBar.module.css'

interface TitleBarProps {
  onOpenSettings: () => void
  onToggleSidebar: () => void
  onShowAbout: () => void
  /** 铃铛点击 → 打开通知中心（2.11 §6.1：动作按钮，2026-10-26 自 NavTabs 迁入顶栏） */
  onOpenNotifications: () => void
}

export function TitleBar({ onOpenSettings, onToggleSidebar, onShowAbout, onOpenNotifications }: TitleBarProps) {
  const { t } = useTranslation()
  const { status } = useActivity()
  const unreadCount = status?.notifications.unread.length ?? 0
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const [maximized, setMaximized] = useState(false)
  const barRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const offMax = window.electronAPI.on('window:maximized', () => setMaximized(true))
    const offRestore = window.electronAPI.on('window:unmaximized', () => setMaximized(false))
    return () => { offMax(); offRestore() }
  }, [])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) {
        setOpenIndex(null)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const executeAction = useCallback((action?: string) => {
    if (!action) return
    if (action === 'open-settings') {
      onOpenSettings()
      return
    }
    if (action === 'about') {
      onShowAbout()
      return
    }
    window.electronAPI.send('menu:action', action)
  }, [onOpenSettings, onShowAbout])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const ctrl = e.ctrlKey || e.metaKey
      if (!ctrl) return

      const target = e.target as HTMLElement
      const isEditable =
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable

      switch (e.key.toLowerCase()) {
        case 'q':
          e.preventDefault()
          window.electronAPI.send('menu:action', 'quit')
          break
        case ',':
          e.preventDefault()
          onOpenSettings()
          break
        case '=':
        case '+':
          if (!isEditable) {
            e.preventDefault()
            window.electronAPI.send('menu:action', 'zoomIn')
          }
          break
        case '-':
          if (!isEditable) {
            e.preventDefault()
            window.electronAPI.send('menu:action', 'zoomOut')
          }
          break
        case '0':
          if (!isEditable) {
            e.preventDefault()
            window.electronAPI.send('menu:action', 'zoomReset')
          }
          break
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onOpenSettings])

  const handleMenuClick = (index: number) => {
    setOpenIndex((prev) => (prev === index ? null : index))
  }

  const handleItemClick = (item: MenuItemDef) => {
    executeAction(item.action)
    setOpenIndex(null)
  }

  const handleItemHover = (menuIndex: number) => {
    if (openIndex !== null && openIndex !== menuIndex) {
      setOpenIndex(menuIndex)
    }
  }

  const menus = buildMenuDef(t)

  return (
    <div className={s.titleBar} ref={barRef}>
      <div className={s.brand}>{t('app.title')}</div>
      <div className={s.menus}>
        {menus.map((menu, i) => (
          <div key={menu.label} className={s.menuGroup}>
            <button
              className={`${s.menuTrigger} ${openIndex === i ? s.open : ''}`}
              onClick={() => handleMenuClick(i)}
            >
              {menu.label}
            </button>
            {openIndex === i && (
              <div className={s.dropdown}>
                {menu.children!.map((item, j) =>
                  item.type === 'separator' ? (
                    <div key={j} className={s.separator} />
                  ) : (
                    <button
                      key={item.id}
                      className={s.menuItem}
                      onClick={() => handleItemClick(item)}
                      onMouseEnter={() => handleItemHover(i)}
                    >
                      <span>{item.label}</span>
                      {item.shortcut && <span className={s.shortcut}>{item.shortcut}</span>}
                    </button>
                  ),
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className={s.spacer} />

      {/* 右上角工具区（no-drag）：侧栏开关 + 通知铃铛（2.11 §6.1，未读角标），
          与窗口控制隔开避免误触；铃铛 2026-10-26 自 NavTabs 迁入 */}
      <div className={s.actions}>
        <button className={s.actionBtn} onClick={onToggleSidebar} title={t('nav.toggleSidebar')}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2">
            <rect x="1" y="2" width="12" height="10" rx="1" />
            <line x1="5" y1="2" x2="5" y2="12" />
          </svg>
        </button>
        <button className={`${s.actionBtn} ${s.notifBtn}`} onClick={onOpenNotifications} title={t('nav.notifications')}>
          <Bell size={15} strokeWidth={1.8} />
          {unreadCount > 0 && (
            <span className={s.notifBadge}>{unreadCount > 99 ? '99+' : unreadCount}</span>
          )}
        </button>
      </div>

      <div className={s.windowControls}>
        <button className={s.winBtn} onClick={() => window.electronAPI.invoke('window:minimize')} title={t('window.minimize')}>
          <svg width="10" height="1" viewBox="0 0 10 1"><rect width="10" height="1" fill="currentColor" /></svg>
        </button>
        <button className={s.winBtn} onClick={() => window.electronAPI.invoke('window:maximize')} title={maximized ? t('window.restore') : t('window.maximize')}>
          {maximized ? (
            <svg width="10" height="10" viewBox="0 0 10 10">
              <rect x="2" y="0" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1" />
              <rect x="0" y="2" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1" />
            </svg>
          ) : (
            <svg width="10" height="10" viewBox="0 0 10 10">
              <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1" />
            </svg>
          )}
        </button>
        <button className={`${s.winBtn} ${s.closeBtn}`} onClick={() => window.electronAPI.invoke('window:close')} title={t('window.close')}>
          <svg width="10" height="10" viewBox="0 0 10 10">
            <line x1="0" y1="0" x2="10" y2="10" stroke="currentColor" strokeWidth="1" />
            <line x1="10" y1="0" x2="0" y2="10" stroke="currentColor" strokeWidth="1" />
          </svg>
        </button>
      </div>
    </div>
  )
}
