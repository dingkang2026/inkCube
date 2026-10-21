import { useTranslation } from 'react-i18next'
import { Settings } from 'lucide-react'
import { useActivity } from '../../features/activity/ActivityContext'
import styles from './UserBar.module.css'

interface UserBarProps {
  onOpenSettings: () => void
}

export function UserBar({ onOpenSettings }: UserBarProps) {
  const { t } = useTranslation()
  const { summary } = useActivity()

  return (
    <div className={styles.bar}>
      <div className={styles.settingsWrap}>
        <button className={styles.settingsBtn} onClick={() => onOpenSettings()} title={t('userBar.settings')}>
          <Settings size={16} />
        </button>
        {summary.downloading_models > 0 && (
          <span className={styles.downloadDot} title={t('activity.badge.downloading')} />
        )}
      </div>
    </div>
  )
}
