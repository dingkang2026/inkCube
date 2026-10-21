/** src/features/project/affairs/CalendarView.tsx — 日历视图(3.0 §6.1.5)
 *  月历网格(固定 6 行 42 格,前后月补灰):格内按 due_date 分组显示任务条
 *  (≤3 条 +N,done 灰、逾期红);点任务条开抽屉(stopPropagation 防冒泡),
 *  点格空白以该日预填新建(经父级 AffairsTab → TaskFormModal);今日高亮,可回今天。
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { isOverdue } from './types'
import type { PhaseTask } from './types'
import s from './CalendarView.module.css'

export interface CalendarViewProps {
  tasks: PhaseTask[]
  onOpenDrawer: (task: PhaseTask) => void
  /** 点格空白：携带该日 YYYY-MM-DD 供表单预填（Task 10 经 AffairsTab 接线） */
  onCreateTask: (prefillDate: string) => void
}

const pad2 = (n: number): string => String(n).padStart(2, '0')
const dsOf = (d: Date): string => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`

export function CalendarView({ tasks, onOpenDrawer, onCreateTask }: CalendarViewProps) {
  const { t } = useTranslation()
  const today = new Date()
  const [cur, setCur] = useState({ y: today.getFullYear(), m: today.getMonth() })

  // 42 格:当月首日所在周的周日起,含前后月补位
  const cells = useMemo(() => {
    const first = new Date(cur.y, cur.m, 1)
    const start = new Date(cur.y, cur.m, 1 - first.getDay())
    const out: { ds: string; inMonth: boolean; day: number }[] = []
    for (let i = 0; i < 42; i++) {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i)
      out.push({ ds: dsOf(d), inMonth: d.getMonth() === cur.m, day: d.getDate() })
    }
    return out
  }, [cur])

  // 按 due_date 归格;pending_activation(铺入侧非当前期种子实例)不上墙,对齐 PhaseBoard/RhythmCards 口径
  const byDate = useMemo(() => {
    const map = new Map<string, PhaseTask[]>()
    for (const task of tasks) {
      if (!task.due_date || task.status === 'pending_activation') continue
      const key = task.due_date.slice(0, 10)
      const list = map.get(key) ?? []
      list.push(task)
      map.set(key, list)
    }
    return map
  }, [tasks])

  const todayStr = dsOf(today)

  return (
    <div className={s.container}>
      <div className={s.calHead}>
        <button className={s.navBtn}
          onClick={() => setCur(({ y, m }) => (m === 0 ? { y: y - 1, m: 11 } : { y, m: m - 1 }))}>
          <ChevronLeft size={14} />
        </button>
        <span className={s.calTitle}>{t('affairs.calMonth', { year: cur.y, month: cur.m + 1 })}</span>
        <button className={s.navBtn}
          onClick={() => setCur(({ y, m }) => (m === 11 ? { y: y + 1, m: 0 } : { y, m: m + 1 }))}>
          <ChevronRight size={14} />
        </button>
        <button className={s.todayBtn} onClick={() => setCur({ y: today.getFullYear(), m: today.getMonth() })}>
          {t('affairs.calToday')}
        </button>
      </div>
      <div className={s.calGrid}>
        {[0, 1, 2, 3, 4, 5, 6].map((i) => (
          <div key={i} className={s.dow}>{t(`affairs.dow${i}`)}</div>
        ))}
        {cells.map(({ ds, inMonth, day }) => {
          const items = byDate.get(ds) ?? []
          const shown = items.slice(0, 3)
          return (
            <div key={ds}
              className={`${s.cell} ${inMonth ? '' : s.cellOut} ${ds === todayStr ? s.cellToday : ''}`}
              onClick={() => onCreateTask(ds)}>
              <span className={s.dayNum}>{day}</span>
              {shown.map((task) => (
                <button key={task.id}
                  className={`${s.evBtn} ${task.status === 'done' ? s.evDone : isOverdue(task) ? s.evErr : ''}`}
                  onClick={(e) => { e.stopPropagation(); onOpenDrawer(task) }}>
                  {task.title}
                </button>
              ))}
              {items.length > 3 && <span className={s.evMore}>+{items.length - 3}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
