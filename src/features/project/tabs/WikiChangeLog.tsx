/** src/features/project/tabs/WikiChangeLog.tsx — 时间线双栏（2026-10-26 IA 收敛）：左=变更日志
 *  （wiki_change_log 系统事件），右=台账动态（wiki_ledger_entries 业务条目，原首页「台账时间轴」
 *  卡迁入，终结「台账时间线/时间线」撞名；两栏数据源不同，分栏即语义） */
import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, Activity, Loader2 } from 'lucide-react'
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

/** 台账条目（wiki:listLedgerEntries 返回行，3.0 四期 T7）。类型定义迁自 WikiHomePage——
 *  封面台账卡删除（2026-10-26）后，本组件右栏是全项目维度台账条目的唯一消费方 */
export interface LedgerEntry {
  id: string
  page_key: string
  entry_date: string | null
  entry_type: string
  content: string
}

/** 页面标题反查用轻量结构（WikiTab 全量 WikiPage 结构兼容，TS 结构化子类型） */
interface PageTitleRef {
  page_key: string
  title: string
}

interface WikiChangeLogProps {
  projectId: string
  onNavigateToPage: (pageKey: string) => void
  /** 台账条目（父级 wiki:listLedgerEntries usePagedList 拉取）；空/undefined 显示空态 */
  ledgerEntries?: LedgerEntry[]
  /** 台账解析进行中（心跳 ledger_parse 含本项目）：右栏头部灰字提示，旧条目照常可读 */
  ledgerParsing?: boolean
  /** 台账条目分页（20/页「加载更多」，§列表分页统一约定） */
  hasMoreLedger?: boolean
  ledgerLoadingMore?: boolean
  onLoadMoreLedger?: () => void
  /** 页面清单（台账行标题反查：显示页标题而非 page_key） */
  pages?: PageTitleRef[]
}

export function WikiChangeLog({
  projectId, onNavigateToPage, ledgerEntries, ledgerParsing,
  hasMoreLedger, ledgerLoadingMore, onLoadMoreLedger, pages,
}: WikiChangeLogProps) {
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

  // Group logs by date（日期格式跟随当前语言）
  const grouped = new Map<string, ChangeLogEntry[]>()
  for (const log of logs) {
    const date = new Date(log.created_at).toLocaleDateString(i18n.language, {
      year: 'numeric', month: 'long', day: 'numeric',
    })
    if (!grouped.has(date)) grouped.set(date, [])
    grouped.get(date)!.push(log)
  }

  // 台账条目按 entry_date 归并（后端已倒序，NULL 组自然置尾；渲染逻辑迁自首页台账卡）
  const ledgerGroups = useMemo(() => {
    if (!ledgerEntries || ledgerEntries.length === 0) return null
    const groups: Record<string, LedgerEntry[]> = {}
    for (const e of ledgerEntries) {
      const key = e.entry_date ?? '—'
      ;(groups[key] ??= []).push(e)
    }
    return Object.entries(groups)
  }, [ledgerEntries])

  /** 台账行标题：按 page_key 反查页标题，查不到显示 key 本身 */
  const ledgerPageTitle = (key: string) => pages?.find((p) => p.page_key === key)?.title || key

  return (
    <div className={s.changelogPage}>
      <div className={s.changelogColumns}>
        {/* 左栏：系统变更日志（ingest/编辑/归档/链接补全/去重合并…） */}
        <div className={s.changelogCol}>
          <div className={s.changelogColHead}>
            <Clock size={14} className={s.changelogColIcon} />
            <span>{t('project.wiki.changeLog', { defaultValue: '变更日志' })}</span>
          </div>
          {loading ? (
            <div className={s.loadingInline}><Loader2 size={20} className={s.spin} /></div>
          ) : logs.length === 0 ? (
            <p className={s.homePlaceholder}>{t('project.wiki.noChangeLog', { defaultValue: '暂无变更记录' })}</p>
          ) : (
            <>
              {Array.from(grouped.entries()).map(([date, entries]) => (
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
              ))}
              {hasMore && (
                <button
                  className={s.loadMoreBtn}
                  onClick={() => { void loadMore() }}
                  disabled={loadingMore}
                >
                  {loadingMore ? t('common.loading') : t('common.loadMore')}
                </button>
              )}
            </>
          )}
        </div>

        {/* 右栏：台账动态（监控页条目流，行点击直达台账页 Reader 时间轴） */}
        <div className={s.ledgerFeedCol}>
          <div className={s.changelogColHead}>
            <Activity size={14} className={s.changelogColIcon} />
            <span>{t('project.wiki.timeline.ledgerFeed', { defaultValue: '台账动态' })}</span>
            {ledgerParsing && (
              <span className={s.ledgerFeedParsing}>{t('project.wiki.timeline.parsing')}</span>
            )}
          </div>
          {!ledgerGroups ? (
            <p className={s.homePlaceholder}>
              {t('project.wiki.timeline.ledgerFeedEmpty', { defaultValue: '汇编后自动生成台账条目' })}
            </p>
          ) : (
            <>
              <div className={s.timelineWrap}>
                {ledgerGroups.map(([date, items]) => (
                  <div key={date} className={s.timelineGroup}>
                    <div className={s.timelineDate}>{date}</div>
                    <div className={s.timelineList}>
                      {items.map((e) => (
                        <button key={e.id} className={s.timelineItem}
                          onClick={() => onNavigateToPage(e.page_key)}>
                          <span className={s.timelinePageTitle}>{ledgerPageTitle(e.page_key)}</span>
                          <span className={s.timelineContent}>{e.content}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              {hasMoreLedger && (
                <button
                  className={s.loadMoreBtn}
                  onClick={onLoadMoreLedger}
                  disabled={ledgerLoadingMore}
                >
                  {ledgerLoadingMore ? t('common.loading') : t('common.loadMore')}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
