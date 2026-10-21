/** src/features/settings/HealthStatus.tsx — Service health status panel. */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw } from 'lucide-react'
import type { HealthStatus as HealthStatusType } from '../../types/error'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import s from './HealthStatus.module.css'

const SERVICE_LABELS: Record<string, string> = {
  llm: 'LLM',
  vlm: 'VLM',
  embedding: 'Embedding',
  parsing: 'parsing',
}

export function HealthStatus() {
  const { t } = useTranslation()
  const handleError = useErrorHandler()
  const [health, setHealth] = useState<HealthStatusType | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchHealth = useCallback(async () => {
    setLoading(true)
    try {
      const result = await window.electronAPI.invoke('system:getHealth') as HealthStatusType
      setHealth(result)
    } catch (err) {
      // invoke 层异常（多数「Python 未就绪」场景走 electron 侧结构化返回，
      // 不进此 catch）：toast 反馈后仍保留失败态 UI
      handleError(err)
    } finally {
      setLoading(false)
    }
  }, [handleError])

  useEffect(() => {
    fetchHealth()
  }, [fetchHealth])

  if (loading) {
    return (
      <div className={s.container}>
        <div className={s.header}>
          <h3 className={s.title}>{t('health.title')}</h3>
          <span className={s.loading}>{t('health.checking')}</span>
        </div>
      </div>
    )
  }

  if (!health) {
    return (
      <div className={s.container}>
        <div className={s.header}>
          <h3 className={s.title}>{t('health.title')}</h3>
          <span className={s.error}>{t('health.checkFailed')}</span>
        </div>
      </div>
    )
  }

  // Handle Python service down (errorCode from Electron)
  if (health.status === 'down' && health.errorCode === 'PYTHON_SERVICE_DOWN') {
    return (
      <div className={s.container}>
        <div className={s.header}>
          <h3 className={s.title}>{t('health.title')}</h3>
          <span className={s.error}>{t('health.pythonDown')}</span>
        </div>
      </div>
    )
  }

  const services = Object.entries(health.services)

  return (
    <div className={s.container}>
      <div className={s.header}>
        <h3 className={s.title}>{t('health.title')}</h3>
        <div className={s.headerRight}>
          <span className={`${s.overallBadge} ${health.status === 'ok' ? s.ok : s.degraded}`}>
            {health.status === 'ok' ? t('health.allOk') : t('health.degraded')}
          </span>
          <button className={s.refreshBtn} onClick={fetchHealth} title={t('health.refresh')}>
            <RefreshCw size={14} />
          </button>
        </div>
      </div>
      <div className={s.serviceList}>
        {services.map(([key, svc]) => (
          <div key={key} className={s.serviceRow}>
            <span className={`${s.statusDot} ${svc.status === 'ok' ? s.dotOk : s.dotError}`} />
            <span className={s.serviceLabel}>
              {SERVICE_LABELS[key] || key}
            </span>
            <span className={s.serviceMsg}>
              {svc.message || (svc.status === 'ok' ? t('health.ok') : t('health.error'))}
            </span>
            {svc.status === 'error' && svc.suggestion && (
              <button
                className={s.fixBtn}
                onClick={() => window.dispatchEvent(new CustomEvent('app:navigate-settings'))}
              >
                {t('health.goFix')}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
