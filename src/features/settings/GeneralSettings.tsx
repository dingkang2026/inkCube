import { useTranslation } from 'react-i18next'
import type { LLMSettings } from '../../types'
import styles from './GeneralSettings.module.css'

interface GeneralSettingsProps {
  settings?: LLMSettings
  onUpdate?: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

export function GeneralSettings({ settings, onUpdate }: GeneralSettingsProps) {
  const { t, i18n } = useTranslation()

  const handleLanguageChange = (lng: string) => {
    i18n.changeLanguage(lng)
  }

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{t('settings.general')}</h3>

      <label className={styles.label}>{t('settings.language')}</label>
      <select
        className={styles.select}
        value={i18n.language}
        onChange={(e) => handleLanguageChange(e.target.value)}
      >
        <option value="zh-CN">中文</option>
        <option value="en-US">English</option>
      </select>

      {/* Notification (2.11 §6.1)：OS 通知开关即时生效（SettingsPage 保存后广播
          app:settings-changed，ActivityMonitor 重读） */}
      <h3 className={styles.heading}>{t('settings.notification.title')}</h3>
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.osEnabled')}</span>
          <p className={styles.hint}>{t('settings.notification.osEnabledHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.os_notify_enabled ?? true}
            onChange={(e) => onUpdate?.('os_notify_enabled', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.sound')}</span>
          <p className={styles.hint}>{t('settings.notification.soundHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.os_notify_sound ?? true}
            onChange={(e) => onUpdate?.('os_notify_sound', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.affairReminder')}</span>
          <p className={styles.hint}>{t('settings.notification.affairReminderHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.affair_reminder_enabled ?? true}
            onChange={(e) => onUpdate?.('affair_reminder_enabled', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>
      {/* R4 订阅完成通知（review P2-7 提前落地）：默认关闭 = 静默（通知中心
          可见+静默已读）；开启后 completed 实时弹 Toast/OS 通知 */}
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.scheduleNotify')}</span>
          <p className={styles.hint}>{t('settings.notification.scheduleNotifyHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.schedule_notify ?? false}
            onChange={(e) => onUpdate?.('schedule_notify', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>

      {/* 处理并发已移至「项目」组 ConcurrencySettings（0.3 §4.4 五组导航 2026-10-19） */}
    </div>
  )
}
