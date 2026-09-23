import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { MoreVertical } from 'lucide-react'
import type { Conversation } from '../../types'
import { useConfirmDelete } from '../../hooks/useConfirmDelete'
import s from './ConversationItem.module.css'

interface ConversationItemProps {
  conversation: Conversation
  isActive: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (title: string) => void
}

export function ConversationItem({
  conversation,
  isActive,
  onSelect,
  onDelete,
  onRename,
}: ConversationItemProps) {
  const { t, i18n } = useTranslation()
  const [isEditing, setIsEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(conversation.title)
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
    if (editTitle.trim() && editTitle !== conversation.title) {
      onRename(editTitle.trim())
    }
    setIsEditing(false)
  }

  const handleDelete = () => {
    if (requestDelete()) {
      onDelete()
    }
  }

  const time = new Date(conversation.updated_at).toLocaleTimeString(i18n.language, {
    hour: '2-digit',
    minute: '2-digit',
  })

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
            <div className={s.title}>{conversation.title}</div>
            <div className={s.meta}>
              {t('sidebar.messages', { count: conversation.message_count })} · {time}
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
