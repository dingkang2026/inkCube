/** src/features/knowledge/ProjectList.tsx */
import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Folder, MoreVertical } from 'lucide-react'
import type { Project } from './types'
import s from './ProjectList.module.css'

interface ProjectListProps {
  projects: Project[]
  selectedProject: string | null
  onSelect: (projectId: string) => void
  onDelete: (projectId: string) => void
  onRename?: (projectId: string, name: string) => void
  onCreate?: (name: string, description?: string) => void
}

export function ProjectList({
  projects,
  selectedProject,
  onSelect,
  onDelete,
  onRename,
  onCreate,
}: ProjectListProps) {
  const { t } = useTranslation()
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [newProjectDesc, setNewProjectDesc] = useState('')
  const [creating, setCreating] = useState(false)
  const [confirmingProjectId, setConfirmingProjectId] = useState<string | null>(null)
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpenId) return
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpenId(null)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [menuOpenId])

  const handleDeleteClick = (projectId: string) => {
    if (confirmingProjectId === projectId) {
      // Second click — confirmed
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current)
      setConfirmingProjectId(null)
      onDelete(projectId)
    } else {
      // First click — show confirmation
      setConfirmingProjectId(projectId)
      confirmTimerRef.current = setTimeout(() => setConfirmingProjectId(null), 3000)
    }
  }

  const cancelDelete = () => {
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current)
    setConfirmingProjectId(null)
  }

  const startRename = (project: Project) => {
    setEditingProjectId(project.id)
    setEditName(project.name)
  }

  const handleRename = (projectId: string) => {
    if (editName.trim() && onRename) {
      onRename(projectId, editName.trim())
    }
    setEditingProjectId(null)
  }

  const handleCreate = async () => {
    if (!newProjectName.trim() || !onCreate || creating) return
    setCreating(true)
    try {
      await onCreate(newProjectName.trim(), newProjectDesc.trim() || undefined)
      setNewProjectName('')
      setNewProjectDesc('')
      setShowCreateModal(false)
    } finally {
      setCreating(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && newProjectName.trim()) {
      handleCreate()
    }
    if (e.key === 'Escape') {
      setShowCreateModal(false)
    }
  }

  return (
    <div className={s.container}>
      {/* Create button */}
      {onCreate && (
        <button className={s.createBtn} onClick={() => setShowCreateModal(true)}>
          <span className={s.createIcon}>+</span>
          {t('knowledge.newProject')}
        </button>
      )}

      {/* Project list */}
      {projects.length === 0 ? (
        <div className={s.empty}>
          <div className={s.emptyIcon}><Folder size={28} /></div>
          <p>{t('knowledge.noProjects')}</p>
        </div>
      ) : (
        <div className={s.list}>
          {projects.map((project) => (
            <div
              key={project.id}
              className={`${s.item} ${selectedProject === project.id ? s.selected : ''}`}
              onClick={() => onSelect(project.id)}
            >
              <div className={s.itemContent}>
                {editingProjectId === project.id ? (
                  <input
                    className={s.renameInput}
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onBlur={() => handleRename(project.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleRename(project.id)
                      if (e.key === 'Escape') setEditingProjectId(null)
                    }}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <>
                    <h4 className={s.name}>{project.name}</h4>
                    {project.description && (
                      <p className={s.desc}>{project.description}</p>
                    )}
                    <div className={s.meta}>
                      <span>{project.file_count} {t('knowledge.files')}</span>
                      <span className={s.dot}>·</span>
                      <span>{project.chunk_count} {t('knowledge.chunks')}</span>
                    </div>
                  </>
                )}
              </div>
              {confirmingProjectId === project.id ? (
                <div className={s.inlineConfirm} onClick={(e) => e.stopPropagation()}>
                  <span>{t('knowledge.confirmDeleteProject')}</span>
                  <button className={s.confirmCancelBtn} onClick={(e) => { e.stopPropagation(); cancelDelete() }}>
                    {t('knowledge.cancel')}
                  </button>
                  <button className={s.confirmDeleteBtn} onClick={(e) => { e.stopPropagation(); handleDeleteClick(project.id) }}>
                    {t('knowledge.deleteProject')}
                  </button>
                </div>
              ) : (
                <div className={s.menuBtnWrap} onClick={(e) => e.stopPropagation()} ref={menuRef}>
                  <button className={s.menuBtn} onClick={() => setMenuOpenId(menuOpenId === project.id ? null : project.id)}><MoreVertical size={15} /></button>
                  <div className={`${s.dropdownMenu} ${menuOpenId === project.id ? s.dropdownMenuOpen : ''}`}>
                    <button className={s.menuItem} onClick={() => startRename(project)}>
                      {t('task.renameBtn')}
                    </button>
                    <button className={`${s.menuItem} ${s.menuItemDanger}`} onClick={() => handleDeleteClick(project.id)}>
                      {t('task.deleteBtn')}
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Create modal */}
      {showCreateModal && (
        <div className={s.overlay} onClick={() => setShowCreateModal(false)}>
          <div className={s.modal} onClick={(e) => e.stopPropagation()} onKeyDown={handleKeyDown}>
            <h3 className={s.modalTitle}>{t('knowledge.createProject')}</h3>
            <input
              type="text"
              className={s.input}
              placeholder={t('knowledge.projectName')}
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
              autoFocus
            />
            <input
              type="text"
              className={s.input}
              placeholder={t('knowledge.projectDescription')}
              value={newProjectDesc}
              onChange={(e) => setNewProjectDesc(e.target.value)}
            />
            <div className={s.modalActions}>
              <button className={s.btnCancel} onClick={() => setShowCreateModal(false)}>
                {t('knowledge.cancel')}
              </button>
              <button
                className={s.btnCreate}
                onClick={handleCreate}
                disabled={!newProjectName.trim() || creating}
              >
                {creating ? t('knowledge.creating') : t('knowledge.create')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
