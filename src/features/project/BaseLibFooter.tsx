/**
 * src/features/project/BaseLibFooter.tsx — Sidebar base library list (API-driven).
 * 自定义基础库 CRUD（2.1 §7 / 2.7 §7 重建）：header「+」新建；自定义库行 hover
 * 行内编辑/删除（预置库受保护，无操作入口）；创建/编辑共用表单弹窗，删除独立
 * 确认弹窗（含影响说明）。后端守门：预置 403 / 有在途活动 409，code 经
 * structuredPythonResponse 返回值透传（throw 型跨进程丢 errorCode，Electron
 * #24427），判 res.ok 后 apiErrorText 映射双语文案。
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Factory, DollarSign, Library, FileText, Plus, Pencil, Trash2, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useActivity } from '../activity/ActivityContext'
import type { ProjectActivity } from '../activity/types'
import { usePagedList } from '../../hooks/usePagedList'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'
import s from './BaseLibFooter.module.css'

interface BaseLibRecord {
  id: string
  name: string
  description: string
  lib_type: string | null
  file_count: number
}

/** Icon mapping for known preset base lib IDs */
const PRESET_ICONS: Record<string, LucideIcon> = {
  'bl-recruitment': Factory,
  'bl-investment': DollarSign,
}

function isPreset(libType: string | null): boolean {
  return !!libType && libType.startsWith('preset_')
}

function getIcon(id: string): LucideIcon {
  return PRESET_ICONS[id] || Library
}

interface BaseLibFooterProps {
  selectedBaseLib: string | null
  onSelectBaseLib: (baseLibId: string) => void
  onOpenTemplateManagement?: () => void
  /** Whether the template management page is currently shown (peer highlight) */
  templateManagementActive?: boolean
  /** 删除成功且删的是当前选中库时回调（App 清选中态，避免悬空详情页） */
  onBaseLibDeleted?: (baseLibId: string) => void
}

/** 弹窗态：create / edit / delete（携带库快照）/ 关闭 */
type ModalState =
  | { mode: 'create' }
  | { mode: 'edit'; lib: BaseLibRecord }
  | { mode: 'delete'; lib: BaseLibRecord }
  | null

