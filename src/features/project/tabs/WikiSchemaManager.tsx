/** src/features/project/tabs/WikiSchemaManager.tsx — Wiki 结构页(schema)管理视图
 *
 * 2.6 §3.1/§4.1:schema 定义(page_key/title/compile_hint/base_lib_search_hints/position)
 * 在每次编译时注入两阶段 prompt 作为结构指引。后端 CRUD 端点与 IPC 四通道
 * (wiki:getSchemas/createSchema/updateSchema/deleteSchema)早已预留,本组件为首个前端接入点。
 * 变更经 DB 层 _notify_schema_change 使同类型项目 pending_compile_count+1(首页
 * 「待编译」提示自动出现);已编译页面不回溯,重新编译后按新结构重建。
 */
import { useState, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Plus, Pencil, Trash2, Loader2, AlertCircle, CheckCircle,
  RotateCw, ListOrdered,
} from 'lucide-react'
import s from './WikiTab.module.css'

interface SchemaRow {
  id: string
  project_type: string
  page_key: string
  title: string
  compile_hint: string
  base_lib_search_hints: string[]
  position: number
}

interface SchemaForm {
  page_key: string
  title: string
  compile_hint: string
  hintsText: string
  position: string
}

const EMPTY_FORM: SchemaForm = { page_key: '', title: '', compile_hint: '', hintsText: '', position: '' }
// page_key 同时用于 [[page_key]] 链接语法与 schema_id,限制为安全字符集
const PAGE_KEY_RE = /^[A-Za-z0-9_]+$/

