import type { LucideIcon } from 'lucide-react'
import styles from './SettingsNav.module.css'

interface NavItem {
  key: string
  label: string
  icon?: LucideIcon
  /** 服务状态点（0.3 §4.4）：missing = 必配缺失（红，与红条同口径）；
   *  判定与文案由调用方经 useHealthServices 计算，本组件纯展示 */
  missing?: boolean
  missingTitle?: string
}

interface NavGroup {
  /** 组标题 i18n 已翻译；空串 = 无标题组（不渲染标题行） */
  label: string
  items: NavItem[]
}

interface SettingsNavProps {
  active: string
  onChange: (section: string) => void
  groups: NavGroup[]
}

/** 设置页左侧导航（0.3 §4.4）：分组标题小字分隔、条目平铺组内 */
export function SettingsNav({ active, onChange, groups }: SettingsNavProps) {
  return (
    <nav className={styles.nav}>
      {groups.map((group, gi) => (
        <div key={group.label || `group-${gi}`} className={styles.group}>
          {group.label && <div className={styles.groupLabel}>{group.label}</div>}
          {group.items.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.key}
                className={`${styles.item} ${active === item.key ? styles.active : ''}`}
                onClick={() => onChange(item.key)}
              >
                {Icon && <Icon size={15} className={styles.icon} />}
                {item.label}
                {item.missing && (
                  <span
                    className={styles.statusDot}
                    role="img"
                    aria-label={item.missingTitle}
                    title={item.missingTitle}
                  />
                )}
              </button>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
