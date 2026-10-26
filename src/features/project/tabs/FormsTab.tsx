/** src/features/project/tabs/FormsTab.tsx — 项目「填表」Tab（2.4 §5.1 实例管理）
 *
 * 两视图状态机 list → preview（模板管理已移出至全局模板管理页，§5.4 不变量 6）。
 * 轮询谓词从 useActivity('form_fill') 派生（§5.4.8 纪律，不自建布尔）：实例卡进度
 * 常显，终态边沿补拉一次。preview 用 FormSchemaForm 六型渲染，人工修订经
 * form-fill:update 同步重放——值集形状与 AI 初填同构（§5.4 不变量 3），服务端
 * 归一化后的值集/报告以响应为准回填（严进宽出，非法编辑形状被丢弃）。
 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Table, Plus, ArrowLeft, Download, Trash2, Loader2, CheckCircle, Circle, AlertCircle, RefreshCw } from 'lucide-react'
import { useToast } from '../../knowledge/Toast'
import { useConfirm } from '../../../components/ConfirmDialog'
import { usePolling } from '../../../hooks/usePolling'
import { useEdgeRefetch } from '../../../hooks/useEdgeRefetch'
import { usePagedList } from '../../../hooks/usePagedList'
import { useActivity } from '../../activity/ActivityContext'
import { apiErrorText } from '../../../i18n/errorCodes'
import { FormSchemaForm, type FormSchema, type FormValues, type FormReportEntry, type FieldStatus } from './FormSchemaForm'
import { UsageText } from '../../usage/UsageText'
import type { UsageSummary } from '../../../types'
import s from './FormsTab.module.css'

interface FormFillItem {
  id: string
  project_id: string
  template_id: string
  title: string
  status: 'pending' | 'filling' | 'success' | 'failed'
  progress: number
  error: string | null
  created_at: string
  updated_at: string
  template_name: string | null
  /** 该次填表总消耗（form-fill:list 聚合；无记录为 null，2.10 §4.5） */
  usage?: UsageSummary | null
}

/** 发起弹窗内的模板轻量视图（form-template:list 行） */
interface FormTemplateLite {
  id: string
  name: string
  status: 'pending' | 'analyzing' | 'success' | 'invalid' | 'failed'
  error: string | null
  field_count: number | null
  file_changed?: boolean  // 文件已变更：置灰不可选（旧 schema 填新文件会错位）
}

interface PreviewState {
  fill: FormFillItem & { report: FormReportEntry[] | null }
  schema: FormSchema | null
}

