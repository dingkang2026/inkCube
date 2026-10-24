/**
 * IntelFeed（0.6 §3.2 V2-E1a）：情报中心情报流卡片流。
 * 数据源 = GET /schedules/runs/inbox（done；sort=unread 未读前置/latest 纯时间倒序；
 * category 订阅分类直读过滤；limit/offset 分页契约 has_more/total）。
 * chips 五枚：全部/企业/产业/区域/通用；排序切换未读优先⇄最新（§6.3-3 排序
 * 独立于类别筛选）。卡片 = 摘要首行 + 订阅名 + 类别 chip + 相对时间 + 未读点；
 * 点击 → onOpenRead（ReadView）。
 * 边沿重拉：订阅期次终态（'schedule' 活动源——影子任务终态注册在 schedule 源
 * 而非 task 源，同 useSchedules 模式，零新增轮询）；
 * 零订阅空态引导建订阅（onOpenAdmin → 订阅管理 tab）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Inbox, Plus, Sparkles } from 'lucide-react'
import { useActivity } from '../activity/ActivityContext'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { useSchedules, type ScheduleRunRow } from '../schedule/useSchedules'
import s from './IntelFeed.module.css'

const PAGE_SIZE = 20
/** chips 值域：'' = 全部；四类 = 订阅分类直读（0.6 §3.2） */
const CATEGORIES: Array<{ value: string; labelKey: string }> = [
  { value: '', labelKey: 'intel.cat.all' },
  { value: 'corporate', labelKey: 'intel.cat.corporate' },
  { value: 'industry', labelKey: 'intel.cat.industry' },
  { value: 'region', labelKey: 'intel.cat.region' },
  { value: 'general', labelKey: 'intel.cat.general' },
]

interface IntelFeedProps {
  /** 卡片点击 → 期次阅读页（App 切 scheduleView read 态）；
   *  action='extract' = 产业卡快捷钮直达阅读页并自动展开提取动线（V2-E2） */
  onOpenRead: (run: ScheduleRunRow, action?: 'extract') => void
  /** 零订阅空态「新建订阅」/ running 行点击 → 订阅管理 tab */
  onOpenAdmin: () => void
}

