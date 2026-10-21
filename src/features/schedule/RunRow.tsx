/** src/features/schedule/RunRow.tsx — 订阅期次行（2.11 §9.1，E3 T7）。
 *  管理台右列 / 收件箱共用：期次（M/D）+ 状态点（running 呼吸/failed 红/done 默认）
 *  + 未读点（read_at 为空且 done）+ 订阅名（收件箱模式）+ 要点预览（title 悬停）。
 *  行内删除（2.11 §4.1，2026-10-20 审计补齐）：终态 run 悬停显删除按钮
 *  （onDelete 注入；running 行不渲染——在途执行后端 409 拒删）；
 *  外层 rowWrap 包裹防 button 嵌套（HTML 不允许 button 套 button）。 */
import { useTranslation } from 'react-i18next'
import { Trash2 } from 'lucide-react'
import type { ScheduleRunRow } from './useSchedules'
import s from './RunRow.module.css'

interface RunRowProps {
  run: ScheduleRunRow
  onOpen: () => void
  /** 单条删除回调（未传则不渲染删除按钮，如未来只读场景）；running 行恒不渲染 */
  onDelete?: () => void
}

export function RunRow({ run, onOpen, onDelete }: RunRowProps) {
  const { t } = useTranslation()
  const d = new Date(run.started_at)
  const label = `${d.getMonth() + 1}/${d.getDate()}`
  const summary = run.summary?.length ? run.summary.join('；') : ''
  return (
    <div className={s.rowWrap} data-testid="run-row-wrap">
      <button className={s.row} data-testid="run-row" onClick={onOpen}>
        <span className={`${s.dot} ${run.status === 'running' ? s.dotRunning : ''} ${run.status === 'failed' ? s.dotFailed : ''}`} />
        <span className={s.date}>{label}</span>
        {run.schedule_name && <span className={s.name}>{run.schedule_name}</span>}
        {run.status === 'done' && !run.read_at && (
          <span className={s.unreadDot} data-testid="run-unread-dot" title={t('schedule.unread')} />
        )}
        {summary && <span className={s.summary} title={summary}>{summary}</span>}
      </button>
      {onDelete && run.status !== 'running' && (
        <button
          className={s.deleteBtn}
          data-testid="run-delete-btn"
          title={t('schedule.deleteRun')}
          onClick={(e) => { e.stopPropagation(); onDelete() }}
        >
          <Trash2 size={12} />
        </button>
      )}
    </div>
  )
}
