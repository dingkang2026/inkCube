/** src/features/project/affairs/TaskDrawer.tsx — 执行抽屉(3.0 §6.1.7)
 *  右侧滑出面板,复用 WikiTab 预览面板模式:拖宽 260-720px、遮罩点击收起;
 *  与 Chat 互斥由父级 onPanelOpenChange 处理(§6.4-7)。
 *  动作:完成/跳过/恢复/编辑/删除(useConfirm danger)/发起报告(report 类,裁决 7);
 *  material 类 v1 显示上传引导文案(裁决 9 延伸:闭环挂解析终态,不内置上传);
 *  关联文件名不显示(裁决 9);完成规则文案含 auto_done 来源标注。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, SkipForward, RotateCcw, Pencil, Trash2, FileText } from 'lucide-react'
import { useConfirm } from '../../../components/ConfirmDialog'
import { daysOverdue, isOverdue } from './types'
import type { PhaseTask } from './types'
import s from './TaskDrawer.module.css'

const MIN_W = 260
const MAX_W = 720
const DEFAULT_W = 420

export interface TaskDrawerProps {
  task: PhaseTask
  projectId: string
  onClose: () => void
  onChanged: () => void
  onEdit: () => void
  onOpenReports: () => void
}

export function TaskDrawer({ task, projectId, onClose, onChanged, onEdit, onOpenReports }: TaskDrawerProps) {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const [width, setWidth] = useState(DEFAULT_W)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(false)
  const dragging = useRef(false)
  const startX = useRef(0)
  const startW = useRef(DEFAULT_W)

  const onDragDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    startX.current = e.clientX
    startW.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!dragging.current) return
      const delta = startX.current - e.clientX
      setWidth(Math.min(MAX_W, Math.max(MIN_W, startW.current + delta)))
    }
    const up = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
    return () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
      // 拖宽中途卸载(如任务被清理自动关抽屉)也复位 body 样式,防游标/选区卡死
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [])

  const patch = async (fields: Record<string, unknown>) => {
    if (busy) return
    setBusy(true)
    setActionError(false)
    try {
      await window.electronAPI.invoke('affairs:updateTask', { projectId, taskId: task.id, ...fields })
      onChanged()
    } catch (err) {
      console.error('Failed to update task:', err)
      setActionError(true)   // 失败不得静默(对齐 saveFailed/loadFailed 原则)
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async () => {
    if (busy) return
    if (!(await confirmDialog({
      title: t('affairs.drawer.deleteTitle'),
      message: t('affairs.drawer.deleteConfirm', { title: task.title }),
      danger: true,
    }))) return
    setBusy(true)
    setActionError(false)
    try {
      await window.electronAPI.invoke('affairs:deleteTask', { projectId, taskId: task.id })
      onChanged()
      onClose()
    } catch (err) {
      console.error('Failed to delete task:', err)
      setActionError(true)
    } finally {
      setBusy(false)
    }
  }

  const ruleText = (): string => {
    const ac = task.auto_complete
    if (ac?.on === 'file_parsed') return t('affairs.drawer.ruleFile', { match: ac.match ?? '' })
    if (ac?.on === 'report_completed') {
      return t('affairs.drawer.ruleReport', { match: (ac.match ?? '').replace(/^title~/, '') })
    }
    if (task.recurrence_rule) return t('affairs.drawer.ruleRecurring')
    return t('affairs.drawer.ruleManual')
  }

  return (
    <div className={s.overlay} onClick={busy ? undefined : onClose}>
      <div className={s.panel} style={{ width }} onClick={(e) => e.stopPropagation()}>
        <div className={s.dragHandle} onMouseDown={onDragDown} />
        <div className={s.body}>
          <div className={s.headRow}>
            <span className={`${s.statusBadge} ${task.status === 'done' ? s.badgeDone
              : task.status === 'skipped' ? s.badgeMuted
              : isOverdue(task) ? s.badgeErr : ''}`}>
              {t(`affairs.status.${task.status}`)}
            </span>
            <span className={s.typeBadge}>{t(`affairs.type.${task.task_type}`)}</span>
            {task.occurrence_key && <span className={s.occ}>{task.occurrence_key}</span>}
          </div>
          <h3 className={s.title}>{task.title}</h3>
          {task.description && <p className={s.desc}>{task.description}</p>}
          <div className={s.meta}>
            {/* 非 ISO due_date 哨兵:显示原文,不产碎片文本/NaN 天数(与 PhaseBoard dueText 同口径,Task 7 C-2) */}
            <span className={isOverdue(task) ? s.overdue : ''}>
              {task.due_date
                ? !/^\d{4}-\d{2}-\d{2}/.test(task.due_date) ? task.due_date
                  : isOverdue(task)
                    ? t('affairs.overdueDays', { count: daysOverdue(task) })
                    : t('affairs.drawer.dueAt', { date: task.due_date.slice(0, 10) })
                : t('affairs.drawer.noDue')}
            </span>
          </div>
          <div className={s.rule}>
            <span className={s.ruleLabel}>{t('affairs.drawer.ruleLabel')}</span>
            <span>{ruleText()}</span>
            {task.completed_by === 'auto' && task.completed_at && (
              <span className={s.autoDone}>
                {t('affairs.drawer.autoDone', { date: task.completed_at.slice(0, 10) })}
              </span>
            )}
          </div>
          {task.task_type === 'material' && task.status === 'open' && (
            <p className={s.uploadHint}>{t('affairs.drawer.uploadHint')}</p>
          )}
          {actionError && <p className={s.actionError}>{t('affairs.drawer.actionFailed')}</p>}
          <div className={s.actions}>
            {task.status === 'open' && (
              <>
                <button className={s.primaryBtn} disabled={busy} onClick={() => void patch({ status: 'done' })}>
                  <Check size={14} />{t('affairs.drawer.done')}
                </button>
                <button className={s.btn} disabled={busy} onClick={() => void patch({ status: 'skipped' })}>
                  <SkipForward size={14} />{t('affairs.drawer.skip')}
                </button>
              </>
            )}
            {(task.status === 'done' || task.status === 'skipped') && (
              <button className={s.btn} disabled={busy} onClick={() => void patch({ status: 'open' })}>
                <RotateCcw size={14} />{t('affairs.drawer.reopen')}
              </button>
            )}
            <button className={s.btn} onClick={onEdit}>
              <Pencil size={14} />{t('affairs.drawer.edit')}
            </button>
            {task.task_type === 'report' && task.status !== 'done' && (
              <button className={s.btn} onClick={onOpenReports}>
                <FileText size={14} />{t('affairs.drawer.startReport')}
              </button>
            )}
            <button className={s.dangerBtn} disabled={busy} onClick={() => void handleDelete()}>
              <Trash2 size={14} />{t('affairs.drawer.delete')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
