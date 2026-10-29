/** src/features/project/affairs/RhythmCards.tsx — 周期节奏卡(3.0 §6.1.4)
 *  按 template_id 聚合周期任务(自建 recurring 共享 user_* 模板键,同样聚合);
 *  卡头节奏文案取组内最新期(MAX occurrence_key)实例 —— 后端滚动生成器已保证
 *  未来期次携带最新 rule(裁决 4);卡内横排近 6 期状态,点期次开抽屉,「调整节奏」进编辑。
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Repeat, Settings2 } from 'lucide-react'
import { isOverdue, dueWithin } from './types'
import type { PhaseTask, RecurrenceRule } from './types'
import s from './RhythmCards.module.css'

export interface RhythmCardsProps {
  tasks: PhaseTask[]
  onOpenDrawer: (task: PhaseTask) => void
  onEditRhythm: (task: PhaseTask) => void
}

interface RhythmGroup {
  key: string
  title: string
  rule: RecurrenceRule
  latest: PhaseTask
  recent: PhaseTask[]                       // 跨格式时间序最后 6 期
}

/** occurrence_key 跨格式时间序(混排换档字典序非时间序,Task 8 评审 M-2):
 *  月=年*12+月-1、季=年*12+(Q-1)*3、年=年*12;脏 key 殿后 */
const occRank = (key: string | null): number => {
  if (!key) return -1
  let m = /^(\d{4})-(\d{2})$/.exec(key); if (m) return +m[1] * 12 + (+m[2] - 1)
  m = /^(\d{4})-Q(\d)$/.exec(key); if (m) return +m[1] * 12 + (+m[2] - 1) * 3
  m = /^(\d{4})$/.exec(key); if (m) return +m[1] * 12
  return Number.MAX_SAFE_INTEGER
}

export function RhythmCards({ tasks, onOpenDrawer, onEditRhythm }: RhythmCardsProps) {
  const { t } = useTranslation()
  const groups = useMemo<RhythmGroup[]>(() => {
    const byKey = new Map<string, PhaseTask[]>()
    for (const task of tasks) {
      if (!task.recurrence_rule) continue
      const key = task.template_id ?? task.id
      const list = byKey.get(key) ?? []
      list.push(task)
      byKey.set(key, list)
    }
    return [...byKey.entries()].map(([key, list]) => {
      // 行内时间轴按 occRank 排;latest 单独保持字典序 MAX —— 与后端 roll 的 rule 载体
      // rows[-1](ORDER BY occurrence_key ASC)严格同构,保证「调整节奏」编辑到的
      // rule 即下次滚动实际采用的 rule(裁决 4,Task 8 评审 M-2 解耦警示)
      const byTime = [...list].sort((a, b) => occRank(a.occurrence_key) - occRank(b.occurrence_key))
      const latest = [...list].sort((a, b) =>
        (a.occurrence_key ?? '').localeCompare(b.occurrence_key ?? ''))[list.length - 1]
      return { key, title: latest.title, rule: latest.recurrence_rule!, latest, recent: byTime.slice(-6) }
    })
  }, [tasks])

  if (groups.length === 0) return <div className={s.empty}>{t('affairs.rhythmEmpty')}</div>

  const freqLabel = (rule: RecurrenceRule): string =>
    rule.freq === 'monthly' ? t('affairs.freqMonthly')
      : rule.freq === 'quarterly' ? t('affairs.freqQuarterly')
        : t('affairs.freqYearly')

  const dueLabel = (rule: RecurrenceRule): string => {
    if (rule.freq === 'monthly') return t('affairs.dueDayOfMonth', { day: String(rule.due_day) })
    if (rule.freq === 'quarterly') {
      return rule.due_day === 'end'
        ? t('affairs.dueDayQuarterEnd')
        : t('affairs.dueDayOfQuarter', { day: String(rule.due_day) })
    }
    return t('affairs.dueDayOfYear', { date: String(rule.due_day) })
  }

  const occCellClass = (task: PhaseTask): string => {
    // pending_activation 灰斜体:铺入侧非当前期种子实例带 rule+occ=NULL,不处理会
    // 以「进行中」蓝点出现(评审 M-1;与 PhaseBoard rowPending 口径对齐)
    if (task.status === 'pending_activation') return s.occPending
    if (task.status === 'done') return s.occDone
    if (task.status === 'skipped') return s.occSkipped
    if (isOverdue(task)) return s.occErr
    if (dueWithin(task, 7)) return s.occWarn
    return s.occOpen
  }
  const occShort = (key: string | null): string => {
    if (!key) return '—'
    if (/^\d{4}-\d{2}$/.test(key)) return key.slice(5) + t('affairs.occMonthSuffix')  // 10月 / 10
    if (/^\d{4}-Q\d$/.test(key)) return key.slice(5)                                   // Q4
    return key
  }
  // hover 语义补充:done 期次带完成时间(格内仅期次短键,完成时间经 title 呈现)
  const occTitle = (task: PhaseTask): string => {
    const key = task.occurrence_key ?? '—'
    if (task.status === 'done' && task.completed_at) {
      return `${key} · ${t('affairs.doneAt', { date: task.completed_at.slice(0, 10) })}`
    }
    return key
  }

  return (
    <div className={s.container}>
      {groups.map((g) => (
        <div key={g.key} className={s.card}>
          <div className={s.cardHead}>
            <Repeat size={14} />
            <span className={s.cardTitle}>{g.title}</span>
            <span className={s.freqBadge}>{freqLabel(g.rule)} · {dueLabel(g.rule)}</span>
            <button className={s.editBtn} onClick={() => onEditRhythm(g.latest)} title={t('affairs.editRhythm')}>
              <Settings2 size={13} /><span>{t('affairs.editRhythm')}</span>
            </button>
          </div>
          <div className={s.occRow}>
            {g.recent.map((task) => (
              <button key={task.id}
                className={`${s.occCell} ${occCellClass(task)}`}
                onClick={() => onOpenDrawer(task)}
                title={occTitle(task)}>
                <span className={s.occKey}>{occShort(task.occurrence_key)}</span>
                <span className={s.occDot} />
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