export function BaseLibFooter({ selectedBaseLib, onSelectBaseLib, onOpenTemplateManagement, templateManagementActive, onBaseLibDeleted }: BaseLibFooterProps) {
  const { t } = useTranslation()
  const { byProject } = useActivity()
  const { isActive: formTplAnalyzing, items: formTplItems } = useActivity('form_template')
  // PPT 模板任务活跃态（2.3 §4.2）：container='templates' 的 ppt 活动（模板工坊），
  // awaiting_input 门禁待确认用醒目色点，running 用中性活跃点（同基础库行视觉）
  const { items: pptItems } = useActivity('ppt')
  const tplAwaiting = pptItems.filter((a) => a.container === 'templates' && a.status === 'awaiting_input')
  const tplRunning = pptItems.filter((a) => a.container === 'templates' && a.status !== 'awaiting_input')
  const showToast = useToast()
  // 列表分页（0.1dev-guide 统一约定）：50/页「加载更多」，挂载即拉
  const {
    items: baseLibs, hasMore, loadingMore, loading, error: libsError, loadMore, reload,
  } = usePagedList<BaseLibRecord>({ channel: 'baseLib:list', itemsKey: 'base_libs' })
  // 列表加载失败提示（对齐 FormsTab m-3：静默吞掉会让列表空白且无任何提示）
  useEffect(() => {
    if (libsError) showToast(`${t('common.listLoadFailed')}: ${libsError}`, 'error')
  }, [libsError, showToast, t])

  // ── 自定义库 CRUD（2.1 §7）：create/update 共用表单弹窗，delete 独立确认 ──
  const [modal, setModal] = useState<ModalState>(null)
  const [submitting, setSubmitting] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const openCreate = () => {
    setName('')
    setDescription('')
    setModal({ mode: 'create' })
  }

  const openEdit = (lib: BaseLibRecord) => {
    setName(lib.name)
    setDescription(lib.description || '')
    setModal({ mode: 'edit', lib })
  }

  const closeModal = () => {
    if (!submitting) setModal(null)
  }

  const submitForm = async () => {
    const trimmed = name.trim()
    if (!trimmed || submitting || !modal || modal.mode === 'delete') return
    setSubmitting(true)
    try {
      // 守门 code（400/403）经 structuredPythonResponse 返回值透传，判 res.ok 后
      // 映射双语文案（throw 型跨进程丢 errorCode，Electron #24427）
      const res = (await (
        modal.mode === 'create'
          ? window.electronAPI.invoke('baseLib:create', {
              name: trimmed, description: description.trim(),
            })
          : window.electronAPI.invoke('baseLib:update', {
              libId: modal.lib.id, name: trimmed, description: description.trim(),
            })
      )) as { ok?: boolean; code?: string }
      if (!res?.ok) {
        showToast(apiErrorText(res?.code, t('baseLib.saveFailed'), t), 'error')
        return
      }
      showToast(t(
        modal.mode === 'create' ? 'baseLib.createSuccess' : 'baseLib.updateSuccess',
        { name: trimmed },
      ), 'success')
      setModal(null)
      void reload()
    } catch {
      // 网络/超时等非结构化失败：无 code，走通用文案（弹窗保留可重试）
      showToast(apiErrorText(undefined, t('baseLib.saveFailed'), t), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const confirmDelete = async () => {
    if (!modal || modal.mode !== 'delete' || submitting) return
    const lib = modal.lib
    setSubmitting(true)
    try {
      // 409 在途活动 / 403 预置保护经返回值 code 透传，映射双语文案
      const res = (await window.electronAPI.invoke('baseLib:delete', { libId: lib.id })) as { ok?: boolean; code?: string }
      if (!res?.ok) {
        showToast(apiErrorText(res?.code, t('baseLib.deleteFailed'), t), 'error')
        return
      }
      showToast(t('baseLib.deleteSuccess', { name: lib.name }), 'success')
      setModal(null)
      void reload()
      // 删的是当前选中库 → 通知 App 清选中态（详情页退出，避免悬空）
      if (selectedBaseLib === lib.id) onBaseLibDeleted?.(lib.id)
    } catch {
      showToast(apiErrorText(undefined, t('baseLib.deleteFailed'), t), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  /** 活跃点 tooltip（0.4 §5.7）：与项目行对齐，复用 ProjectList 同一组 i18n */
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
    <div className={s.footer}>
      <div className={s.header}>
        <span className={s.title}>{t('baseLib.title')}</span>
        <button
          type="button"
          className={s.addBtn}
          title={t('baseLib.create')}
          aria-label={t('baseLib.create')}
          data-testid="baselib-create-btn"
          onClick={openCreate}
        >
          <Plus size={13} />
        </button>
      </div>
      <div className={s.list}>
        {!loading && baseLibs.map((lib) => {
          const Icon = getIcon(lib.id)
          const preset = isPreset(lib.lib_type)
          // 活跃点与项目行对齐（0.4 §5.7）：全局心跳 byProject 派生，仅基础库容器行先小动作
          const pa = byProject.get(lib.id)
          const hasOngoing = !!pa && pa.container === 'baselib' && pa.files + pa.compiles + pa.reports + pa.forms + pa.ppts > 0
          return (
            <div
              key={lib.id}
              className={`${s.item} ${selectedBaseLib === lib.id ? s.itemSelected : ''}`}
              onClick={() => onSelectBaseLib(lib.id)}
            >
              <Icon size={15} className={s.icon} />
              <span className={s.name}>{lib.name}</span>
              {hasOngoing && pa && (
                <span
                  className={s.activityDot}
                  title={activityTooltip(pa)}
                />
              )}
              {preset ? (
                <span className={s.badge}>{t('baseLib.preset')}</span>
              ) : (
                <span className={s.rowActions}>
                  <button
                    type="button"
                    className={s.rowActionBtn}
                    title={t('common.edit')}
                    aria-label={`${t('common.edit')} ${lib.name}`}
                    data-testid={`baselib-edit-${lib.id}`}
                    onClick={(e) => { e.stopPropagation(); openEdit(lib) }}
                  >
                    <Pencil size={12} />
                  </button>
                  <button
                    type="button"
                    className={`${s.rowActionBtn} ${s.rowActionDanger}`}
                    title={t('common.delete')}
                    aria-label={`${t('common.delete')} ${lib.name}`}
                    data-testid={`baselib-delete-${lib.id}`}
                    onClick={(e) => { e.stopPropagation(); setModal({ mode: 'delete', lib }) }}
                  >
                    <Trash2 size={12} />
                  </button>
                </span>
              )}
            </div>
          )
        })}
        {hasMore && (
          <button
            className={s.loadMoreBtn}
            onClick={() => { void loadMore() }}
            disabled={loadingMore}
          >
            {loadingMore ? t('common.loading') : t('common.loadMore')}
          </button>
        )}
      </div>
      {onOpenTemplateManagement && (
        <>
          <div className={s.header} style={{ marginTop: '8px' }}>
            <span className={s.title}>{t('baseLib.templateManagement')}</span>
          </div>
          <div
            className={`${s.item} ${templateManagementActive ? s.itemSelected : ''}`}
            onClick={onOpenTemplateManagement}
            style={{ cursor: 'pointer', paddingLeft: '18px' }}
          >
            <FileText size={15} className={s.icon} />
            <span className={s.name}>{t('project.report.templateManagement')}</span>
            {formTplAnalyzing && (
              <span
                className={s.activityDot}
                title={t('activity.tipFormTemplates', { count: formTplItems.length })}
              />
            )}
            {tplAwaiting.length > 0 ? (
              <span
                className={`${s.activityDot} ${s.dotAlert}`}
                title={t('activity.tipPptTemplatesAwaiting', { count: tplAwaiting.length })}
              />
            ) : tplRunning.length > 0 ? (
              <span
                className={s.activityDot}
                title={t('activity.tipPptTemplates', { count: tplRunning.length })}
              />
            ) : null}
          </div>
        </>
      )}

      {modal && modal.mode !== 'delete' && (
        <div className={s.backdrop} onClick={closeModal}>
          <div className={s.dialog} role="dialog" aria-modal="true" data-testid="baselib-form-modal" onClick={(e) => e.stopPropagation()}>
            <div className={s.dialogHead}>
              <h3 className={s.dialogTitle}>
                {modal.mode === 'create' ? t('baseLib.create') : t('baseLib.editTitle')}
              </h3>
              <button type="button" className={s.closeBtn} aria-label={t('common.close')} disabled={submitting} onClick={closeModal}>
                <X size={14} />
              </button>
            </div>
            <div className={s.field}>
              <label className={s.label} htmlFor="baselib-name">{t('baseLib.nameLabel')}</label>
              <input
                id="baselib-name"
                className={s.input}
                value={name}
                maxLength={80}
                autoFocus
                placeholder={t('baseLib.namePlaceholder')}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void submitForm() }}
              />
            </div>
            <div className={s.field}>
              <label className={s.label} htmlFor="baselib-desc">{t('baseLib.descLabel')}</label>
              <textarea
                id="baselib-desc"
                className={s.textarea}
                value={description}
                rows={3}
                maxLength={200}
                placeholder={t('baseLib.descPlaceholder')}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            {modal.mode === 'create' && <p className={s.hint}>{t('baseLib.createHint')}</p>}
            <div className={s.actions}>
              <button type="button" className={s.secondaryBtn} disabled={submitting} onClick={closeModal}>
                {t('common.cancel')}
              </button>
              <button
                type="button"
                className={s.primaryBtn}
                data-testid="baselib-form-submit"
                disabled={!name.trim() || submitting}
                onClick={() => void submitForm()}
              >
                {submitting ? t('baseLib.saving') : t('baseLib.save')}
              </button>
            </div>
          </div>
        </div>
      )}

      {modal && modal.mode === 'delete' && (
        <div className={s.backdrop} onClick={closeModal}>
          <div className={s.dialog} role="dialog" aria-modal="true" data-testid="baselib-delete-modal" onClick={(e) => e.stopPropagation()}>
            <div className={s.dialogHead}>
              <h3 className={s.dialogTitle}>{t('baseLib.deleteTitle')}</h3>
              <button type="button" className={s.closeBtn} aria-label={t('common.close')} disabled={submitting} onClick={closeModal}>
                <X size={14} />
              </button>
            </div>
            <div className={s.deleteBody}>
              <p className={s.deleteName}>{modal.lib.name}</p>
              <p className={s.deleteImpact}>{t('baseLib.deleteImpact')}</p>
            </div>
            <div className={s.actions}>
              <button type="button" className={s.secondaryBtn} disabled={submitting} onClick={closeModal}>
                {t('common.cancel')}
              </button>
              <button
                type="button"
                className={`${s.primaryBtn} ${s.dangerBtn}`}
                data-testid="baselib-delete-confirm"
                disabled={submitting}
                onClick={() => void confirmDelete()}
              >
                {submitting ? t('baseLib.deleting') : t('common.delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
