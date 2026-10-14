import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import styles from './WebLogins.module.css'
import { useConfirm } from '../../components/ConfirmDialog'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import { apiErrorText } from '../../i18n/errorCodes'
import { browserPanelStore } from '../browser/browserPanelStore'

interface WebLogin {
  domain: string
  note: string | null
  created_at: string
}

/** 设置页「网页登录态」区块（2.8 §8.1）：仅管理入口（查看/重新登录/移除/手动添加），
 *  首登主路径在任务对话流卡片。cookie 的实际清除由 Electron IPC 承担；
 *  重新登录导航到内嵌 BrowserPanel（2.8 V2 §8.3）。 */
export function WebLogins() {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const handleError = useErrorHandler()
  const [logins, setLogins] = useState<WebLogin[]>([])
  const [loading, setLoading] = useState(true)
  const [domain, setDomain] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [adding, setAdding] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('weblogin:list') as { logins: WebLogin[] }
      setLogins(res.logins || [])
    } catch (err) {
      handleError(err)
    } finally {
      setLoading(false)
    }
  }, [handleError])

  useEffect(() => {
    void load()
  }, [load])

  const handleAdd = async () => {
    if (!domain.trim() || adding) return
    setAdding(true)
    setError('')
    try {
      const res = await window.electronAPI.invoke('weblogin:add', {
        domain: domain.trim(),
        note: note.trim() || null,
      }) as { error?: string; code?: string }
      if (res.error) {
        setError(apiErrorText(res.code, res.error, t))
      } else {
        setDomain('')
        setNote('')
        await load()
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setAdding(false)
    }
  }

  // 重新登录 = 导航内嵌 BrowserPanel 到登录页（V2 §8.3），复用任务卡片主路径的分区 cookie
  const handleRelogin = (d: string) => {
    browserPanelStore.navigate(`https://${d}`)
  }

  const handleRemove = async (d: string) => {
    if (!(await confirmDialog({
      title: t('webLogins.remove'),
      message: t('webLogins.confirmRemove', { domain: d }),
    }))) return
    // 链路方向（§9.2）：先清 partition cookie，再删登记行
    try {
      await window.electronAPI.invoke('weblogin:remove-cookies', { domain: d })
    } catch {
      // cookie 清理失败仍删行（下次可重新登记）
    }
    try {
      await window.electronAPI.invoke('weblogin:remove', { domain: d })
      await load()
    } catch (err) {
      handleError(err)
    }
  }

  return (
    <div className={styles.container}>
      <h3 className={styles.heading}>{t('webLogins.title')}</h3>
      <p className={styles.description}>{t('webLogins.description')}</p>
      <p className={styles.hint}>⚠ {t('webLogins.noPasswordHint')}</p>

      <div className={styles.addRow}>
        <input
          type="text"
          className={styles.domainInput}
          placeholder={t('webLogins.domainPlaceholder')}
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void handleAdd()}
        />
        <input
          type="text"
          className={styles.noteInput}
          placeholder={t('webLogins.notePlaceholder')}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void handleAdd()}
        />
        <button className={styles.addBtn} onClick={() => void handleAdd()} disabled={adding || !domain.trim()}>
          {adding ? t('webLogins.adding') : t('webLogins.add')}
        </button>
      </div>
      {error && <div className={styles.error}>{error}</div>}

      {loading ? (
        <div className={styles.empty}>{t('skills.loading')}</div>
      ) : logins.length === 0 ? (
        <div className={styles.empty}>{t('webLogins.empty')}</div>
      ) : (
        <div className={styles.list}>
          {logins.map((login) => (
            <div key={login.domain} className={styles.row}>
              <div className={styles.info}>
                <span className={styles.domain}>{login.domain}</span>
                {login.note && <span className={styles.note}>{login.note}</span>}
              </div>
              <div className={styles.actions}>
                <button className={styles.reloginBtn} onClick={() => handleRelogin(login.domain)}>
                  {t('webLogins.relogin')}
                </button>
                <button
                  className={styles.removeBtn}
                  onClick={() => void handleRemove(login.domain)}
                  title={t('webLogins.remove')}
                >
                  🗑
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
