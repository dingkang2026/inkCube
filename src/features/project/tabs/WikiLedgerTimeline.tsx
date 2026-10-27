/** src/features/project/tabs/WikiLedgerTimeline.tsx — 台账页时间轴阅读形态（V2 E3 §3.5）
 *
 * is_ledger 页阅读时以条目流替代 Markdown 正文：按 entry_date 分组（后端已倒序、
 * NULL 日期置尾），行 = entry_type 徽标 + 内容。首拉无条目/失败 → onFallback
 * 回落原文渲染（0.6 §3.5）。分页走 usePagedList（20/页「加载更多」，0.1 §16，
 * 禁止静默截断）；Ctrl+F 在时间轴模式不挂载（无 Markdown 块可扫，Reader 侧裁决）。 */
import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { usePagedList } from '../../../hooks/usePagedList'
import type { LedgerEntry } from './WikiChangeLog'
import s from './WikiTab.module.css'

/** entry_type 徽标键（3.1 §4.3 枚举，ledger_service 生成约束）；未知类型回显原值 */
const ENTRY_TYPE_LABEL_KEYS: Record<string, string> = {
  milestone: 'project.wiki.timeline.entryType.milestone',
  risk: 'project.wiki.timeline.entryType.risk',
  policy: 'project.wiki.timeline.entryType.policy',
  finance: 'project.wiki.timeline.entryType.finance',
  service: 'project.wiki.timeline.entryType.service',
}

interface WikiLedgerTimelineProps {
  projectId: string
  pageKey: string
  /** 首拉完成且无条目（或失败）：父级回落原文渲染 */
  onFallback: () => void
}

export function WikiLedgerTimeline({ projectId, pageKey, onFallback }: WikiLedgerTimelineProps) {
  const { t } = useTranslation()
  const {
    items: entries, hasMore, loading, loadingMore, error,
    loadMore,
  } = usePagedList<LedgerEntry>({
    channel: 'wiki:listLedgerEntries',
    itemsKey: 'entries',
    params: { projectId, pageKey },
    pageSize: 20,
  })

  // 首拉终局空集（无条目，或首拉失败——fetchPage 失败不回写 items）→ 回落原文；
  // 不以 error 判定：加载更多失败时 items 已有数据，误回落会静默替换已展示时间轴（CodeReview Major #1）
  const fallbackRef = useRef(false)
  useEffect(() => {
    if (loading || fallbackRef.current) return
    if (entries.length === 0) {
      fallbackRef.current = true
      onFallback()
    }
  }, [loading, entries.length, onFallback])

  // 按 entry_date 归并（后端已倒序，NULL 日期置尾）；组内保持倒序
  const groups = useMemo(() => {
    const map: Record<string, LedgerEntry[]> = {}
    for (const e of entries) {
      const key = e.entry_date ?? '—'
      ;(map[key] ??= []).push(e)
    }
    return Object.entries(map)
  }, [entries])

  // 首拉中不渲染（本地 SQLite 廉价查询，窗口极短）；回落判定交给上方 effect
  if (loading) return null

  return (
    <div className={s.timelineWrap}>
      {groups.map(([date, items]) => (
        <div key={date} className={s.timelineGroup}>
          <div className={s.timelineDate}>{date}</div>
          <div className={s.timelineList}>
            {items.map((e) => (
              <div key={e.id} className={s.ledgerEntryItem}>
                <span className={s.ledgerEntryType}>
                  {ENTRY_TYPE_LABEL_KEYS[e.entry_type]
                    ? t(ENTRY_TYPE_LABEL_KEYS[e.entry_type], { defaultValue: e.entry_type })
                    : e.entry_type}
                </span>
                <span className={s.ledgerEntryContent}>{e.content}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      {hasMore && (
        <button
          className={s.loadMoreBtn}
          onClick={() => void loadMore()}
          disabled={loadingMore}
        >
          {loadingMore ? t('common.loading') : t('common.loadMore')}
        </button>
      )}
      {/* 加载更多失败：内联提示，不回落（已展示条目保留，重试经再点加载更多） */}
      {error && (
        <div className={s.timelineLoadFailed} title={error}>
          {t('project.wiki.timeline.loadFailed', { defaultValue: '加载失败，请重试' })}
        </div>
      )}
    </div>
  )
}
