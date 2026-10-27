/** src/features/project/tabs/WikiSchemaManager.tsx — 知识框架视图：结构页(schema)管理 + Wiki 目标
 *
 * 2.6 §3.1/§4.1:schema 定义(page_key/title/compile_hint/base_lib_search_hints/position)
 * 在每次编译时注入两阶段 prompt 作为结构指引。后端 CRUD 端点与 IPC 四通道
 * (wiki:getSchemas/createSchema/updateSchema/deleteSchema)早已预留,本组件为首个前端接入点。
 * 变更经 DB 层 _notify_schema_change 使同类型项目 pending_compile_count+1(首页
 * 「待编译」提示自动出现）;已编译页面不回溯，重新编译后按新结构重建。
 * 「Wiki 目标」区块自 WikiHomePage 迁入（2026-10-26 二次收敛）：目标属于框架定义层，
 * 与结构页管理同视图——编辑经 wiki:updatePurpose，保存后经 onMetaSaved 回调父级刷新回流。
 */
import { useState, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Plus, Pencil, Trash2, Loader2, AlertCircle, CheckCircle,
  RotateCw, ListOrdered, Target, Edit3, Check, X,
} from 'lucide-react'
import { Markdown } from '../../../components/Markdown'
import { useToast } from '../../knowledge/Toast'
import s from './WikiTab.module.css'

interface SchemaRow {
  id: string
  project_type: string
  page_key: string
  title: string
  compile_hint: string
  base_lib_search_hints: string[]
  position: number
  is_ledger?: number | boolean   // 3.1 §3 台账页(监控式,只追加,解析源)
}

interface SchemaForm {
  page_key: string
  title: string
  compile_hint: string
  hintsText: string
  position: string
  isLedger: boolean
}

const EMPTY_FORM: SchemaForm = { page_key: '', title: '', compile_hint: '', hintsText: '', position: '', isLedger: false }
// page_key 同时用于 [[page_key]] 链接语法与 schema_id,限制为安全字符集
const PAGE_KEY_RE = /^[A-Za-z0-9_]+$/

export function WikiSchemaManager({ projectId, purpose, onMetaSaved }: {
  projectId: string
  /** Wiki 目标（父级 listPages 聚合下发；本组件仅承载编辑） */
  purpose: string
  /** purpose 保存成功后回调父级刷新（避免保存后界面回显旧值） */
  onMetaSaved?: () => void
}) {
  const { t } = useTranslation()
  const showToast = useToast()
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
  // 「Wiki 目标」编辑态（2026-10-26 自 WikiHomePage 迁入）：saving 为 schema 表单专用，
  // purpose 保存独立 state 避免互锁
  const [editingPurpose, setEditingPurpose] = useState(false)
  const [draftPurpose, setDraftPurpose] = useState(purpose)
  const [savingPurpose, setSavingPurpose] = useState(false)

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

  // purpose props 回流时同步草稿（父级 loadPages 后新值下发）
  useEffect(() => { setDraftPurpose(purpose) }, [purpose])

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
      isLedger: Boolean(schema.is_ledger),
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
          isLedger: form.isLedger,
        })
      } else {
        await window.electronAPI.invoke('wiki:updateSchema', {
          projectId,
          schemaId: editing.id,
          title: form.title.trim(),
          compileHint: form.compile_hint,
          baseLibSearchHints: parseHints(form.hintsText),
          position,
          isLedger: form.isLedger,
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

  // 「Wiki 目标」保存（自 WikiHomePage 迁入，语义不变：wiki:updatePurpose + 父级刷新回流）
  const handleSavePurpose = async () => {
    setSavingPurpose(true)
    try {
      await window.electronAPI.invoke('wiki:updatePurpose', { projectId, purpose: draftPurpose })
      setEditingPurpose(false)
      onMetaSaved?.() // 父级刷新 purpose → props 回流，避免保存后仍显示旧值
    } catch (err) {
      console.error('Failed to save purpose:', err)
      showToast(t('project.wiki.saveMetaFailed', {
        reason: err instanceof Error ? err.message : String(err),
        defaultValue: `保存失败: ${err instanceof Error ? err.message : String(err)}`,
      }), 'error')
    } finally {
      setSavingPurpose(false)
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
      {/* ── Wiki 目标（2026-10-26 自 WikiHomePage 迁入）：目标属于框架定义层，与结构页同视图 ── */}
      <div className={s.homeSection}>
        <div className={s.homeSectionHeader}>
          <div className={s.homeSectionTitle}>
            <Target size={15} className={s.homeSectionIcon} />
            <h3>{t('project.wiki.purpose')}</h3>
          </div>
          {!editingPurpose && (
            <button
              className={s.editBtn}
              onClick={() => setEditingPurpose(true)}
            >
              <Edit3 size={12} /> {t('project.wiki.edit')}
            </button>
          )}
        </div>
        <p className={s.sectionHint}>{t('project.wiki.homePage.purposeHint')}</p>
        {editingPurpose ? (
          <div className={s.editBlock}>
            <textarea
              className={s.editTextarea}
              value={draftPurpose}
              onChange={(e) => setDraftPurpose(e.target.value)}
              placeholder={t('project.wiki.homePage.purposePlaceholder')}
              rows={4}
            />
            <div className={s.editActions}>
              <button className={s.editSaveBtn} onClick={handleSavePurpose} disabled={savingPurpose}>
                {savingPurpose ? <Loader2 size={12} className={s.spin} /> : <Check size={12} />}
                {t('project.wiki.save')}
              </button>
              <button className={s.editCancelBtn} onClick={() => { setEditingPurpose(false); setDraftPurpose(purpose) }}>
                <X size={12} /> {t('project.wiki.cancel')}
              </button>
            </div>
          </div>
        ) : (
          <div className={s.homeSectionContent}>
            {purpose ? (
              <Markdown content={purpose} />
            ) : (
              <p className={s.homePlaceholder}>
                {t('project.wiki.noPurpose')}
              </p>
            )}
          </div>
        )}
      </div>

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
              <label className={s.schemaCheckRow}>
                <input
                  type="checkbox"
                  checked={form.isLedger}
                  onChange={(e) => setForm({ ...form, isLedger: e.target.checked })}
                />
                <span className={s.schemaFormLabel}>{t('project.wiki.schemaIsLedger')}</span>
                <span className={s.schemaFieldHint}>{t('project.wiki.schemaIsLedgerHint')}</span>
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
                      {Boolean(schema.is_ledger) && (
                        <span className={s.schemaLedgerBadge}>{t('project.wiki.schemaLedgerBadge')}</span>
                      )}
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
