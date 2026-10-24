/**
 * src/features/schedule/IntelCardZone.tsx — 首页情报卡区（0.6 §3.2 V2-E1a T10，
*  前身 IntelDigest 2.11 §8.1 动线②，主列顶部化；V2-E1b 2026-10-24 1 大 N 小）。
 *  挂载拉一次 schedules:digest（每订阅最新 1 条 run，含 running/failed）+ schedule
 *  活动终态边沿重拉（useSchedules 同模式，零新增轮询，0.4 §6.3）；
 *  零订阅渲染空态引导（「新建订阅」→ dispatch app:navigate-schedules，0.6 §3.2
 *  「情报是首屏第一公民」——空态不再是无声消失）；区块头「查看全部」→
 *  dispatch app:navigate-intel（情报中心 feed tab）。
 *  编辑排版（§3.0-3 / §3.1②）：最新一条（finished_at ?? started_at 倒序）占大卡，
 *  带摘要两行预览 + 类别 chip（schedule_category 直读）+ 订阅名 meta；其余紧凑行
 *  （要点首行/订阅名 + 相对时间 + 状态点 + 未读点，§8.4 状态-界面映射）；
 *  暂停订阅灰显（历史可读，不变量 1）。
 *  done/failed 点击 → onOpen(runId)（ReadView）；running 点击 →
 *  dispatch app:navigate-schedules（订阅管理看进度，语义不分大小卡）。
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import { useActivity } from '../activity/ActivityContext'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { useSchedules, type ScheduleRunRow } from './useSchedules'
import s from './IntelCardZone.module.css'

interface IntelCardZoneProps {
  /** done/failed 行点击：App 切 scheduleView read 态 */
  onOpen: (runId: string) => void
}

export function IntelCardZone({ onOpen }: IntelCardZoneProps) {
  const { t } = useTranslation()
  const { totals } = useSchedules()
  const [rows, setRows] = useState<ScheduleRunRow[]>([])

  const load = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('schedules:digest') as {
        digest?: ScheduleRunRow[]
      }
      setRows(res.digest ?? [])
    } catch {
      /* 增强信息拉取失败静默（不打断首页主体） */
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 订阅期次终态边沿重拉（'schedule' 活动源——task 源不含影子任务终态，零新增轮询）
  const { isActive } = useActivity('schedule')
  useEdgeRefetch(isActive, () => {
    void load()
  })

  // 零订阅空态引导（0.6 §3.2：首页第一公民必须有路可走——建订阅）
  if (!totals.n) {
    return (
      <section className={s.block} data-testid="intel-card-zone">
        <div className={s.titleRow}>
          <span className={s.sectionTitle}>{t('home.digestTitle')}</span>
        </div>
        <div className={s.emptyGuide} data-testid="intel-zone-empty">
          <p className={s.emptyText}>{t('intel.emptyNoSubs')}</p>
          <button
            type="button"
            className={s.emptyBtn}
            onClick={() => window.dispatchEvent(new CustomEvent('app:navigate-schedules'))}
          >
            <Plus size={14} />{t('schedule.newSubscription')}
          </button>
        </div>
      </section>
    )
  }

  const relTime = (iso: string) => {
    const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
    const hours = Math.floor(minutes / 60)
    return minutes < 1 ? t('activity.time.justNow')
      : minutes < 60 ? t('activity.time.minutesAgo', { count: minutes })
      : hours < 24 ? t('activity.time.hoursAgo', { count: hours })
      : t('activity.time.daysAgo', { count: Math.floor(hours / 24) })
  }

  // 1 大 N 小（§3.0-3）：大卡 = 时间最新一条，其余保持 digest 原序为紧凑行
  const latest = rows.reduce<ScheduleRunRow | null>((acc, run) => {
    const tOf = (r: ScheduleRunRow) => new Date(r.finished_at ?? r.started_at).getTime()
    return !acc || tOf(run) > tOf(acc) ? run : acc
  }, null)
  const restRows = latest ? rows.filter((r) => r.id !== latest.id) : []

  return (
    <section className={s.block} data-testid="intel-card-zone">
      <div className={s.titleRow}>
        <span className={s.sectionTitle}>{t('home.digestTitle')}</span>
        <button
          className={s.viewAll}
          onClick={() => window.dispatchEvent(new CustomEvent('app:navigate-intel'))}
        >
          {t('schedule.digest.viewAll')}
        </button>
      </div>
      {rows.length === 0 ? (
        <div className="empty-block">{t('schedule.digest.empty')}</div>
      ) : (
        <>
          {latest && (
            <div
              data-testid="digest-big"
              className={`${s.bigCard} ${latest.schedule_enabled === 0 ? s.rowPaused : ''}`}
              onClick={() => (latest.status === 'running'
                ? window.dispatchEvent(new CustomEvent('app:navigate-schedules'))
                : onOpen(latest.id))}
            >
              <div className={s.bigHead}>
                {latest.status === 'running' && (
                  <span data-testid="dot-running" className={`${s.dot} ${s.dotRunning}`} />
                )}
                {latest.status === 'failed' && (
                  <span data-testid="dot-failed" className={`${s.dot} ${s.dotAbnormal}`} />
                )}
                <span className={s.bigTitle}>{latest.summary?.[0] || latest.schedule_name}</span>
                {latest.status === 'done' && !latest.read_at && (
                  <span data-testid="unread-dot" className={s.unreadDot} />
                )}
              </div>
              {(latest.summary?.length ?? 0) > 1 && (
                <p className={s.bigPreview}>{latest.summary!.slice(1).filter(Boolean).join('　')}</p>
              )}
              <div className={s.bigMeta}>
                <span className={s.catChip}>{t(`intel.cat.${latest.schedule_category || 'general'}`)}</span>
                {latest.schedule_scope === 'rollup' && (
                  <span className={s.rollupTag}>{t('schedule.scope.rollup')}</span>
                )}
                {/* 订阅名仅标题取自摘要时补显，防同名重复 */}
                {latest.summary?.[0] && <span className={s.bigName}>{latest.schedule_name}</span>}
                <span className={s.bigTime}>{relTime(latest.finished_at ?? latest.started_at)}</span>
              </div>
            </div>
          )}
          <div className={s.rowList}>
            {restRows.map((run) => {
              const paused = run.schedule_enabled === 0
              const when = run.finished_at ?? run.started_at
              return (
                <div
                  key={run.id}
                  data-testid="digest-row"
                  className={`${s.row} ${paused ? s.rowPaused : ''}`}
                  onClick={() => (run.status === 'running'
                    ? window.dispatchEvent(new CustomEvent('app:navigate-schedules'))
                    : onOpen(run.id))}
                >
                  {run.status === 'running' && (
                    <span data-testid="dot-running" className={`${s.dot} ${s.dotRunning}`} />
                  )}
                  {run.status === 'failed' && (
                    <span data-testid="dot-failed" className={`${s.dot} ${s.dotAbnormal}`} />
                  )}
                  <span className={s.rowTitle}>
                    {run.schedule_scope === 'rollup' && (
                      <span className={s.rollupTag}>{t('schedule.scope.rollup')}</span>
                    )}
                    {run.summary?.[0] || run.schedule_name}
                  </span>
                  <span className={s.rowTime}>{relTime(when)}</span>
                  {run.status === 'done' && !run.read_at && (
                    <span data-testid="unread-dot" className={s.unreadDot} />
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
