import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import styles from './TaskLogViewer.module.css'

interface TaskLogViewerProps {
  taskId: string | null
  visible: boolean
  onClose: () => void
}

export function TaskLogViewer({ taskId, visible, onClose }: TaskLogViewerProps) {
  const { t } = useTranslation()
  const [lines, setLines] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [totalLines, setTotalLines] = useState(0)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const fetchLogs = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      const res = await window.electronAPI.invoke('tasks:getLogs', { taskId, lines: 500 }) as {
        lines?: string[]
        total_lines?: number
        error?: string
      }
      if (res?.lines) {
        setLines(res.lines)
        setTotalLines(res.total_lines || 0)
      }
    } catch (err) {
      console.error('Failed to fetch logs:', err)
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    if (visible && taskId) {
      fetchLogs()
    }
  }, [visible, taskId, fetchLogs])

  // Auto-refresh polling
  useEffect(() => {
    if (!autoRefresh || !visible || !taskId) return
    const interval = setInterval(fetchLogs, 3000)
    return () => clearInterval(interval)
  }, [autoRefresh, visible, taskId, fetchLogs])

  // Auto-scroll to bottom
  useEffect(() => {
    if (autoRefresh) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [lines, autoRefresh])

  if (!visible) return null

  return (
    <div className={styles.overlay}>
      <div className={styles.panel}>
        <div className={styles.header}>
          <span className={styles.title}>📋 {t('task.execLogTitle', '执行日志')}</span>
          <span className={styles.meta}>
            {totalLines > 0 && `${totalLines} ${t('task.execLogLines', '行')}`}
          </span>
          <div className={styles.headerActions}>
            <label className={styles.autoRefreshLabel}>
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              {t('task.execLogAutoRefresh', '自动刷新')}
            </label>
            <button className={styles.refreshBtn} onClick={fetchLogs} disabled={loading}>
              {loading ? '⏳' : '🔄'}
            </button>
            <button className={styles.closeBtn} onClick={onClose}>✕</button>
          </div>
        </div>
        <div className={styles.content}>
          {lines.length === 0 ? (
            <div className={styles.empty}>{t('task.execLogEmpty', '暂无执行日志')}</div>
          ) : (
            <pre className={styles.logPre}>
              {lines.map((line, i) => (
                <div key={i} className={styles.logLine}>{line}</div>
              ))}
              <div ref={bottomRef} />
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}
