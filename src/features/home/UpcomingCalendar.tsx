/** src/features/home/UpcomingCalendar.tsx — 首页近期日历(3.0 §6.1.8:未来 7 天跨项目事务按日分组) */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { DUE_HORIZON_DAYS, type DueTask } from '../project/affairs/types'
import type { HomeTab } from './TodayTodo'
import s from './UpcomingCalendar.module.css'

interface UpcomingCalendarProps {
  tasks: DueTask[]
  onOpenProject: (projectId: string, tab: HomeTab) => void
}

function localISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

export function UpcomingCalendar({ tasks, onOpenProject }: UpcomingCalendarProps) {
  const { t } = useTranslation()

  const days = useMemo(() => {
    const today = localISO(new Date())
    const limit = new Date()
    limit.setDate(limit.getDate() + DUE_HORIZON_DAYS)
    const byDay = new Map<string, DueTask[]>()
    for (const task of tasks) {
      const due = task.due_date?.slice(0, 10)
      if (!due || due < today || due > localISO(limit)) continue
      const arr = byDay.get(due) ?? []
      arr.push(task)
      byDay.set(due, arr)
    }
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [tasks])

  if (days.length === 0) return null
  return (
    <section className={s.section}>
      <div className={s.title}>{t('homeTodo.calendarTitle')}</div>
      {days.map(([day, list]) => (
        <div key={day} className={s.dayRow}>
          <span className={s.dayLabel}>
            {day.slice(5).replace('-', '/')} · {t(`affairs.dow${new Date(`${day}T00:00:00`).getDay()}`)}
          </span>
          <div className={s.dayItems}>
            {list.map((task) => (
              <span key={task.id} className={s.item}
                onClick={() => onOpenProject(task.project_id, 'affairs')}>
                <b>{task.project_name}</b> · {task.title}
              </span>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}
