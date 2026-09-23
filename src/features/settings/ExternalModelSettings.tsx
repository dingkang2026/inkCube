import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { LLMSettings } from '../../types'
import styles from './ExternalModelSettings.module.css'

interface ExternalModelSettingsProps {
  form: LLMSettings
  onUpdate: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

export function ExternalModelSettings({ form, onUpdate }: ExternalModelSettingsProps) {
  const { t } = useTranslation()
  const [showKey, setShowKey] = useState(false)
  const [showVlmKey, setShowVlmKey] = useState(false)

  return (
    <>
      <section className={styles.section}>
        <h3 className={styles.heading}>{t('settings.modelConfig')}</h3>

        <label className={styles.label}>{t('settings.apiUrl')}</label>
        <input
          className={styles.input}
          value={form.api_url}
          onChange={(e) => onUpdate('api_url', e.target.value)}
          placeholder="https://api.openai.com/v1"
        />

        <label className={styles.label}>{t('settings.apiKey')}</label>
        <div className={styles.withAction}>
          <input
            className={styles.input}
            type={showKey ? 'text' : 'password'}
            value={form.api_key}
            onChange={(e) => onUpdate('api_key', e.target.value)}
            placeholder="sk-..."
          />
          <button className="icon-btn" onClick={() => setShowKey(!showKey)}>
            {showKey ? '🙈' : '👁'}
          </button>
        </div>

        <label className={styles.label}>{t('settings.modelName')}</label>
        <input
          className={styles.input}
          value={form.model}
          onChange={(e) => onUpdate('model', e.target.value)}
          placeholder="gpt-4o"
        />
      </section>

      <section className={styles.section}>
        <h3 className={styles.heading}>{t('settings.toolConfig')}</h3>

        <label className={styles.label}>{t('settings.tavilyKey')}</label>
        <input
          className={styles.input}
          type="password"
          value={form.tavily_key}
          onChange={(e) => onUpdate('tavily_key', e.target.value)}
          placeholder="tvly-..."
        />
      </section>

      <section className={styles.section}>
        <h3 className={styles.heading}>{t('settings.vlmConfig')}</h3>
        <p className={styles.hint}>{t('settings.vlmDescription')}</p>

        <label className={styles.label}>{t('settings.vlmApiUrl')}</label>
        <input
          className={styles.input}
          value={form.vlm_api_url}
          onChange={(e) => onUpdate('vlm_api_url', e.target.value)}
          placeholder="https://open.bigmodel.cn/api/paas/v4/"
        />

        <label className={styles.label}>{t('settings.vlmApiKey')}</label>
        <div className={styles.withAction}>
          <input
            className={styles.input}
            type={showVlmKey ? 'text' : 'password'}
            value={form.vlm_api_key}
            onChange={(e) => onUpdate('vlm_api_key', e.target.value)}
            placeholder="sk-..."
          />
          <button className="icon-btn" onClick={() => setShowVlmKey(!showVlmKey)}>
            {showVlmKey ? '🙈' : '👁'}
          </button>
        </div>

        <label className={styles.label}>{t('settings.vlmModel')}</label>
        <input
          className={styles.input}
          value={form.vlm_model}
          onChange={(e) => onUpdate('vlm_model', e.target.value)}
          placeholder="glm-4v"
        />
      </section>

      <section className={styles.section}>
        <h3 className={styles.heading}>{t('settings.generationParams')}</h3>

        <label className={styles.label}>
          {t('settings.temperature')}
          <span className={styles.value}>{form.temperature.toFixed(1)}</span>
        </label>
        <input
          type="range"
          className={styles.range}
          min="0"
          max="2"
          step="0.1"
          value={form.temperature}
          onChange={(e) => onUpdate('temperature', parseFloat(e.target.value))}
        />

        <label className={styles.label}>
          {t('settings.topP')}
          <span className={styles.value}>{form.top_p.toFixed(2)}</span>
        </label>
        <input
          type="range"
          className={styles.range}
          min="0"
          max="1"
          step="0.05"
          value={form.top_p}
          onChange={(e) => onUpdate('top_p', parseFloat(e.target.value))}
        />

        <label className={styles.label}>{t('settings.maxTokens')}</label>
        <input
          className={styles.input}
          type="number"
          min="1"
          max="128000"
          value={form.max_tokens}
          onChange={(e) => onUpdate('max_tokens', parseInt(e.target.value) || 4096)}
        />
      </section>

      <section className={styles.section}>
        <h3 className={styles.heading}>{t('settings.systemPrompt')}</h3>
        <textarea
          className={styles.textarea}
          value={form.system_prompt}
          onChange={(e) => onUpdate('system_prompt', e.target.value)}
          placeholder={t('settings.systemPromptPlaceholder')}
          rows={4}
        />
      </section>
    </>
  )
}
