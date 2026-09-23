import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { MoreVertical } from 'lucide-react'
import type { Task } from '../../types'
import { useConfirmDelete } from '../../hooks/useConfirmDelete'
import s from './TaskItem.module.css'

interface TaskItemProps {
  task: Task
  isActive: boolean
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
  const { confirming, requestDelete, cancel } = useConfirmDelete()

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

  const handleDelete = () => {
    if (requestDelete()) {
      onDelete()
    }
  }

  const time = new Date(task.updated_at).toLocaleTimeString(i18n.language, {
    hour: '2-digit',
    minute: '2-digit',
  })

  const isArchived = task.status === 'archived'

  // Extract last 2 path segments for workspace display
  const workspaceName = task.workspace_path
    ? task.workspace_path.split(/[\\/]/).filter(Boolean).slice(-2).join('/')
    : ''

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
              {task.mode === 'general' && (
                <span className={s.modeTag}>{t('task.modeGeneralShort')}</span>
              )}
            </div>
            <div className={s.meta}>
              {t('task.messages', { count: task.message_count ?? 0 })} · {time}
              {workspaceName && <span> · {workspaceName}</span>}
            </div>
          </>
        )}
      </div>
      {!isEditing && (
        confirming ? (
          <div className={s.inlineConfirm} onClick={(e) => e.stopPropagation()}>
            <span>{t('conversation.confirmDelete')}</span>
            <button className={s.confirmCancelBtn} onClick={(e) => { e.stopPropagation(); cancel() }}>
              {t('conversation.cancel')}
            </button>
            <button className={s.confirmDeleteBtn} onClick={(e) => { e.stopPropagation(); handleDelete() }}>
              {t('conversation.delete')}
            </button>
          </div>
        ) : (
          <div className={s.menuBtnWrap} onClick={(e) => e.stopPropagation()} ref={menuRef}>
            <button className={s.menuBtn} onClick={() => setMenuOpen(!menuOpen)}><MoreVertical size={15} /></button>
            <div className={`${s.dropdownMenu} ${menuOpen ? s.dropdownMenuOpen : ''}`}>
              {onExport && (
                <button className={s.menuItem} onClick={onExport}>
                  {t('task.export')}
                </button>
              )}
              {!isArchived && onArchive && (
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
              <button className={`${s.menuItem} ${s.menuItemDanger}`} onClick={handleDelete}>
                {t('task.deleteBtn')}
              </button>
            </div>
          </div>
        )
      )}
    </div>
  )
}
