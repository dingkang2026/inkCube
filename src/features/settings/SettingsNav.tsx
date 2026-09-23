import styles from './SettingsNav.module.css'

interface SettingsNavProps {
  active: string
  onChange: (section: string) => void
  items: { key: string; label: string }[]
}

export function SettingsNav({ active, onChange, items }: SettingsNavProps) {
  return (
    <nav className={styles.nav}>
      {items.map((item) => (
        <button
          key={item.key}
          className={`${styles.item} ${active === item.key ? styles.active : ''}`}
          onClick={() => onChange(item.key)}
        >
          {item.label}
        </button>
      ))}
    </nav>
  )
}
