import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { MoreVertical } from 'lucide-react'
import type { Task } from '../../types'
import { useConfirm } from '../../components/ConfirmDialog'
import { UsageRounds } from '../usage/UsageRounds'
import s from './TaskItem.module.css'

interface TaskItemProps {
  task: Task
  isActive: boolean
  isRunning?: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (title: string) => void
  onArchive?: () => void
  onUnarchive?: () => void
  onExport?: () => void
}

export function TaskItem({
  task,
  isActive,
  isRunning = false,
  onSelect,
  onDelete,
  onRename,
  onArchive,
  onUnarchive,
  onExport,
}: TaskItemProps) {
  const { t, i18n } = useTranslation()
  const [isEditing, setIsEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(task.title)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const confirmDialog = useConfirm()

  useEffect(() => {
    if (!menuOpen) return
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [menuOpen])

  const handleRename = () => {
    if (editTitle.trim() && editTitle !== task.title) {
      onRename(editTitle.trim())
    }
    setIsEditing(false)
  }

  const handleDelete = async () => {
    setMenuOpen(false)
    if (!(await confirmDialog({ title: t('task.deleteBtn'), message: t('task.confirmDelete') }))) return
    onDelete()
  }

  // 智能日期：今天 HH:mm / 今年 MM-DD / 跨年 YYYY-MM-DD（hover 显示完整时间）
  const updated = new Date(task.updated_at)
  const now = new Date()
  const time = updated.toDateString() === now.toDateString()
    ? updated.toLocaleTimeString(i18n.language, { hour: '2-digit', minute: '2-digit' })
    : updated.getFullYear() === now.getFullYear()
      ? updated.toLocaleDateString(i18n.language, { month: '2-digit', day: '2-digit' })
      : updated.toLocaleDateString(i18n.language, { year: 'numeric', month: '2-digit', day: '2-digit' })
  const fullTime = updated.toLocaleString(i18n.language)

  const isArchived = task.status === 'archived'

  return (
    <div
      className={`${s.item} ${isActive ? s.active : ''}`}
      onClick={onSelect}
    >
      <div className={s.itemContent}>
        {isEditing ? (
          <input
            className={s.renameInput}
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={handleRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRename()
              if (e.key === 'Escape') setIsEditing(false)
            }}
            autoFocus
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <>
            <div className={s.title}>
              {task.title}
              {isRunning && <span className={s.runningDot} title={t('task.running')} />}
              {task.mode === 'general' && (
                <span className={s.modeTag}>{t('task.modeGeneralShort')}</span>
              )}
            </div>
            <div className={s.meta}>
              {t('task.messages', { count: task.message_count ?? 0 })} · <span title={fullTime}>{time}</span>
              {/* 任务累计消耗 + 轮次下钻（2.10 §6.1/线框 4/§5.2；L3 操作驱动：随列表刷新） */}
              <UsageRounds taskId={task.id} usage={task.usage} />
            </div>
          </>
        )}
      </div>
      {!isEditing && (
        <div className={s.menuBtnWrap} onClick={(e) => e.stopPropagation()} ref={menuRef}>
          <button className={s.menuBtn} onClick={() => setMenuOpen(!menuOpen)}><MoreVertical size={15} /></button>
          <div className={`${s.dropdownMenu} ${menuOpen ? s.dropdownMenuOpen : ''}`}>
            {onExport && (
              <button className={s.menuItem} onClick={onExport}>
                {t('task.export')}
              </button>
            )}
            {!isArchived && onArchive && !isRunning && (
              <button className={s.menuItem} onClick={onArchive}>
                {t('task.archive')}
              </button>
            )}
            {isArchived && onUnarchive && (
              <button className={s.menuItem} onClick={onUnarchive}>
                {t('task.unarchive')}
              </button>
            )}
            <button className={s.menuItem} onClick={() => setIsEditing(true)}>
              {t('task.renameBtn')}
            </button>
            {isArchived && (
              <button className={`${s.menuItem} ${s.menuItemDanger}`} onClick={handleDelete}>
                {t('task.deleteBtn')}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