/** FormsTab — 填表 Tab */
export function FormsTab({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()
  const [viewMode, setViewMode] = useState<'list' | 'preview'>('list')
  const [createOpen, setCreateOpen] = useState(false)
  // preview：打开时的详情快照 + 工作值集（编辑不落库，保存/离开时处理）
  const [preview, setPreview] = useState<PreviewState | null>(null)
  const [values, setValues] = useState<FormValues>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  // 填表实例列表分页（0.1dev-guide 统一约定）：50/页「加载更多」；projectId 变化自动重拉
  const {
    items: fills,
    total: fillsTotal,
    hasMore: hasMoreFills,
    loadingMore: loadingMoreFills,
    loading,
    error: fillsError,
    reload: reloadFills,
    loadMore: loadMoreFills,
  } = usePagedList<FormFillItem>({ channel: 'form-fill:list', itemsKey: 'fills', params: { projectId } })

  // 加载失败提示（m-3）：静默吞掉会让列表空白且无任何提示
  useEffect(() => {
    if (fillsError) showToast(`${t('project.forms.listLoadFailed')}: ${fillsError}`, 'error')
  }, [fillsError, showToast, t])

  useEffect(() => {
    setViewMode('list')
    setPreview(null)
  }, [projectId])

  // 轮询谓词从全局心跳派生（§5.4.8 纪律）；终态边沿补拉一次（0.4 §6.3）
  const { items: activeFillItems } = useActivity('form_fill')
  const hasActive = activeFillItems.some((a) => a.project_id === projectId)
  usePolling({
    name: 'form-fills-active',
    enabled: hasActive,
    interval: 3000,
    // reload 按已加载量重拉（不退回第一页）；内部 catch 吞错由 usePolling 视为成功
    task: () => reloadFills(),
  })
  useEdgeRefetch(hasActive, () => { void reloadFills() })

  // ── 打开 preview（success/failed；进行中禁入，进度在实例卡常显）──
  const openFill = async (fill: FormFillItem) => {
    if (fill.status === 'pending' || fill.status === 'filling') return
    try {
      const res: any = await window.electronAPI.invoke('form-fill:detail', fill.id)
      if (res.error) {
        showToast(t('project.forms.detailLoadFailed'), 'error')
        return
      }
      setPreview({ fill: res.fill, schema: res.schema ?? null })
      setValues(res.fill?.filled_values ?? {})
      setDirty(false)
      setViewMode('preview')
    } catch (err) {
      console.error('Failed to load form fill detail:', err)
      showToast(t('project.forms.detailLoadFailed'), 'error')
    }
  }

  const handleBack = async () => {
    if (dirty && !(await confirmDialog({ title: t('project.forms.back'), message: t('project.forms.unsavedConfirm') }))) return
    setViewMode('list')
    setPreview(null)
    setValues({})
    setDirty(false)
  }

  // ── 保存并重新生成：同步重放（秒级），服务端归一结果为准 ──
  const handleSave = async () => {
    if (!preview || saving) return
    setSaving(true)
    try {
      const res: any = await window.electronAPI.invoke('form-fill:update', {
        fillId: preview.fill.id,
        values,
      })
      if (res.error) {
        showToast(`${t('project.forms.saveFailed')}: ${apiErrorText(res.code, res.error, t)}`, 'error')
      } else {
        setValues(res.fill?.filled_values ?? values)
        setPreview((p) => (p ? { ...p, fill: { ...p.fill, report: res.report ?? p.fill.report } } : p))
        setDirty(false)
        showToast(t('project.forms.saveSuccess'), 'success')
        void reloadFills()
      }
    } catch (err) {
      console.error('Failed to save form fill:', err)
      showToast(t('project.forms.saveFailed'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleDownload = async (fill: FormFillItem) => {
    try {
      const res: any = await window.electronAPI.invoke('form-fill:download', {
        fillId: fill.id,
        title: fill.title,
      })
      if (res?.canceled) return
      if (res?.error) {
        showToast(`${t('project.forms.downloadFailed')}: ${res.error}`, 'error')
      } else {
        showToast(t('project.forms.downloadSuccess', { path: res.path }), 'success')
      }
    } catch (err) {
      console.error('Failed to download form fill:', err)
      showToast(t('project.forms.downloadFailed'), 'error')
    }
  }

  const handleDelete = async (fill: FormFillItem) => {
    if (!(await confirmDialog({
      title: t('project.forms.deleteConfirmTitle'),
      message: t('project.forms.deleteConfirm', { title: fill.title }),
    }))) return
    try {
      const res: any = await window.electronAPI.invoke('form-fill:delete', fill.id)
      if (res?.error) {
        showToast(`${t('project.forms.deleteFailed')}: ${apiErrorText(res.code, res.error, t)}`, 'error')
      } else {
        showToast(t('project.forms.deleteSuccess'), 'success')
        if (preview?.fill.id === fill.id) {
          setViewMode('list')
          setPreview(null)
        }
        await reloadFills()
      }
    } catch (err) {
      console.error('Failed to delete form fill:', err)
      showToast(t('project.forms.deleteFailed'), 'error')
    }
  }

  // ── failed 实例重试：同模板同标题重新发起（新实例，旧记录保留追溯）──
  const handleRetry = async (fill: FormFillItem) => {
    try {
      const res: any = await window.electronAPI.invoke('form-fill:create', {
        projectId,
        templateId: fill.template_id,
        title: fill.title,
      })
      if (res.error) {
        showToast(`${t('project.forms.createFailed')}: ${apiErrorText(res.code, res.error, t)}`, 'error')
      } else {
        showToast(t('project.forms.statusPending'), 'info')
        setViewMode('list')
        setPreview(null)
        await reloadFills()
      }
    } catch (err) {
      console.error('Failed to retry form fill:', err)
      showToast(t('project.forms.createFailed'), 'error')
    }
  }

  // 徽条点击滚动至首个对应状态字段（§5.1；FormSchemaForm 行带 data-field-key）
  const scrollToFirst = (status: FieldStatus) => {
    const key = (preview?.fill.report ?? []).find((r) => r.status === status)?.field_key
    if (!key) return
    document.querySelector(`[data-field-key="${CSS.escape(key)}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  // ── preview 视图 ──
  if (viewMode === 'preview' && preview) {
    const report = preview.fill.report ?? []
    const filledCount = report.filter((r) => r.status === 'filled').length
    const noDataCount = report.filter((r) => r.status === 'no_data').length
    const failedCount = report.filter((r) => r.status === 'failed').length
    return (
      <div className={s.container}>
        <div className={s.previewHead}>
          <div className={s.headRow}>
            <div className={s.headLeft}>
              <button className={s.backBtn} onClick={handleBack}>
                <ArrowLeft size={14} /> {t('project.forms.back')}
              </button>
              <span className={s.headerTitle}>{preview.fill.title}</span>
            </div>
            <div className={s.headRight}>
              <button
                className={s.actionBtn}
                onClick={() => void handleDownload(preview.fill)}
                disabled={preview.fill.status !== 'success'}
              >
                <Download size={13} /> {t('project.forms.downloadDocx')}
              </button>
            </div>
          </div>
          {report.length > 0 && (
            <div className={s.badgeBar}>
              {filledCount > 0 && (
                <button className={`${s.badge} ${s.badgeFilled}`} onClick={() => scrollToFirst('filled')}>
                  <CheckCircle size={12} /> {t('project.forms.badgeFilled', { count: filledCount })}
                </button>
              )}
              {noDataCount > 0 && (
                <button className={`${s.badge} ${s.badgeNoData}`} onClick={() => scrollToFirst('no_data')}>
                  <Circle size={12} /> {t('project.forms.badgeNoData', { count: noDataCount })}
                </button>
              )}
              {failedCount > 0 && (
                <button className={`${s.badge} ${s.badgeFailed}`} onClick={() => scrollToFirst('failed')}>
                  <AlertCircle size={12} /> {t('project.forms.badgeFailed', { count: failedCount })}
                </button>
              )}
            </div>
          )}
        </div>

        {preview.fill.status === 'failed' && (
          <div className={s.errorBanner}>
            <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
            <div className={s.errorBody}>
              <p className={s.errorTitle}>{t('project.forms.errorLabel')}</p>
              <p className={s.errorText}>{preview.fill.error || '—'}</p>
            </div>
            <button
              className={s.actionBtn}
              onClick={() => void handleRetry(preview.fill)}
            >
              <RefreshCw size={12} /> {t('project.forms.retryFill')}
            </button>
          </div>
        )}

        <div className={s.formBody}>
          {preview.schema && (
            <FormSchemaForm
              schema={preview.schema}
              values={values}
              report={report}
              onChange={(fieldKey, value) => {
                setValues((prev) => ({ ...prev, [fieldKey]: value }))
                setDirty(true)
              }}
            />
          )}
        </div>

        <div className={s.footerActions}>
          <button className={s.cancelBtn} onClick={handleBack}>{t('project.forms.cancel')}</button>
          <button className={s.saveBtn} onClick={() => void handleSave()} disabled={!dirty || saving}>
            {saving && <Loader2 size={14} className={s.spinIcon} />}
            {saving ? t('project.forms.saving') : t('project.forms.saveAndRegenerate')}
          </button>
        </div>
      </div>
    )
  }

  // ── list 视图（默认）──
  // 头部统计（PhaseBoard headMeta 同款）：共 N 份 · 进行中 M 份（有活跃才带后缀）
  // 计数取服务端 total 全量口径（分页下不随已加载量漂移，review Major 2026-10-25）；
  // active 为前端派生值仅计已载入条目（进行中实例按 created_at 倒序落在首页，可接受）
  const activeFillCount = fills.filter((f) => f.status === 'pending' || f.status === 'filling').length
  return (
    <div className={s.container}>
      <div className={s.header}>
        <button
          className={s.primaryBtn}
          onClick={() => setCreateOpen(true)}
        >
          <Plus size={16} /> {t('project.forms.newForm')}
        </button>
        {fills.length > 0 && (
          <span className={s.headMeta}>
            {activeFillCount > 0
              ? t('project.forms.headCountActive', { count: fillsTotal ?? fills.length, active: activeFillCount })
              : t('project.forms.headCount', { count: fillsTotal ?? fills.length })}
          </span>
        )}
      </div>

      {loading ? (
        <div className={s.emptyState}>{t('common.loading')}</div>
      ) : fills.length === 0 ? (
        <div className={s.emptyState}>{t('project.forms.emptyForms')}</div>
      ) : (
        <div className={s.formList}>
          {fills.map((fill) => {
            const active = fill.status === 'pending' || fill.status === 'filling'
            return (
              <div key={fill.id} className={`${s.formCard} ${
                fill.status === 'pending' || fill.status === 'filling' ? s.formCardActive :
                fill.status === 'success' ? s.formCardDone : s.formCardFailed}`}>
                {/* 状态左缘色对齐 PhaseBoard 行口径（§6.3）：进行中 accent / 成功 success / 失败 error */}
                <div className={s.formIcon}><Table size={18} /></div>
                <div className={s.formInfo}>
                  <div className={s.formTitle}>{fill.title}</div>
                  <div className={s.formMeta}>
                    <span>{fill.template_name ?? t('project.forms.templateDeleted')}</span>
                    <span className={s.dot}>·</span>
                    <span>{new Date(fill.created_at).toLocaleString()}</span>
                    {/* 该次填表总消耗（2.10 §6.1/线框 6） */}
                    {fill.usage?.calls ? (<> · <UsageText usage={fill.usage} /></>) : null}
                  </div>
                </div>
                <div className={s.formStatus}>
                  {fill.status === 'pending' && (
                    <span className={s.statusPending}>
                      <span className={s.pulseDot} /> {t('project.forms.statusPending')}
                    </span>
                  )}
                  {fill.status === 'filling' && (
                    <span className={s.statusFilling}>
                      <span className={s.pulseDot} /> {t('project.forms.filling')} {fill.progress || 0}%
                    </span>
                  )}
                  {fill.status === 'success' && (
                    <span className={s.statusCompleted}>
                      <CheckCircle size={13} /> {t('project.forms.completed')}
                    </span>
                  )}
                  {fill.status === 'failed' && (
                    <span className={s.statusFailed} title={fill.error || undefined}>
                      <AlertCircle size={13} /> {t('project.forms.statusFailed')}
                    </span>
                  )}
                </div>
                <div className={s.formActions}>
                  <button
                    className={s.actionBtn}
                    disabled={active}
                    onClick={() => void openFill(fill)}
                  >
                    {t('project.forms.open')}
                  </button>
                  <button
                    className={s.actionBtn}
                    disabled={fill.status !== 'success'}
                    onClick={() => void handleDownload(fill)}
                  >
                    <Download size={12} /> {t('project.forms.download')}
                  </button>
                  <button
                    className={s.actionBtn}
                    disabled={active}
                    title={active ? (fill.status === 'pending' ? t('project.forms.statusPending') : t('project.forms.filling')) : t('project.forms.delete')}
                    onClick={() => void handleDelete(fill)}
                  >
                    <Trash2 size={12} /> {t('project.forms.delete')}
                  </button>
                </div>
              </div>
            )
          })}
          {hasMoreFills && (
            <button
              className={s.loadMoreBtn}
              onClick={() => { void loadMoreFills() }}
              disabled={loadingMoreFills}
            >
              {loadingMoreFills ? t('common.loading') : t('common.loadMore')}
            </button>
          )}
        </div>
      )}

      {createOpen && (
        <CreateFillDialog
          projectId={projectId}
          onClose={() => setCreateOpen(false)}
          onCreated={() => { setCreateOpen(false); void reloadFills() }}
        />
      )}
    </div>
  )
}

// ── 发起填表弹窗：模板卡单选（仅 success 可选）+ 标题输入（§5.1）──

function CreateFillDialog({ projectId, onClose, onCreated }: {
  projectId: string
  onClose: () => void
  onCreated: () => void
}) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [templates, setTemplates] = useState<FormTemplateLite[] | null>(null)
  // 模板列表拉取失败态（页面遍历 review M3-3 2026-10-24）：失败不再与「真无模板」
  // 同路渲染空态误导用户（有模板可用却被告知无可选）
  const [tplError, setTplError] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [creating, setCreating] = useState(false)

  const loadTemplates = useCallback(async () => {
    setTemplates(null)
    setTplError(false)
    try {
      // 选择器形态：limit=0 全量（分页不得截断模板选择列表）
      const res: any = await window.electronAPI.invoke('form-template:list', { limit: 0 })
      setTemplates(res.templates ?? [])
    } catch {
      setTplError(true)
    }
  }, [])

  useEffect(() => {
    void loadTemplates()
  }, [loadTemplates])

  // 可选 = 分析成功且文件未变更（变更的模板需先重解析，后端 create 同步准入拦截）
  const isSelectable = (tpl: FormTemplateLite) =>
    tpl.status === 'success' && !tpl.file_changed

  // 模板列表加载后默认选中第一个可选模板
  useEffect(() => {
    if (!templates || selectedId !== null) return
    const first = templates.find(isSelectable)
    if (first) setSelectedId(first.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templates, selectedId])

  const start = async () => {
    if (!selectedId || creating) return
    setCreating(true)
    try {
      const res: any = await window.electronAPI.invoke('form-fill:create', {
        projectId,
        templateId: selectedId,
        title: title.trim() || null,
      })
      if (res.error) {
        showToast(`${t('project.forms.createFailed')}: ${apiErrorText(res.code, res.error, t)}`, 'error')
      } else {
        onCreated()
      }
    } catch (err) {
      console.error('Failed to create form fill:', err)
      showToast(t('project.forms.createFailed'), 'error')
    } finally {
      setCreating(false)
    }
  }

  const reasonFor = (tpl: FormTemplateLite): string => {
    if (tpl.status === 'success') return t('project.forms.createChanged')
    if (tpl.status === 'pending') return t('project.forms.tpl.statusPending')
    if (tpl.status === 'analyzing') return t('project.forms.tpl.statusAnalyzing')
    if (tpl.status === 'invalid') return t('project.forms.tpl.statusInvalid')
    return t('project.forms.tpl.statusFailed')
  }

  const selectableCount = (templates ?? []).filter(isSelectable).length

  return (
    <div className={s.dialogBackdrop} onClick={onClose}>
      <div className={s.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3 className={s.dialogTitle}>{t('project.forms.createTitle')}</h3>
        <div className={s.dialogBody}>
          <p className={s.sectionLabel}>{t('project.forms.createSelectTemplate')}</p>
          {templates === null ? (
            <div className={s.emptyInline}><Loader2 size={14} className={s.spinIcon} /></div>
          ) : tplError ? (
            <div className={s.emptyInline}>
              <p>{t('project.forms.tplLoadFailed')}</p>
              <button className={s.cancelBtn} onClick={() => void loadTemplates()}>
                {t('knowledge.retry')}
              </button>
            </div>
          ) : selectableCount === 0 ? (
            <div className={s.emptyInline}>{t('project.forms.createNoTemplates')}</div>
          ) : (
            <div className={s.tplList}>
              {templates.map((tpl) => {
                const ok = isSelectable(tpl)
                return (
                  <div
                    key={tpl.id}
                    className={`${s.tplCard} ${selectedId === tpl.id ? s.tplSelected : ''} ${!ok ? s.tplDisabled : ''}`}
                    onClick={() => { if (ok) setSelectedId(tpl.id) }}
                  >
                    <span className={s.tplName}>{tpl.name}</span>
                    <span className={s.tplHint}>
                      {ok
                        ? t('project.forms.tpl.fieldCount', { count: tpl.field_count ?? 0 })
                        : reasonFor(tpl)}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
          <p className={s.sectionLabel}>{t('project.forms.createTitleLabel')}</p>
          <input
            className={s.titleInput}
            value={title}
            placeholder={t('project.forms.createTitlePlaceholder')}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div className={s.dialogActions}>
          <button className={s.cancelBtn} onClick={onClose}>{t('project.forms.cancel')}</button>
          <button className={s.saveBtn} onClick={() => void start()} disabled={!selectedId || creating}>
            {creating && <Loader2 size={14} className={s.spinIcon} />}
            {t('project.forms.createStart')}
          </button>
        </div>
      </div>
    </div>
  )
}
