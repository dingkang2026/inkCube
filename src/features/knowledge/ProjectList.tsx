/** src/features/knowledge/ProjectList.tsx */
import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Folder, Loader2, MoreVertical, Upload } from 'lucide-react'
import type { Project, CreateProjectFn } from './types'
import type { ProjectType } from '../project/types'
import { PROJECT_TYPE_CONFIG } from '../project/types'
import { useActivity } from '../activity/ActivityContext'
import type { ProjectActivity } from '../activity/types'
import { HealthDot } from '../project/affairs/HealthDot'
import type { ProjectHealthSummary } from '../project/affairs/types'
import { UsageText } from '../usage/UsageText'
import s from './ProjectList.module.css'

interface ProjectListProps {
  projects: Project[]
  selectedProject: string | null
  onSelect: (projectId: string) => void
  onDelete: (projectId: string) => void
  onRename?: (projectId: string, name: string) => void
  /** 导出项目（2.9 §8）：弹保存框 → 同步导出，完成后 toast */
  onExportProject?: (projectId: string, projectName: string) => void
  /** 导入项目包（2.9 §8）：弹打开框 → 后台导入，进度走活动心跳 */
  onImportProject?: () => void
  onCreate?: CreateProjectFn
  /** 3.0 三期:per 项目健康概要(色点),App 级快照下发;未接/降级时不渲染点 */
  healthData?: Record<string, ProjectHealthSummary>
  /** 归档(2.1 §归档):当前 tab(App 持有,列表内容已按 tab 分发) */
  activeTab: 'active' | 'archived'
  onTabChange: (tab: 'active' | 'archived') => void
  archivedCount: number
  /** 列表分页（0.1dev-guide 统一约定）：当前 tab 的加载更多状态与回调 */
  hasMoreProjects?: boolean
  loadingMoreProjects?: boolean
  onLoadMoreProjects?: () => void
  onArchiveProject: (projectId: string) => void
  onUnarchiveProject: (projectId: string) => void
  /** 0.5 E1 T4:首页「新建项目」信号:递增时自动打开创建模态(纯前端信号,无新 IPC) */
  createSignal?: number
  onCreateSignalConsumed?: () => void
}

