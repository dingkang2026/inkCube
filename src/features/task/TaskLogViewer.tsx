import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { usePolling } from '../../hooks/usePolling'
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
  const [showing, setShowing] = useState(0)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const fetchLogs = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      // 2000 = 后端 lines 参数上限：日志超过上限时 UI 提示截断（避免“看到 N 行”误以为全量）
      const res = await window.electronAPI.invoke('tasks:getLogs', { taskId, lines: 2000 }) as {
        lines?: string[]
        total_lines?: number
        showing?: number
        error?: string
      }
      if (res?.lines) {
        setLines(res.lines)
        setTotalLines(res.total_lines || 0)
        setShowing(res.showing ?? res.lines.length)
      }
    } catch (err) {
      console.error('Failed to fetch logs:', err)
      // rethrow：轮询侧由 usePolling 计入退避（0.4 §5.8）；手动侧 onClick 内 catch
      throw err
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    if (visible && taskId) {
      fetchLogs()
    }
  }, [visible, taskId, fetchLogs])

  // Auto-refresh polling（#10，v1.7 迁移 usePolling，0.4 §8.1）
  usePolling({
    name: 'task-log-viewer',
    enabled: autoRefresh && visible && !!taskId,
    interval: 3000,
    task: fetchLogs,
  })

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
            {totalLines > 0 && (
              showing > 0 && showing < totalLines
                ? t('task.execLogTruncated', { shown: showing, total: totalLines })
                : `${totalLines} ${t('task.execLogLines', '行')}`
            )}
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
            <button className={styles.refreshBtn} onClick={() => void fetchLogs().catch(() => {})} disabled={loading}>
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
