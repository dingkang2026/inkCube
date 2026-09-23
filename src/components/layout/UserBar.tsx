import { useTranslation } from 'react-i18next'
import styles from './UserBar.module.css'

interface UserBarProps {
  onOpenSettings: () => void
}

export function UserBar({ onOpenSettings }: UserBarProps) {
  const { t } = useTranslation()

  return (
    <div className={styles.bar}>
      <div className={styles.user}>
        <span className={styles.avatar}>K</span>
        <span className={styles.name}>Kelvin</span>
      </div>
      <button className={styles.settingsBtn} onClick={onOpenSettings} title={t('userBar.settings')}>
        ⚙
      </button>
    </div>
  )
}
