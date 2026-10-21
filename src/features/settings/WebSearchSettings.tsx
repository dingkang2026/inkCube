import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, EyeOff } from 'lucide-react'
import type { LLMSettings } from '../../types'
import styles from './WebSearchSettings.module.css'

interface WebSearchSettingsProps {
  form: LLMSettings
  onUpdate: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

/** 设置 > 基础配置 > 网络搜索（2026-10-19 供应商化）：Tavily / 智谱二选一，
 * 无默认选中——provider 为空即未配置（与 readiness 判定同口径）；
 * 两把 Key 并存，切换供应商不丢已填配置。 */
export function WebSearchSettings({ form, onUpdate }: WebSearchSettingsProps) {
  const { t } = useTranslation()
  const [showKey, setShowKey] = useState(false)
  const provider = form.web_search_provider

  return (
    <section className={styles.section}>
      <h3 className={styles.heading}>{t('settings.webSearch.title')}</h3>
      <p className={styles.hint}>{t('settings.webSearch.description')}</p>

      {/* 供应商单选卡：二选一，无默认（必配语义，空 = 未完成） */}
      <div className={styles.providerGroup} role="radiogroup" aria-label={t('settings.webSearch.providerLabel')}>
        <label className={`${styles.providerCard} ${provider === 'tavily' ? styles.providerActive : ''}`}>
          <input
            type="radio"
            name="web_search_provider"
            value="tavily"
            className={styles.providerRadio}
            checked={provider === 'tavily'}
            onChange={() => onUpdate('web_search_provider', 'tavily')}
          />
          <span className={styles.providerName}>Tavily</span>
          <span className={styles.providerDesc}>{t('settings.webSearch.tavilyDesc')}</span>
        </label>
        <label className={`${styles.providerCard} ${provider === 'zhipu' ? styles.providerActive : ''}`}>
          <input
            type="radio"
            name="web_search_provider"
            value="zhipu"
            className={styles.providerRadio}
            checked={provider === 'zhipu'}
            onChange={() => onUpdate('web_search_provider', 'zhipu')}
          />
          <span className={styles.providerName}>{t('settings.webSearch.zhipuName')}</span>
          <span className={styles.providerDesc}>{t('settings.webSearch.zhipuDesc')}</span>
        </label>
      </div>

      {provider === 'tavily' && (
        <>
          <label className={styles.label} htmlFor="web-search-key-input">
            {t('settings.webSearch.tavilyKey')}
          </label>
          <div className={styles.withAction}>
            <input
              id="web-search-key-input"
              className={styles.input}
              type={showKey ? 'text' : 'password'}
              value={form.tavily_key}
              onChange={(e) => onUpdate('tavily_key', e.target.value)}
              placeholder="tvly-..."
            />
            <button
              className="icon-btn"
              aria-label={showKey ? t('settings.hideKey') : t('settings.showKey')}
              onClick={() => setShowKey(!showKey)}
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <p className={styles.hint}>{t('settings.webSearch.tavilyKeyHint')}</p>
        </>
      )}

      {provider === 'zhipu' && (
        <>
          <label className={styles.label} htmlFor="web-search-key-input">
            {t('settings.webSearch.zhipuKey')}
          </label>
          <div className={styles.withAction}>
            <input
              id="web-search-key-input"
              className={styles.input}
              type={showKey ? 'text' : 'password'}
              value={form.zhipu_search_key}
              onChange={(e) => onUpdate('zhipu_search_key', e.target.value)}
              placeholder={t('settings.webSearch.zhipuKeyPlaceholder')}
            />
            <button
              className="icon-btn"
              aria-label={showKey ? t('settings.hideKey') : t('settings.showKey')}
              onClick={() => setShowKey(!showKey)}
            >
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <p className={styles.hint}>{t('settings.webSearch.zhipuKeyHint')}</p>

          <label className={styles.label} htmlFor="zhipu-engine-select">
            {t('settings.webSearch.engineLabel')}
          </label>
          <select
            id="zhipu-engine-select"
            className={styles.select}
            value={form.zhipu_search_engine || 'search_pro'}
            onChange={(e) => onUpdate('zhipu_search_engine', e.target.value)}
          >
            <option value="search_std">{t('settings.webSearch.engineStd')}</option>
            <option value="search_pro">{t('settings.webSearch.enginePro')}</option>
          </select>
          <p className={styles.hint}>{t('settings.webSearch.engineHint')}</p>
        </>
      )}
    </section>
  )
}
