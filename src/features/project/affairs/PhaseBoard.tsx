/** src/features/project/affairs/PhaseBoard.tsx — 时期看板(3.0 §6.1.3)
 *  顶部时期进度条仅展示当前期高亮(general 无时期则不渲染);任务按时期分组,
 *  当前期默认展开、其余折叠为计数;阶段 pill 切换仅自动展开对应组,不激活任务(§6.4-2)。
 *  行状态色(§6.3):逾期 --error / 7 日内 --warning / 进行中 --accent /
 *  done 灰 / skipped 灰划线 / pending_activation 灰斜体「等待进入时期」。
 *  已跳过默认隐藏,开关切换;组内 due_date 升序、无 due 殿后。
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import type { ProjectStage, ProjectType } from '../types'
import { STAGE_MAP } from '../types'
import { daysOverdue, dueWithin, isOverdue } from './types'
import type { PhaseKey, PhaseTask } from './types'
import s from './PhaseBoard.module.css'

const PHASES: PhaseKey[] = ['before', 'during', 'after']

export interface PhaseBoardProps {
  tasks: PhaseTask[]
  stage: ProjectStage
  projectType: ProjectType
  onCreateTask: () => void
  onOpenDrawer: (task: PhaseTask) => void
}

export function PhaseBoard({ tasks, stage, projectType, onCreateTask, onOpenDrawer }: PhaseBoardProps) {
  const { t } = useTranslation()
  const [openPhases, setOpenPhases] = useState<Set<PhaseKey>>(
    () => new Set(stage !== 'none' ? [stage as PhaseKey] : PHASES),
  )
  const [showSkipped, setShowSkipped] = useState(false)

  // 阶段 pill 切换 → 自动展开对应组(仅展开视图,不激活任务,§6.4-2;不收缩已展开组)
  useEffect(() => {
    if (stage !== 'none') setOpenPhases((prev) => new Set([...prev, stage as PhaseKey]))
  }, [stage])

  const visible = useMemo(
    () => tasks.filter((x) => showSkipped || x.status !== 'skipped'),
    [tasks, showSkipped],
  )
  const phaseLabelKey = (p: PhaseKey): string =>
    STAGE_MAP[projectType].find((x) => x.key === p)?.labelKey ?? `project.stage.${p}`

  const rowClass = (task: PhaseTask): string => {
    if (task.status === 'done') return s.rowDone
    if (task.status === 'skipped') return s.rowSkipped
    if (task.status === 'pending_activation') return s.rowPending
    if (isOverdue(task)) return s.rowErr
    if (dueWithin(task, 7)) return s.rowWarn
    return s.rowOpen
  }
  const dueText = (task: PhaseTask): string => {
    if (task.status === 'pending_activation') return t('affairs.pendingHint')
    if (!task.due_date) return '—'
    // 非 ISO 格式哨兵:API 直调坏值下显示原文,不产碎片文本/NaN 天数(Task 7 评审 C-2)
    if (!/^\d{4}-\d{2}-\d{2}/.test(task.due_date)) return task.due_date
    if (isOverdue(task)) return t('affairs.overdueDays', { count: daysOverdue(task) })
    return task.due_date.slice(5, 10)
  }

  const togglePhase = (p: PhaseKey) => {
    setOpenPhases((prev) => {
      const next = new Set(prev)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return next
    })
  }

  return (
    <div className={s.container}>
      <div className={s.head}>
        {STAGE_MAP[projectType].length > 0 && (
          <div className={s.phaseBar}>
            {PHASES.map((p) => (
              <span key={p} className={`${s.phaseSeg} ${stage === p ? s.phaseSegActive : ''}`}>
                {t(phaseLabelKey(p))}
              </span>
            ))}
          </div>
        )}
        <div className={s.headActions}>
          <label className={s.skippedToggle}>
            <input type="checkbox" checked={showSkipped} onChange={(e) => setShowSkipped(e.target.checked)} />
            <span>{t('affairs.showSkipped')}</span>
          </label>
          <button className={s.addBtn} onClick={onCreateTask} title={t('affairs.addTask')}>
            <Plus size={14} /><span>{t('affairs.addTask')}</span>
          </button>
        </div>
      </div>
      {visible.length === 0 && <div className={s.empty}>{t('affairs.boardEmpty')}</div>}
      {PHASES.map((p) => {
        const group = visible
          .filter((x) => x.phase === p)
          // 无 due 殿后用主键位而非 '9999' 字符串哨兵(due ≥ 9999-… 时哨兵失效的理论边界,评审 C-3)
          .sort((a, b) =>
            (a.due_date ? 0 : 1) - (b.due_date ? 0 : 1) ||
            (a.due_date ?? '').localeCompare(b.due_date ?? ''))
        if (group.length === 0) return null
        const hasPhases = STAGE_MAP[projectType].length > 0
        const open = !hasPhases || openPhases.has(p)
        return (
          <div key={p} className={s.phaseGroup}>
            {hasPhases && (
              <button className={s.groupHead} aria-expanded={open} onClick={() => togglePhase(p)}>
                <span>{t(phaseLabelKey(p))}</span>
                <span className={s.groupCount}>{group.length}</span>
                <span className={s.caret}>{open ? '▾' : '▸'}</span>
              </button>
            )}
            {open && (
              <div className={s.rows}>
                {group.map((task) => (
                  <button key={task.id}
                    className={`${s.row} ${rowClass(task)}`}
                    onClick={() => onOpenDrawer(task)}>
                    <span className={s.dot} />
                    <span className={s.rowTitle}>{task.title}</span>
                    {task.task_type === 'recurring' && task.occurrence_key && (
                      <span className={s.occ}>{task.occurrence_key}</span>
                    )}
                    <span className={s.typeBadge}>{t(`affairs.type.${task.task_type}`)}</span>
                    <span className={s.due}>{dueText(task)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
