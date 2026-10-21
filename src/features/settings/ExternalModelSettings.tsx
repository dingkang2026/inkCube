import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, EyeOff } from 'lucide-react'
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

  // L2: validate reasoning_override on input — invalid JSON silently falls back
  // to the effort level on the backend, so surface the error here instead
  const validateOverride = (value: string): string => {
    const trimmed = value.trim()
    if (!trimmed) return ''
    try {
      const parsed: unknown = JSON.parse(trimmed)
      return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
        ? ''
        : t('settings.reasoningOverrideInvalid')
    } catch {
      return t('settings.reasoningOverrideInvalid')
    }
  }
  const [overrideError, setOverrideError] = useState(() => validateOverride(form.reasoning_override ?? ''))

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
          <button className="icon-btn" aria-label={showKey ? t('settings.hideKey') : t('settings.showKey')} onClick={() => setShowKey(!showKey)}>
            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>

        {/* 模型档位槽（2026-10-19 档位制）：两槽互为回退，路由下拉选档 */}
        <label className={styles.label}>{t('settings.standardModel')}</label>
        <input
          className={styles.input}
          value={form.standard_model}
          onChange={(e) => onUpdate('standard_model', e.target.value)}
          placeholder="glm-5.3-flash"
        />
        <p className={styles.hint}>{t('settings.standardModelHint')}</p>

        <label className={styles.label}>{t('settings.highModel')}</label>
        <input
          className={styles.input}
          value={form.high_model}
          onChange={(e) => onUpdate('high_model', e.target.value)}
          placeholder="glm-5.3"
        />
        <p className={styles.hint}>{t('settings.highModelHint')}</p>
        <label className={styles.label} htmlFor="reasoning-effort-input">
          {t('settings.reasoningEffort')}
        </label>
        <input
          id="reasoning-effort-input"
          className={styles.input}
          list="reasoning-effort-options"
          value={form.reasoning_effort ?? ''}
          onChange={(e) => onUpdate('reasoning_effort', e.target.value)}
          placeholder={t('settings.reasoningEffortPlaceholder')}
        />
        <datalist id="reasoning-effort-options">
          <option value="low" />
          <option value="medium" />
          <option value="high" />
          <option value="max" />
        </datalist>
        <p className={styles.hint}>{t('settings.reasoningEffortHint')}</p>

        <details className={styles.advanced}>
          <summary className={styles.advancedTitle}>{t('settings.reasoningOverrideTitle')}</summary>
          <p className={styles.hint}>{t('settings.reasoningOverrideHint')}</p>
          <textarea
            className={`${styles.textarea} ${overrideError ? styles.textareaInvalid : ''}`}
            rows={3}
            value={form.reasoning_override ?? ''}
            onChange={(e) => {
              onUpdate('reasoning_override', e.target.value)
              setOverrideError(validateOverride(e.target.value))
            }}
            placeholder={'{"thinking": {"type": "disabled"}}'}
          />
          {overrideError && <p className={styles.overrideError}>{overrideError}</p>}
        </details>

        {/* 连接超时（0.3 §4.4 归位：自通用移入，模型连接参数随连接配置） */}
        <label className={styles.label}>{t('settings.llm.connectTimeout')}</label>
        <input
          className={styles.input}
          type="number"
          min={5}
          max={120}
          value={form.llm_connect_timeout ?? 30}
          onChange={(e) => onUpdate('llm_connect_timeout', parseInt(e.target.value) || 30)}
        />
        <p className={styles.hint}>{t('settings.llm.connectTimeoutHint')}</p>

        <label className={styles.label}>{t('settings.llm.readTimeout')}</label>
        <input
          className={styles.input}
          type="number"
          min={30}
          max={1800}
          value={form.llm_read_timeout ?? 300}
          onChange={(e) => onUpdate('llm_read_timeout', parseInt(e.target.value) || 300)}
        />
        <p className={styles.hint}>{t('settings.llm.readTimeoutHint')}</p>
      </section>

      {/* 视觉模型：与基础连接同属连接配置，紧跟其后（2026-10-19 规范化重排） */}
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
          <button className="icon-btn" aria-label={showVlmKey ? t('settings.hideKey') : t('settings.showKey')} onClick={() => setShowVlmKey(!showVlmKey)}>
            {showVlmKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>

        <label className={styles.label}>{t('settings.vlmModel')}</label>
        <input
          className={styles.input}
          value={form.vlm_model}
          onChange={(e) => onUpdate('vlm_model', e.target.value)}
          placeholder="glm-4.6v"
        />
      </section>

      {/* 模型路由已拆分至独立「模型路由」section（0.3 §4.4：连接配置与路由分离） */}

      {/* 工具密钥已拆分至独立「网络搜索」section（2026-10-19 供应商化：Tavily / 智谱二选一） */}

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

        <label className={styles.label}>{t('settings.contextWindow')}</label>
        <input
          className={styles.input}
          type="number"
          min="0"
          value={form.context_window}
          onChange={(e) => onUpdate('context_window', parseInt(e.target.value) || 0)}
        />
        <p className={styles.hint}>{t('settings.contextWindowHint')}</p>

        <label className={styles.label}>{t('settings.maxTokens')}</label>
        <input
          className={styles.input}
          type="number"
          min="1"
          max="128000"
          value={form.max_tokens}
          onChange={(e) => onUpdate('max_tokens', parseInt(e.target.value) || 32768)}
        />
        <p className={styles.hint}>{t('settings.maxTokensHint')}</p>
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