export function IntelFeed({ onOpenRead, onOpenAdmin }: IntelFeedProps) {
  const { t } = useTranslation()
  const { totals } = useSchedules()
  const [rows, setRows] = useState<ScheduleRunRow[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [sort, setSort] = useState<'unread' | 'latest'>('unread')
  const [category, setCategory] = useState('')
  // 请求序号：筛选/排序切换后晚到的旧「加载更多」响应不得污染新列表
  const loadSeq = useRef(0)

  const load = useCallback(async (offset: number, append: boolean) => {
    const seq = ++loadSeq.current
    setLoading(true)
    try {
      const res = await window.electronAPI.invoke('schedules:inbox', {
        sort,
        category: category || undefined,
        limit: PAGE_SIZE,
        offset,
      }) as { runs?: ScheduleRunRow[]; has_more?: boolean; total?: number }
      if (seq !== loadSeq.current) return
      setRows((prev) => (append ? [...prev, ...(res.runs ?? [])] : (res.runs ?? [])))
      setHasMore(!!res.has_more)
    } catch {
      /* 情报流拉取失败静默（保留现有内容，分页态不变） */
    } finally {
      if (seq === loadSeq.current) setLoading(false)
    }
  }, [sort, category])

  // 挂载 + 筛选/排序变更 → 重拉第一页；零订阅（totals.n=0，含 summary 未回的初始态）
  // 不拉取 —— inbox 必空，白拉请求；summary 返回后 n>0 自动补拉
  useEffect(() => {
    if (!totals.n) return undefined
    void load(0, false)
    return undefined
  }, [load, totals.n])

  // 订阅期次终态边沿重拉第一页（'schedule' 活动源——task 源不含影子任务终态，
  // 误订阅会导致新产出永不上屏；零新增轮询，同 useSchedules）
  const { isActive } = useActivity('schedule')
  useEdgeRefetch(isActive, () => {
    void load(0, false)
  })

  const relTime = (iso: string) => {
    const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
    const hours = Math.floor(minutes / 60)
    return minutes < 1 ? t('activity.time.justNow')
      : minutes < 60 ? t('activity.time.minutesAgo', { count: minutes })
      : hours < 24 ? t('activity.time.hoursAgo', { count: hours })
      : t('activity.time.daysAgo', { count: Math.floor(hours / 24) })
  }

  // 零订阅空态：引导建订阅（情报流的存在前提）
  if (!totals.n) {
    return (
      <div className={s.empty} data-testid="intel-feed-empty">
        <Inbox size={32} className={s.emptyIcon} />
        <p className={s.emptyText}>{t('intel.emptyNoSubs')}</p>
        <button type="button" className={s.emptyBtn} onClick={onOpenAdmin}>
          <Plus size={14} />{t('schedule.newSubscription')}
        </button>
      </div>
    )
  }

  return (
    <div className={s.wrap} data-testid="intel-feed">
      <div className={s.filters}>
        <div className={s.chips}>
          {CATEGORIES.map((c) => (
            <button
              key={c.value}
              type="button"
              className={`${s.chip} ${category === c.value ? s.chipActive : ''}`}
              onClick={() => setCategory(c.value)}
            >
              {t(c.labelKey)}
            </button>
          ))}
        </div>
        <div className={s.sorts}>
          <button
            type="button"
            className={`${s.chip} ${sort === 'unread' ? s.chipActive : ''}`}
            onClick={() => setSort('unread')}
          >
            {t('intel.sort.unread')}
          </button>
          <button
            type="button"
            className={`${s.chip} ${sort === 'latest' ? s.chipActive : ''}`}
            onClick={() => setSort('latest')}
          >
            {t('intel.sort.latest')}
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className={s.empty}>
          <Inbox size={32} className={s.emptyIcon} />
          <p className={s.emptyText}>{t('intel.emptyNoOutputs')}</p>
        </div>
      ) : (
        <div className={s.cardList}>
          {rows.map((run) => {
            const when = run.finished_at ?? run.started_at
            // 产业 done 卡快捷提取（0.6 §6.3-12：分类只驱动突出度，不裁剪阅读页操作集）
            const canExtract = run.schedule_category === 'industry' && run.status === 'done'
            // 卡片为 div[role=button]：内部需嵌真 button 快捷钮（button 不可嵌 button）
            return (
              <div
                key={run.id}
                role="button"
                tabIndex={0}
                className={s.card}
                data-testid="intel-card"
                onClick={() => onOpenRead(run)}
                onKeyDown={(e) => {
                  // 仅卡片自身响应键盘（CodeReview Minor 2026-10-24）：焦点在嵌套
                  // 快捷钮上时 Enter/Space 走按钮原生 click，冒泡到此处会被
                  // preventDefault 抑制原生激活、误开普通阅读动线
                  if (e.target !== e.currentTarget) return
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onOpenRead(run)
                  }
                }}
              >
                <span className={s.cardTitle}>{run.summary?.[0] || run.schedule_name}</span>
                <span className={s.cardMeta}>
                  <span className={s.catChip}>
                    {t(`intel.cat.${run.schedule_category || 'general'}`)}
                  </span>
                  {run.schedule_scope === 'rollup' && (
                    <span className={s.rollupTag}>{t('schedule.scope.rollup')}</span>
                  )}
                  <span className={s.cardName}>{run.schedule_name}</span>
                  <span className={s.cardTime}>{relTime(when)}</span>
                  {!run.read_at && <span className={s.unreadDot} data-testid="unread-dot" />}
                  {canExtract && (
                    <button
                      type="button"
                      className={s.cardExtract}
                      data-testid="intel-card-extract"
                      onClick={(e) => {
                        e.stopPropagation()
                        onOpenRead(run, 'extract')
                      }}
                    >
                      <Sparkles size={12} />{t('intel.lead.extractLead')}
                    </button>
                  )}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {hasMore && (
        <button
          type="button"
          className={s.loadMore}
          disabled={loading}
          onClick={() => void load(rows.length, true)}
        >
          {t('task.loadMore')}
        </button>
      )}
    </div>
  )
}
