/** src/features/task/TemplateManageModal.tsx — 0.5 E2 T4:任务模板管理弹层（0.5 §4.4/§7.2）。
 *  列表含内置行（仅置顶开关，无编辑/删除）与自定义行（置顶/编辑/删除）；新建/编辑
 *  表单校验 title+prompt 非空（保存键硬门禁 + 失焦提示），删除走 useConfirm。
 *  数据与动作由面板经 props 注入（面板持 hook 单实例），写成功后 refresh 重拉。 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Pin, PinOff, Pencil, Trash2, Plus, X } from 'lucide-react'
import { useConfirm } from '../../components/ConfirmDialog'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'
import { TEMPLATE_CATEGORIES } from './promptTemplate'
import type { MergedTemplate, TemplateCategory, TemplateDraft } from './promptTemplate'
import s from './TemplateManageModal.module.css'

interface TemplateManageModalProps {
  templates: MergedTemplate[]
  refresh: () => void
  save: (draft: TemplateDraft) => Promise<void>
  remove: (id: string) => Promise<void>
  togglePin: (id: string, pinned: boolean) => Promise<void>
  onClose: () => void
}

const FORM_CATEGORIES = TEMPLATE_CATEGORIES.filter((c) => c !== 'all') as TemplateCategory[]

export function TemplateManageModal({
  templates, refresh, save, remove, togglePin, onClose,
}: TemplateManageModalProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  // null=列表态；对象=新建/编辑表单态
  const [draft, setDraft] = useState<TemplateDraft | null>(null)
  const [touched, setTouched] = useState(false)
  const [saving, setSaving] = useState(false)

  const errCode = (err: unknown) => (err as { errorCode?: string })?.errorCode

  const openNew = () => {
    setDraft({ category: 'info', title: '', prompt: '' })
    setTouched(false)
  }

  const openEdit = (m: MergedTemplate) => {
    setDraft({ id: m.id, category: m.category, title: m.title, prompt: m.prompt, pinned: m.pinned })
    setTouched(false)
  }

  const canSave = !!draft && !!draft.title.trim() && !!draft.prompt.trim()

  const handleSave = async () => {
    if (!draft || !canSave || saving) return
    setSaving(true)
    try {
      await save(draft)
      showToast(t('task.tplMgmt.saved'), 'success')
      refresh()
      setDraft(null)
    } catch (err) {
      console.error('Save task template failed:', err)
      showToast(apiErrorText(errCode(err), t('task.tplMgmt.failed'), t), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (m: MergedTemplate) => {
    const ok = await confirmDialog({ message: t('task.tplMgmt.confirmDelete', { title: m.title }) })
    if (!ok) return
    try {
      await remove(m.id)
      showToast(t('task.tplMgmt.deleted'), 'success')
      refresh()
    } catch (err) {
      console.error('Delete task template failed:', err)
      showToast(apiErrorText(errCode(err), t('task.tplMgmt.failed'), t), 'error')
    }
  }

  const handleTogglePin = async (m: MergedTemplate) => {
    try {
      await togglePin(m.id, !m.pinned)
      refresh()
    } catch (err) {
      console.error('Toggle task template pin failed:', err)
      showToast(apiErrorText(errCode(err), t('task.tplMgmt.failed'), t), 'error')
    }
  }

  return (
    <div className={s.backdrop} onClick={onClose}>
      <div className={s.modal} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className={s.header}>
          <h3 className={s.title}>{t('task.tplMgmt.manage')}</h3>
          <div className={s.headerActions}>
            {!draft && (
              <button className={s.newBtn} onClick={openNew}>
                <Plus size={14} />{t('task.tplMgmt.newTpl')}
              </button>
            )}
            <button className={s.iconBtn} aria-label={t('common.close')} onClick={onClose}>
              <X size={16} />
            </button>
          </div>
        </div>

        {draft ? (
          <div className={s.form}>
            <h4 className={s.formTitle}>{draft.id ? t('task.tplMgmt.editTpl') : t('task.tplMgmt.newTpl')}</h4>
            <div className={s.field}>
              <label className={s.label} htmlFor="tpl-category">{t('task.tplMgmt.category')}</label>
              <select
                id="tpl-category"
                className={s.select}
                value={draft.category}
                onChange={(e) => setDraft({ ...draft, category: e.target.value as TemplateCategory })}
              >
                {FORM_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{t(`task.cat.${c}`)}</option>
                ))}
              </select>
            </div>
            <div className={s.field}>
              <label className={s.label} htmlFor="tpl-title">{t('task.tplMgmt.title')}</label>
              <input
                id="tpl-title"
                className={s.input}
                value={draft.title}
                onBlur={() => setTouched(true)}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
              {touched && !draft.title.trim() && (
                <span className={s.fieldError}>{t('task.tplMgmt.titleRequired')}</span>
              )}
            </div>
            <div className={s.field}>
              <label className={s.label} htmlFor="tpl-prompt">{t('task.tplMgmt.prompt')}</label>
              <textarea
                id="tpl-prompt"
                className={s.textarea}
                rows={6}
                value={draft.prompt}
                onBlur={() => setTouched(true)}
                onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
              />
              {touched && !draft.prompt.trim() && (
                <span className={s.fieldError}>{t('task.tplMgmt.promptRequired')}</span>
              )}
              <span className={s.hint}>{t('task.tplMgmt.placeholderHint')}</span>
            </div>
            <div className={s.formActions}>
              <button className={s.cancelBtn} onClick={() => setDraft(null)}>{t('common.cancel')}</button>
              <button className={s.saveBtn} disabled={!canSave || saving} onClick={handleSave}>
                {t('task.tplMgmt.save')}
              </button>
            </div>
          </div>
        ) : (
          <div className={s.list}>
            {templates.map((m) => (
              <div key={m.id} className={s.row}>
                <div className={s.rowMain}>
                  <span className={s.rowTitle}>{m.title}</span>
                  <span className={s.rowCat}>{t(`task.cat.${m.category}`)}</span>
                </div>
                {m.builtin && <span className={s.builtinTag}>{t('task.tplMgmt.builtinReadonly')}</span>}
                <div className={s.rowActions}>
                  <button
                    className={`${s.iconBtn} ${m.pinned ? s.pinOn : ''}`}
                    aria-label={t('task.tplMgmt.pinnedOnly')}
                    title={t('task.tplMgmt.pinnedOnly')}
                    onClick={() => handleTogglePin(m)}
                  >
                    {m.pinned ? <Pin size={15} /> : <PinOff size={15} />}
                  </button>
                  {!m.builtin && (
                    <>
                      <button
                        className={s.iconBtn}
                        aria-label={t('task.tplMgmt.editTpl')}
                        title={t('task.tplMgmt.editTpl')}
                        onClick={() => openEdit(m)}
                      >
                        <Pencil size={15} />
                      </button>
                      <button
                        className={s.iconBtn}
                        aria-label={t('task.tplMgmt.delete')}
                        title={t('task.tplMgmt.delete')}
                        onClick={() => handleDelete(m)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
