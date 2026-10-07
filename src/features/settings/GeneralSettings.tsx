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

      <label className={styles.label}>{t('settings.concurrency.fileParsing')}</label>
      <input
        type="number"
        min={1}
        max={10}
        className={styles.input}
        value={settings?.max_concurrent_file_parsing ?? 2}
        onChange={(e) => onUpdate?.('max_concurrent_file_parsing', parseInt(e.target.value) || 2)}
      />
      <p className={styles.hint}>{t('settings.concurrency.fileParsingHint')}</p>

      <label className={styles.label}>{t('settings.concurrency.imageDescription')}</label>
      <input
        type="number"
        min={1}
        max={10}
        className={styles.input}
        value={settings?.max_concurrent_image_description ?? 2}
        onChange={(e) => onUpdate?.('max_concurrent_image_description', parseInt(e.target.value) || 2)}
      />
      <p className={styles.hint}>{t('settings.concurrency.imageDescriptionHint')}</p>

      <label className={styles.label}>{t('settings.concurrency.compile')}</label>
      <input
        type="number"
        min={1}
        max={5}
        className={styles.input}
        value={settings?.max_compile_concurrency ?? 2}
        onChange={(e) => onUpdate?.('max_compile_concurrency', parseInt(e.target.value) || 2)}
      />
      <p className={styles.hint}>{t('settings.concurrency.compileHint')}</p>

      <label className={styles.label}>{t('settings.llm.connectTimeout')}</label>
      <input
        type="number"
        min={5}
        max={120}
        className={styles.input}
        value={settings?.llm_connect_timeout ?? 30}
        onChange={(e) => onUpdate?.('llm_connect_timeout', parseInt(e.target.value) || 30)}
      />
      <p className={styles.hint}>{t('settings.llm.connectTimeoutHint')}</p>

      <label className={styles.label}>{t('settings.llm.readTimeout')}</label>
      <input
        type="number"
        min={30}
        max={1800}
        className={styles.input}
        value={settings?.llm_read_timeout ?? 300}
        onChange={(e) => onUpdate?.('llm_read_timeout', parseInt(e.target.value) || 300)}
      />
      <p className={styles.hint}>{t('settings.llm.readTimeoutHint')}</p>
    </div>
  )
}