export function ProjectList({
  projects,
  selectedProject,
  onSelect,
  onDelete,
  onRename,
  onExportProject,
  onImportProject,
  onCreate,
  healthData,
  activeTab,
  onTabChange,
  archivedCount,
  hasMoreProjects,
  loadingMoreProjects,
  onLoadMoreProjects,
  onArchiveProject,
  onUnarchiveProject,
  createSignal,
  onCreateSignalConsumed,
}: ProjectListProps) {
  const { t } = useTranslation()
  const { byProject } = useActivity()
  // 导出/导入活动态（2.9 §8.2）：导出中菜单禁用 + 条目 spinner、导入中按钮进度态；
  // 前端禁用 = 幂等防重第一层，后端 409 为纵深防御
  const { items: exportItems } = useActivity('project_export')
  const exportingIds = new Set(exportItems.map((a) => a.project_id))
  const { items: importItems, isActive: importActive } = useActivity('project_import')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [newProjectDesc, setNewProjectDesc] = useState('')
  const [newProjectType, setNewProjectType] = useState<ProjectType>('general')
  const [newCompanyName, setNewCompanyName] = useState('')
  const [newCreditCode, setNewCreditCode] = useState('')
  const [creating, setCreating] = useState(false)
  // 在途创建代际:取消按钮中途关闭后,晚到的成功结果必须丢弃,否则 confirmInfo 残留,
  // 下次打开 modal 直接渲染陈旧确认态(误导航到旧项目)(3.0 二期 Task 13 review M1;
  // 表单模态 Esc/点蒙板不关闭防丢输入,0.3 §5.2——中途关闭唯一途径是取消/确认按钮)
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

  // 0.5 E1 T4:首页「新建项目」信号 → 自动打开创建模态；消费即复位（经回调）——
  // 否则 ProjectSidebar 条件渲染反复卸载重挂时，旧信号被当作新边沿重新消费，模态无故弹出（E1 review M1）
  useEffect(() => {
    if (createSignal) {
      setShowCreateModal(true)
      onCreateSignalConsumed?.()
    }
  }, [createSignal, onCreateSignalConsumed])

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
      if (session !== createSessionRef.current) return   // 在途期间已被取消关闭,丢弃陈旧结果(M1)
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

  // 表单模态：Enter = 创建；Esc/点蒙板不关闭（防丢输入，0.3 §5.2），仅按钮关闭
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && newProjectName.trim() && !confirmInfo) {
      handleCreate()
    }
  }

  /** Activity Hub L2 活跃点 tooltip（0.4 §5.3）：拼接待办活动摘要 */
  const activityTooltip = (pa: ProjectActivity): string => {
    const parts: string[] = []
    if (pa.files > 0) parts.push(t('activity.tipFiles', { count: pa.files }))
    if (pa.compiles > 0) parts.push(t('activity.tipCompiles'))
    if (pa.reports > 0) parts.push(t('activity.tipReports', { count: pa.reports }))
    if (pa.forms > 0) parts.push(t('activity.tipForms', { count: pa.forms }))
    if (pa.ppts > 0) parts.push(t('activity.tipPpts', { count: pa.ppts }))
    return parts.join(' / ')
  }

  return (
    <div className={s.container}>
      {/* Create + Import（视觉沿用 TaskList newBtnRow：accent 虚线主钮 + 右侧小图标钮） */}
      {(onCreate || onImportProject) && (
        <div className={s.newBtnRow}>
          {onCreate && (
            <button className={s.createBtn} onClick={() => setShowCreateModal(true)}>
              <span className={s.createIcon}>+</span>
              {t('knowledge.newProject')}
            </button>
          )}
          {onImportProject && (
            <button
              className={s.importBtn}
              onClick={onImportProject}
              disabled={importActive}
              title={importActive
                ? `${t('knowledge.importing')} ${importItems[0]?.progress ?? 0}%`
                : t('knowledge.importProject')}
            >
              {importActive
                ? <Loader2 size={16} className={s.spinning} aria-hidden />
                : <Upload size={16} />}
            </button>
          )}
        </div>
      )}

      {/* 有效/归档 tab（2.1 §归档；视觉沿用 TaskList tab） */}
      <div className={s.tabs}>
        <button
          className={`${s.tab} ${activeTab === 'active' ? s.tabActive : ''}`}
          onClick={() => onTabChange('active')}
        >
          {t('projectArchive.tabActive')}
        </button>
        <button
          className={`${s.tab} ${activeTab === 'archived' ? s.tabActive : ''}`}
          onClick={() => onTabChange('archived')}
        >
          {t('projectArchive.tabArchived')}
          {archivedCount > 0 && <span className={s.badge}>{archivedCount}</span>}
        </button>
      </div>

      {/* Project list */}
      {projects.length === 0 ? (
        <div className={s.empty}>
          <div className={s.emptyIcon}><Folder size={28} /></div>
          <p>{activeTab === 'archived' ? t('projectArchive.empty') : t('knowledge.noProjects')}</p>
        </div>
      ) : (
        <div className={s.list}>
          {projects.map((project) => {
            const archived = activeTab === 'archived'
            const pa = byProject.get(project.id)
            const hasOngoing = !!pa && pa.files + pa.compiles + pa.reports + pa.forms + pa.ppts > 0
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
                      {archived ? (
                        <span>{t('projectArchive.archivedAtMeta', { date: (project.archived_at || '').slice(0, 10) })}</span>
                      ) : (
                        <>
                          <span>{project.file_count} {t('knowledge.files')}</span>
                          <span className={s.dot}>·</span>
                          <span>{project.chunk_count} {t('knowledge.chunks')}</span>
                          {/* 项目累计消耗（2.10 §6.1/线框 4，多项目横向对比） */}
                          {project.usage?.calls ? (<> · <UsageText usage={project.usage} /></>) : null}
                        </>
                      )}
                    </div>
                  </>
                )}
              </div>
              <div className={s.menuBtnWrap} onClick={(e) => e.stopPropagation()} ref={menuRef}>
                <button
                  className={s.menuBtn}
                  aria-label={t('knowledge.projectActions')}
                  onClick={() => setMenuOpenId(menuOpenId === project.id ? null : project.id)}
                >
                  {exportingIds.has(project.id)
                    ? <Loader2 size={15} className={s.spinning} aria-hidden />
                    : <MoreVertical size={15} />}
                </button>
                <div className={`${s.dropdownMenu} ${menuOpenId === project.id ? s.dropdownMenuOpen : ''}`}>
                  {archived ? (
                    /* 归档 tab：恢复 + 删除（删除准入 = 已归档，入口仅在此 tab） */
                    <>
                      <button className={s.menuItem} onClick={() => { setMenuOpenId(null); onUnarchiveProject(project.id) }}>
                        {t('projectArchive.restoreMenu')}
                      </button>
                      <button className={`${s.menuItem} ${s.menuItemDanger}`} onClick={() => handleDelete(project.id)}>
                        {t('task.deleteBtn')}
                      </button>
                    </>
                  ) : (
                    /* 有效 tab：导出 + 重命名 + 归档（删除不可达，先归档再删除） */
                    <>
                      {onExportProject && (
                        <button
                          className={s.menuItem}
                          disabled={exportingIds.has(project.id)}
                          title={exportingIds.has(project.id) ? t('knowledge.exporting') : undefined}
                          onClick={() => {
                            setMenuOpenId(null)
                            onExportProject(project.id, project.name)
                          }}
                        >
                          {t('knowledge.exportProject')}
                        </button>
                      )}
                      <button className={s.menuItem} onClick={() => startRename(project)}>
                        {t('task.renameBtn')}
                      </button>
                      <button className={s.menuItem} onClick={() => { setMenuOpenId(null); onArchiveProject(project.id) }}>
                        {t('projectArchive.archiveMenu')}
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
            )
          })}
          {hasMoreProjects && (
            <button
              className={s.loadMoreBtn}
              onClick={onLoadMoreProjects}
              disabled={loadingMoreProjects}
            >
              {loadingMoreProjects ? t('common.loading') : t('common.loadMore')}
            </button>
          )}
        </div>
      )}

      {/* Create modal */}
      {showCreateModal && (
        <div className={s.overlay}>
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
