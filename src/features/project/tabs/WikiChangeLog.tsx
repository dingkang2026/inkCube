/** src/features/project/tabs/WikiChangeLog.tsx — Project-wide changelog timeline */
import { useState, useEffect, useCallback } from 'react'
import { Clock, Loader2 } from 'lucide-react'
import { ACTION_LABELS } from './wikiUtils'
import s from './WikiTab.module.css'

interface ChangeLogEntry {
  id: string
  action: string
  wiki_page_id: string | null
  summary: string
  source_title: string
  trigger_file_id: string | null
  affected_page_keys: string[]
  created_at: string
}

interface WikiChangeLogProps {
  projectId: string
  onNavigateToPage: (pageKey: string) => void
}

export function WikiChangeLog({ projectId, onNavigateToPage }: WikiChangeLogProps) {
  const [logs, setLogs] = useState<ChangeLogEntry[]>([])
  const [loading, setLoading] = useState(true)

  const loadChangeLog = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('wiki:getChangeLog', {
        projectId, limit: 100,
      })
      setLogs(result.changelog || [])
    } catch (err) {
      console.error('Failed to load changelog:', err)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { loadChangeLog() }, [loadChangeLog])

  if (loading) {
    return (
      <div className={s.changelogPage}>
        <div className={s.changelogHeader}>
          <h3>变更日志</h3>
        </div>
        <div className={s.loadingInline}><Loader2 size={20} className={s.spin} /></div>
      </div>
    )
  }

  // Group logs by date
  const grouped = new Map<string, ChangeLogEntry[]>()
  for (const log of logs) {
    const date = new Date(log.created_at).toLocaleDateString('zh-CN', {
      year: 'numeric', month: 'long', day: 'numeric',
    })
    if (!grouped.has(date)) grouped.set(date, [])
    grouped.get(date)!.push(log)
  }

  return (
    <div className={s.changelogPage}>
      <div className={s.changelogHeader}>
        <div className={s.changelogTitleRow}>
          <Clock size={15} className={s.changelogIcon} />
          <h3>变更日志</h3>
        </div>
      </div>

      <div className={s.changelogBody}>
        {logs.length === 0 ? (
          <p className={s.homePlaceholder}>暂无变更记录</p>
        ) : (
          Array.from(grouped.entries()).map(([date, entries]) => (
            <div key={date} className={s.changelogDay}>
              <div className={s.changelogDate}>{date}</div>
              {entries.map((log) => (
                <div key={log.id} className={s.changelogEntry}>
                  <div className={s.changelogEntryHeader}>
                    <span className={s.changelogAction}>{ACTION_LABELS[log.action] || log.action}</span>
                    <span className={s.changelogTime}>
                      {new Date(log.created_at).toLocaleTimeString('zh-CN', {
                        hour: '2-digit', minute: '2-digit',
                      })}
                    </span>
                  </div>
                  {log.summary && <div className={s.changelogSummary}>{log.summary}</div>}
                  {log.source_title && (
                    <div className={s.changelogSource}>来源: {log.source_title}</div>
                  )}
                  {log.affected_page_keys?.length > 0 && (
                    <div className={s.changelogAffected}>
                      影响页面:{' '}
                      {log.affected_page_keys.map((key: string) => (
                        <span
                          key={key}
                          className={s.refTag}
                          onClick={() => onNavigateToPage(key)}
                        >
                          {key}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
