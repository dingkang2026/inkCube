/** src/features/project/tabs/WikiChangeLog.tsx — Project-wide changelog timeline */
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, Loader2 } from 'lucide-react'
import { ACTION_LABEL_KEYS } from './wikiUtils'
import { usePagedList } from '../../../hooks/usePagedList'
import { useToast } from '../../knowledge/Toast'
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
  const { t, i18n } = useTranslation()
  const showToast = useToast()
  // 变更日志分页（0.1dev-guide 统一约定）：原 limit=100 一次性拉取改 50/页「加载更多」
  const {
    items: logs,
    hasMore,
    loadingMore,
    loading,
    error: logsError,
    loadMore,
  } = usePagedList<ChangeLogEntry>({
    channel: 'wiki:getChangeLog', itemsKey: 'changelog', params: { projectId },
  })
  // 列表加载失败提示（对齐 FormsTab m-3：静默吞掉会让列表空白且无任何提示）
  useEffect(() => {
    if (logsError) showToast(`${t('common.listLoadFailed')}: ${logsError}`, 'error')
  }, [logsError, showToast, t])

  if (loading) {
    return (
      <div className={s.changelogPage}>
        <div className={s.changelogHeader}>
          <h3>{t('project.wiki.changeLog', { defaultValue: '变更日志' })}</h3>
        </div>
        <div className={s.loadingInline}><Loader2 size={20} className={s.spin} /></div>
      </div>
    )
  }

  // Group logs by date（日期格式跟随当前语言）
  const grouped = new Map<string, ChangeLogEntry[]>()
  for (const log of logs) {
    const date = new Date(log.created_at).toLocaleDateString(i18n.language, {
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
          <h3>{t('project.wiki.changeLog', { defaultValue: '变更日志' })}</h3>
        </div>
      </div>

      <div className={s.changelogBody}>
        {logs.length === 0 ? (
          <p className={s.homePlaceholder}>{t('project.wiki.noChangeLog', { defaultValue: '暂无变更记录' })}</p>
        ) : (
          Array.from(grouped.entries()).map(([date, entries]) => (
            <div key={date} className={s.changelogDay}>
              <div className={s.changelogDate}>{date}</div>
              {entries.map((log) => (
                <div key={log.id} className={s.changelogEntry}>
                  <div className={s.changelogEntryHeader}>
                    <span className={s.changelogAction}>
                      {ACTION_LABEL_KEYS[log.action]
                        ? t(ACTION_LABEL_KEYS[log.action], { defaultValue: log.action })
                        : log.action}
                    </span>
                    <span className={s.changelogTime}>
                      {new Date(log.created_at).toLocaleTimeString(i18n.language, {
                        hour: '2-digit', minute: '2-digit',
                      })}
                    </span>
                  </div>
                  {log.summary && <div className={s.changelogSummary}>{log.summary}</div>}
                  {log.source_title && (
                    <div className={s.changelogSource}>
                      {t('project.wiki.changelogSource', { defaultValue: '来源' })}: {log.source_title}
                    </div>
                  )}
                  {log.affected_page_keys?.length > 0 && (
                    <div className={s.changelogAffected}>
                      {t('project.wiki.changelogAffected', { defaultValue: '影响页面' })}
                      {' '}
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
        {hasMore && (
          <button
            className={s.loadMoreBtn}
            onClick={() => { void loadMore() }}
            disabled={loadingMore}
          >
            {loadingMore ? t('common.loading') : t('common.loadMore')}
          </button>
        )}
      </div>
    </div>
  )
}
