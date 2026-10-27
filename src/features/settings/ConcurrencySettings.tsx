import { useTranslation } from 'react-i18next'
import type { LLMSettings } from '../../types'
import styles from './routingFields.module.css'
// toggle 行样式复用 GeneralSettings 基座（toggleRow/toggle/toggleSlider 单源，不复制 CSS）
import toggleStyles from './GeneralSettings.module.css'

interface ConcurrencySettingsProps {
  form: LLMSettings
  onUpdate: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

/** 处理并发（0.3 §4.4 项目组，自通用迁入）：三项均为知识库链路的全局资源调节 */
export function ConcurrencySettings({ form, onUpdate }: ConcurrencySettingsProps) {
  const { t } = useTranslation()
  return (
    <section className={styles.section}>
      <h3 className={styles.heading}>{t('settings.concurrency.title')}</h3>
      <div className={styles.fieldGrid}>
        <div className={styles.field}>
          <label className={styles.label}>{t('settings.concurrency.fileParsing')}</label>
          <input
            type="number"
            min={1}
            max={10}
            className={styles.input}
            value={form.max_concurrent_file_parsing ?? 2}
            onChange={(e) => onUpdate('max_concurrent_file_parsing', parseInt(e.target.value) || 2)}
          />
          <p className={styles.hint}>{t('settings.concurrency.fileParsingHint')}</p>
        </div>

        <div className={styles.field}>
          <label className={styles.label}>{t('settings.concurrency.imageDescription')}</label>
          <input
            type="number"
            min={1}
            max={10}
            className={styles.input}
            value={form.max_concurrent_image_description ?? 2}
            onChange={(e) => onUpdate('max_concurrent_image_description', parseInt(e.target.value) || 2)}
          />
          <p className={styles.hint}>{t('settings.concurrency.imageDescriptionHint')}</p>
        </div>

        <div className={styles.field}>
          <label className={styles.label}>{t('settings.concurrency.compile')}</label>
          <input
            type="number"
            min={1}
            max={5}
            className={styles.input}
            value={form.max_compile_concurrency ?? 2}
            onChange={(e) => onUpdate('max_compile_concurrency', parseInt(e.target.value) || 2)}
          />
          <p className={styles.hint}>{t('settings.concurrency.compileHint')}</p>
        </div>
      </div>

      {/* 事件驱动自动汇编（2.6 §4.1 路径 A）：解析完成即自动点火；hint 承担
          决策点解释（触发时机/LLM 消耗/停止冷却，§2.10 免培训） */}
      <div className={toggleStyles.toggleRow}>
        <div className={toggleStyles.toggleLabel}>
          <span className={styles.label}>{t('settings.concurrency.autoCompile')}</span>
          <p className={toggleStyles.hint}>{t('settings.concurrency.autoCompileHint')}</p>
        </div>
        <label className={toggleStyles.toggle}>
          <input
            type="checkbox"
            checked={form.auto_wiki_compile ?? true}
            onChange={(e) => onUpdate('auto_wiki_compile', e.target.checked)}
          />
          <span className={toggleStyles.toggleSlider}></span>
        </label>
      </div>
    </section>
  )
}