export function WikiSchemaManager({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const [schemas, setSchemas] = useState<SchemaRow[]>([])
  const [projectType, setProjectType] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [editing, setEditing] = useState<'create' | SchemaRow | null>(null)
  const [form, setForm] = useState<SchemaForm>(EMPTY_FORM)
  const [formError, setFormError] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<SchemaRow | null>(null)
  const [savedFlash, setSavedFlash] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(false)
    try {
      const result: any = await window.electronAPI.invoke('wiki:getSchemas', { projectId })
      setSchemas(result.schemas || [])
      setProjectType(result.project_type || '')
    } catch (err) {
      console.error('Failed to load wiki schemas:', err)
      setLoadError(true)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  const startCreate = () => {
    setEditing('create')
    setForm(EMPTY_FORM)
    setFormError('')
  }

  const startEdit = (schema: SchemaRow) => {
    setEditing(schema)
    setForm({
      page_key: schema.page_key,
      title: schema.title,
      compile_hint: schema.compile_hint || '',
      hintsText: (schema.base_lib_search_hints || []).join(', '),
      position: String(schema.position ?? ''),
    })
    setFormError('')
  }

  const cancelEdit = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormError('')
  }

  // 检索词:逗号(中英)/分号/换行分隔,去空
  const parseHints = (text: string) =>
    text.split(/[,，;；\n]/).map((h) => h.trim()).filter(Boolean)

  const save = async () => {
    if (!editing) return
    if (editing === 'create' && !PAGE_KEY_RE.test(form.page_key.trim())) {
      setFormError(t('project.wiki.schemaPageKeyInvalid'))
      return
    }
    if (!form.title.trim()) {
      setFormError(t('project.wiki.schemaTitleRequired'))
      return
    }
    setSaving(true)
    setFormError('')
    try {
      const pos = form.position.trim() === '' ? undefined : Number(form.position.trim())
      const position = Number.isFinite(pos) ? pos : undefined
      if (editing === 'create') {
        await window.electronAPI.invoke('wiki:createSchema', {
          projectId,
          pageKey: form.page_key.trim(),
          title: form.title.trim(),
          compileHint: form.compile_hint,
          baseLibSearchHints: parseHints(form.hintsText),
          position,
        })
      } else {
        await window.electronAPI.invoke('wiki:updateSchema', {
          projectId,
          schemaId: editing.id,
          title: form.title.trim(),
          compileHint: form.compile_hint,
          baseLibSearchHints: parseHints(form.hintsText),
          position,
        })
      }
      setEditing(null)
      setForm(EMPTY_FORM)
      setSavedFlash(true)
      window.setTimeout(() => setSavedFlash(false), 2500)
      await load()
    } catch (err) {
      console.error('Failed to save wiki schema:', err)
      const msg = String((err as Error)?.message || err)
      // 重复 page_key 后端返回 409「already exists」→ 友好提示，其余透出原始信息
      setFormError(
        /already exists/i.test(msg)
          ? t('project.wiki.schemaExists')
          : msg,
      )
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleting) return
    try {
      await window.electronAPI.invoke('wiki:deleteSchema', { projectId, schemaId: deleting.id })
      setDeleting(null)
      await load()
    } catch (err) {
      console.error('Failed to delete wiki schema:', err)
      setDeleting(null)
    }
  }

  if (loading) {
    return (
      <div className={s.schemaLoading}>
        <Loader2 size={20} className={s.spin} />
      </div>
    )
  }

  if (loadError) {
    return (
      <div className={s.schemaLoadError}>
        <AlertCircle size={16} />
        <span>{t('project.wiki.schemaLoadFailed')}</span>
        <button className={s.editBtn} onClick={() => void load()}>
          <RotateCw size={11} />
          {t('knowledge.refresh')}
        </button>
      </div>
    )
  }

  return (
    <div className={s.homePage}>
      <div className={s.homeSection}>
        <div className={s.homeSectionHeader}>
          <div className={s.homeSectionTitle}>
            <ListOrdered size={15} className={s.homeSectionIcon} />
            <h3>{t('project.wiki.schemaTitle')}</h3>
            {projectType && <span className={s.schemaTypeBadge}>{projectType}</span>}
          </div>
          <button
            className={s.schemaAddBtn}
            onClick={startCreate}
            disabled={editing !== null}
            title={t('project.wiki.schemaAdd')}
          >
            <Plus size={13} />
            {t('project.wiki.schemaAdd')}
          </button>
        </div>
        <p className={s.schemaHintLine}>{t('project.wiki.schemaHint')}</p>

        {savedFlash && (
          <div className={s.schemaSavedFlash}>
            <CheckCircle size={13} />
            {t('project.wiki.schemaSaved')}
          </div>
        )}

        {/* Create / edit form (inline card) */}
        {editing && (
          <div className={s.schemaForm}>
            <div className={s.schemaFormTitle}>
              {editing === 'create' ? t('project.wiki.schemaAdd') : t('project.wiki.schemaEdit')}
            </div>
            <div className={s.schemaFormGrid}>
              <label className={s.schemaField}>
                <span className={s.schemaFormLabel}>{t('project.wiki.schemaPageKey')}</span>
                <input
                  className={s.schemaFormInput}
                  value={form.page_key}
                  onChange={(e) => setForm({ ...form, page_key: e.target.value })}
                  placeholder="e.g. enterprise_profile"
                  disabled={editing !== 'create'}
                />
                <span className={s.schemaFieldHint}>{t('project.wiki.schemaPageKeyHint')}</span>
              </label>
              <label className={s.schemaField}>
                <span className={s.schemaFormLabel}>{t('project.wiki.schemaTitleLabel')}</span>
                <input
                  className={s.schemaFormInput}
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </label>
              <label className={s.schemaField}>
                <span className={s.schemaFormLabel}>{t('project.wiki.schemaPosition')}</span>
                <input
                  className={s.schemaFormInput}
                  type="number"
                  value={form.position}
                  onChange={(e) => setForm({ ...form, position: e.target.value })}
                  placeholder={t('project.wiki.schemaPositionAuto')}
                />
              </label>
              <label className={s.schemaField}>
                <span className={s.schemaFormLabel}>{t('project.wiki.schemaBaseLibHints')}</span>
                <input
                  className={s.schemaFormInput}
                  value={form.hintsText}
                  onChange={(e) => setForm({ ...form, hintsText: e.target.value })}
                  placeholder={t('project.wiki.schemaBaseLibHintsPlaceholder')}
                />
              </label>
              <label className={`${s.schemaField} ${s.schemaFieldWide}`}>
                <span className={s.schemaFormLabel}>{t('project.wiki.schemaCompileHint')}</span>
                <textarea
                  className={s.schemaFormTextarea}
                  rows={4}
                  value={form.compile_hint}
                  onChange={(e) => setForm({ ...form, compile_hint: e.target.value })}
                  placeholder={t('project.wiki.schemaCompileHintPlaceholder')}
                />
              </label>
            </div>
            {formError && (
              <div className={s.schemaFormError}>
                <AlertCircle size={12} />
                {formError}
              </div>
            )}
            <div className={s.editActions}>
              <button className={s.editCancelBtn} onClick={cancelEdit} disabled={saving}>
                {t('project.wiki.cancel')}
              </button>
              <button className={s.editSaveBtn} onClick={() => void save()} disabled={saving}>
                {saving && <Loader2 size={13} className={s.spin} />}
                {t('project.wiki.save')}
              </button>
            </div>
          </div>
        )}

        {schemas.length === 0 ? (
          <div className={s.schemaEmptyState}>{t('project.wiki.schemaEmpty')}</div>
        ) : (
          <div className={s.schemaList}>
            {schemas.map((schema) => (
              <div key={schema.id} className={s.schemaItem}>
                {deleting?.id === schema.id ? (
                  <div className={s.schemaDeleteBar}>
                    <AlertCircle size={13} />
                    <span className={s.schemaDeleteText}>
                      {t('project.wiki.schemaDeleteConfirm', { title: schema.title })}
                    </span>
                    <button className={s.schemaBtnDanger} onClick={() => void confirmDelete()}>
                      {t('common.confirm')}
                    </button>
                    <button className={s.editCancelBtn} onClick={() => setDeleting(null)}>
                      {t('common.cancel')}
                    </button>
                  </div>
                ) : (
                  <>
                    <div className={s.schemaItemHeader}>
                      <span className={s.schemaPos}>#{schema.position}</span>
                      <span className={s.schemaItemTitle}>{schema.title}</span>
                      <code className={s.schemaKey}>{schema.page_key}</code>
                      <div className={s.schemaItemActions}>
                        <button
                          className={s.editBtn}
                          onClick={() => startEdit(schema)}
                          disabled={editing !== null}
                        >
                          <Pencil size={11} />
                          {t('project.wiki.edit')}
                        </button>
                        <button
                          className={s.schemaBtnGhostDanger}
                          onClick={() => setDeleting(schema)}
                          disabled={editing !== null}
                        >
                          <Trash2 size={11} />
                          {t('project.wiki.schemaDelete')}
                        </button>
                      </div>
                    </div>
                    {schema.compile_hint && (
                      <div className={s.schemaItemHint}>{schema.compile_hint}</div>
                    )}
                    {(schema.base_lib_search_hints?.length ?? 0) > 0 && (
                      <div className={s.schemaHints}>
                        {schema.base_lib_search_hints.map((h) => (
                          <span key={h} className={s.schemaHintTag}>{h}</span>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
