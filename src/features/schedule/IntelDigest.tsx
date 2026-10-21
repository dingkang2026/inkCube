/** src/features/schedule/IntelDigest.tsx — 首页情报速览（2.11 §8.1 动线②，E3 T9）。
 *  挂载拉一次 schedules:digest（每订阅最新 1 条 run，含 running/failed）+ task
 *  活动终态边沿重拉（useSchedules 同模式，零新增轮询，0.4 §6.3）；无订阅不渲染
 *  （0.5 §4.2：E1/E2 此区块不渲染），有订阅无产出显示空态。
 *  行 = 要点首行/订阅名 + 相对时间 + 状态点（运行呼吸/异常红）+ 未读点（§8.4
 *  状态-界面映射）；暂停订阅行灰显（历史可读，不变量 1）。
 *  done/failed 行点击 → onOpen(runId)（ReadView）；running 行点击 → onOpenAdmin()。 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useActivity } from '../activity/ActivityContext'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { useSchedules, type ScheduleRunRow } from './useSchedules'
import s from './IntelDigest.module.css'

interface IntelDigestProps {
  /** done/failed 行点击：App 切 scheduleView read 态 */
  onOpen: (runId: string) => void
  /** running 行点击 /「查看全部」：打开订阅管理台 */
  onOpenAdmin: () => void
}

export function IntelDigest({ onOpen, onOpenAdmin }: IntelDigestProps) {
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

  // 影子任务终态边沿重拉（新产出上屏；零新增轮询）
  const { isActive } = useActivity('task')
  useEdgeRefetch(isActive, () => {
    void load()
  })

  if (!totals.n) return null

  const relTime = (iso: string) => {
    const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
    const hours = Math.floor(minutes / 60)
    return minutes < 1 ? t('activity.time.justNow')
      : minutes < 60 ? t('activity.time.minutesAgo', { count: minutes })
      : hours < 24 ? t('activity.time.hoursAgo', { count: hours })
      : t('activity.time.daysAgo', { count: Math.floor(hours / 24) })
  }

  return (
    <section className={s.block} data-testid="intel-digest">
      <div className={s.titleRow}>
        <span className={s.sectionTitle}>{t('home.digestTitle')}</span>
        <button className={s.viewAll} onClick={onOpenAdmin}>
          {t('schedule.digest.viewAll')}
        </button>
      </div>
      {rows.length === 0 ? (
        <div className="empty-block">{t('schedule.digest.empty')}</div>
      ) : (
        <div className={s.rowList}>
          {rows.map((run) => {
            const paused = run.schedule_enabled === 0
            const when = run.finished_at ?? run.started_at
            return (
              <div
                key={run.id}
                data-testid="digest-row"
                className={`${s.row} ${paused ? s.rowPaused : ''}`}
                onClick={() => (run.status === 'running' ? onOpenAdmin() : onOpen(run.id))}
              >
                {run.status === 'running' && (
                  <span data-testid="dot-running" className={`${s.dot} ${s.dotRunning}`} />
                )}
                {run.status === 'failed' && (
                  <span data-testid="dot-failed" className={`${s.dot} ${s.dotAbnormal}`} />
                )}
                <span className={s.rowTitle}>{run.summary?.[0] || run.schedule_name}</span>
                <span className={s.rowTime}>{relTime(when)}</span>
                {run.status === 'done' && !run.read_at && (
                  <span data-testid="unread-dot" className={s.unreadDot} />
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
