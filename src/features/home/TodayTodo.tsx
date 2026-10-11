/** src/features/home/TodayTodo.tsx — 首页今日待办(3.0 §7.1:跨项目,逾期置顶;
 *  §10 裁决 10:只读 + 直达,打勾为快捷完成,不做字段编辑。
 *  行点击携带 taskId 直达执行抽屉(§6.1.8);行内按钮按类型分流至 Tab 级 */
import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'
import { isOverdue, daysOverdue, type DueTask } from '../project/affairs/types'
import { useToast } from '../knowledge/Toast'
import s from './TodayTodo.module.css'

export type HomeTab = 'affairs' | 'materials' | 'reports'

interface TodayTodoProps {
  tasks: DueTask[]
  /** taskId 可选:整行点击携带 → 事务 Tab + 自动打开执行抽屉;行内按钮仅 Tab 级 */
  onOpenProject: (projectId: string, tab: HomeTab, taskId?: string) => void
}

function localISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

export function TodayTodo({ tasks, onOpenProject }: TodayTodoProps) {
  const { t } = useTranslation()
  const showToast = useToast()

  const dueText = (task: DueTask): string => {
    if (isOverdue(task)) return t('homeTodo.overdueDays', { count: daysOverdue(task) })
    if (task.due_date!.slice(0, 10) === localISO(new Date())) return t('homeTodo.dueToday')
    const dueMs = new Date(`${task.due_date!.slice(0, 10)}T00:00:00`).getTime()
    const todayMs = new Date(`${localISO(new Date())}T00:00:00`).getTime()
    return t('homeTodo.dueInDays', { count: Math.round((dueMs - todayMs) / 86400000) })
  }

  const handleDone = async (task: DueTask) => {
    try {
      await window.electronAPI.invoke('affairs:updateTask', {
        projectId: task.project_id, taskId: task.id, status: 'done',
      })
      // 跨页边沿:App 重拉 listDue(侧栏色点/卡片墙/本列表同步)——打开时拉取+事件边沿,非轮询(0.4 §3.1)
      window.dispatchEvent(new CustomEvent('app:tasks-changed'))
    } catch {
      showToast(t('homeTodo.doneFailed'), 'error')
    }
  }

  if (tasks.length === 0) return null
  return (
    <section className={s.section}>
      <div className={s.title}>{t('homeTodo.title')}</div>
      <div className={s.list}>
        {tasks.map((task) => (
          <div key={task.id} className={s.row} onClick={() => onOpenProject(task.project_id, 'affairs', task.id)}>
            <span className={`${s.due} ${isOverdue(task) ? s.dueOverdue : ''}`}>{dueText(task)}</span>
            <span className={s.text}><b>{task.project_name}</b> · {task.title}</span>
            {task.task_type === 'material' && (
              <button className={s.action} title={t('homeTodo.upload')}
                onClick={(e) => { e.stopPropagation(); onOpenProject(task.project_id, 'materials') }}>
                {t('homeTodo.upload')}
              </button>
            )}
            {task.task_type === 'report' && (
              <button className={s.action} title={t('homeTodo.open')}
                onClick={(e) => { e.stopPropagation(); onOpenProject(task.project_id, 'reports') }}>
                {t('homeTodo.open')}
              </button>
            )}
            <button className={s.action} title={t('homeTodo.done')} aria-label={t('homeTodo.done')}
              onClick={(e) => { e.stopPropagation(); void handleDone(task) }}>
              <Check size={14} />
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}
