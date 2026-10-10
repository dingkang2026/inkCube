/** src/features/knowledge/ProjectList.tsx */
import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Folder, MoreVertical } from 'lucide-react'
import type { Project, CreateProjectFn } from './types'
import type { ProjectType } from '../project/types'
import { PROJECT_TYPE_CONFIG } from '../project/types'
import { useActivity } from '../activity/ActivityContext'
import type { ProjectActivity } from '../activity/types'
import { HealthDot } from '../project/affairs/HealthDot'
import type { ProjectHealthSummary } from '../project/affairs/types'
import s from './ProjectList.module.css'

interface ProjectListProps {
  projects: Project[]
  selectedProject: string | null
  onSelect: (projectId: string) => void
  onDelete: (projectId: string) => void
  onRename?: (projectId: string, name: string) => void
  onCreate?: CreateProjectFn
  /** 3.0 三期:per 项目健康概要(色点),App 级快照下发;未接/降级时不渲染点 */
  healthData?: Record<string, ProjectHealthSummary>
}

export function ProjectList({
  projects,
  selectedProject,
  onSelect,
  onDelete,
  onRename,
  onCreate,
  healthData,
}: ProjectListProps) {
  const { t } = useTranslation()
  const { byProject } = useActivity()
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [newProjectDesc, setNewProjectDesc] = useState('')
  const [newProjectType, setNewProjectType] = useState<ProjectType>('general')
  const [newCompanyName, setNewCompanyName] = useState('')
  const [newCreditCode, setNewCreditCode] = useState('')
  const [creating, setCreating] = useState(false)
  // 在途创建代际:Escape/overlay 中途关闭后,晚到的成功结果必须丢弃,否则 confirmInfo 残留,
  // 下次打开 modal 直接渲染陈旧确认态(误导航到旧项目)(3.0 二期 Task 13 review M1)
  const createSessionRef = useRef(0)
  // 裁决 8:创建成功不关 modal,切确认态展示铺入结果(Adjust → 事务 Tab)
  const [confirmInfo, setConfirmInfo] = useState<{ projectId: string; name: string; type: ProjectType; count: number } | null>(null)
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

  // 删除确认弹框由 App.handleDeleteProject 统一弹出（全局唯一确认层）
  const handleDelete = (projectId: string) => {
    setMenuOpenId(null)
    onDelete(projectId)
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

  const resetAndClose = () => {
    createSessionRef.current++   // 使在途创建的晚到结果失效(同上 M1)
    setNewProjectName('')
    setNewProjectDesc('')
    setNewProjectType('general')
    setNewCompanyName('')
    setNewCreditCode('')
    setConfirmInfo(null)
    setShowCreateModal(false)
  }

  const handleCreate = async () => {
    if (!newProjectName.trim() || !onCreate || creating) return
    setCreating(true)
    const session = ++createSessionRef.current
    try {
      const result = await onCreate(newProjectName.trim(), newProjectDesc.trim() || undefined, newProjectType, newCompanyName.trim() || undefined, newCreditCode.trim() || undefined)
      if (session !== createSessionRef.current) return   // 在途期间已被 Escape/overlay 关闭,丢弃陈旧结果(M1)
      if (result?.id) {
        // 裁决 8:创建成功不关 modal,切确认态(App 已 auto-select 项目)
        setConfirmInfo({ projectId: result.id, name: newProjectName.trim(), type: newProjectType, count: result.seeded_tasks_count ?? 0 })
      } else {
        resetAndClose()
      }
    } finally {
      setCreating(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && newProjectName.trim() && !confirmInfo) {
      handleCreate()
    }
    if (e.key === 'Escape') {
      resetAndClose()   // 确认态下关闭也清残留
    }
  }

  /** Activity Hub L2 活跃点 tooltip（0.4 §5.3）：拼接待办活动摘要 */
  const activityTooltip = (pa: ProjectActivity): string => {
    const parts: string[] = []
    if (pa.files > 0) parts.push(t('activity.tipFiles', { count: pa.files }))
    if (pa.compiles > 0) parts.push(t('activity.tipCompiles'))
    if (pa.reports > 0) parts.push(t('activity.tipReports', { count: pa.reports }))
    return parts.join(' / ')
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
          {projects.map((project) => {
            const pa = byProject.get(project.id)
            const hasOngoing = !!pa && pa.files + pa.compiles + pa.reports > 0
            return (
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
                    <h4 className={s.name}>
                      {healthData?.[project.id] && (
                        <HealthDot summary={healthData[project.id]} className={s.healthDotGap} />
                      )}
                      {project.name}
                      {hasOngoing && pa && (
                        <span
                          className={s.activityDot}
                          title={activityTooltip(pa)}
                        />
                      )}
                    </h4>
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
              <div className={s.menuBtnWrap} onClick={(e) => e.stopPropagation()} ref={menuRef}>
                <button className={s.menuBtn} onClick={() => setMenuOpenId(menuOpenId === project.id ? null : project.id)}><MoreVertical size={15} /></button>
                <div className={`${s.dropdownMenu} ${menuOpenId === project.id ? s.dropdownMenuOpen : ''}`}>
                  <button className={s.menuItem} onClick={() => startRename(project)}>
                    {t('task.renameBtn')}
                  </button>
                  <button className={`${s.menuItem} ${s.menuItemDanger}`} onClick={() => handleDelete(project.id)}>
                    {t('task.deleteBtn')}
                  </button>
                </div>
              </div>
            </div>
            )
          })}
        </div>
      )}

      {/* Create modal */}
      {showCreateModal && (
        <div className={s.overlay} onClick={resetAndClose}>
          <div className={s.modal} onClick={(e) => e.stopPropagation()} onKeyDown={handleKeyDown}>
            {confirmInfo ? (
              <>
                <h3 className={s.modalTitle}>{t('knowledge.wizardConfirmTitle')}</h3>
                <p className={s.confirmDesc}>
                  {t('knowledge.wizardConfirmDesc', {
                    name: confirmInfo.name,
                    count: confirmInfo.count,
                    type: t(PROJECT_TYPE_CONFIG[confirmInfo.type].labelKey),
                  })}
                </p>
                <div className={s.modalActions}>
                  <button className={s.btnCancel} onClick={resetAndClose}>
                    {t('knowledge.wizardConfirmEnter')}
                  </button>
                  <button className={s.btnCreate} onClick={() => {
                    const { projectId } = confirmInfo
                    resetAndClose()
                    window.dispatchEvent(new CustomEvent('app:navigate-project-tab', { detail: { projectId, tab: 'affairs' } }))
                  }}>
                    {t('knowledge.wizardConfirmAdjust')}
                  </button>
                </div>
              </>
            ) : (
              <>
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
            <select
              className={s.input}
              value={newProjectType}
              onChange={(e) => setNewProjectType(e.target.value as ProjectType)}
            >
              {(Object.entries(PROJECT_TYPE_CONFIG) as [ProjectType, { labelKey: string }][]).map(([key, cfg]) => (
                <option key={key} value={key}>{t(cfg.labelKey)}</option>
              ))}
            </select>
            <input
              type="text"
              className={s.input}
              placeholder={t('knowledge.companyName')}
              value={newCompanyName}
              onChange={(e) => setNewCompanyName(e.target.value)}
            />
            <input
              type="text"
              className={s.input}
              placeholder={t('knowledge.creditCode')}
              value={newCreditCode}
              onChange={(e) => setNewCreditCode(e.target.value)}
            />
            <div className={s.modalActions}>
              <button className={s.btnCancel} onClick={resetAndClose}>
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
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
