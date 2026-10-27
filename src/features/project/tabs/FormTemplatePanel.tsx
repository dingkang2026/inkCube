/** src/features/project/tabs/FormTemplatePanel.tsx — 全局模板管理页「填表模板」分区（2.4 §5.1）
 *
 * 全局资产唯一入口（§5.4 不变量 6）：模板上传 / 重解析 / 删除 / schema 查看只在此页，
 * 项目页发起弹窗仅消费（选模板）。轮询谓词从 useActivity('form_template') 派生
 * （§5.4.8 纪律，不自建布尔），分析完成边沿补拉一次，success/invalid/failed 立即上屏。
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Upload, Search, Eye, Trash2, RefreshCw, FileText, Loader2, ChevronLeft, ChevronRight } from 'lucide-react'
import { useToast } from '../../knowledge/Toast'
import { useConfirm } from '../../../components/ConfirmDialog'
import { usePrompt } from '../../../components/PromptDialog'
import { usePolling } from '../../../hooks/usePolling'
import { useEdgeRefetch } from '../../../hooks/useEdgeRefetch'
import { useActivity } from '../../activity/ActivityContext'
import { apiErrorText } from '../../../i18n/errorCodes'
import type { FormSchema, FormField } from './FormSchemaForm'
import { useDrawerWidth } from '../../../hooks/useDrawerWidth'
import s from './FormTemplatePanel.module.css'

interface FormTemplateItem {
  id: string
  name: string
  source: 'preset' | 'upload'
  status: 'pending' | 'analyzing' | 'success' | 'invalid' | 'failed'
  error: string | null
  field_count: number | null
  user_hint: string | null
  file_changed?: boolean  // 模板文件内容变更（后端动态比对 file_hash，2.4 §2.4）
  created_at: string
  updated_at: string
}

/** 详情行：template_schema / llm_mapping 已由后端解析为对象（损坏容错为 null） */
interface FormTemplateDetail extends FormTemplateItem {
  template_schema: FormSchema | null
  llm_mapping: unknown
  file_path?: string
}

type TplStatus = FormTemplateItem['status']

const STATUS_FILTERS: TplStatus[] = ['pending', 'analyzing', 'success', 'invalid', 'failed']
const TPL_PAGE_SIZE = 20

/** 查看抽屉宽度下限（默认 60vw 随视口、上限 92vw 随视口，见 useDrawerWidth；
 *  2026-10-26 由 CSS 固定 46% 不可拖改统一契约，与 CSS min-width 兜底同值） */
const DRAWER_MIN_DETAIL = 460

export function FormTemplatePanel() {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const prompt = usePrompt()
  const showToast = useToast()
  // 列表分页（0.1dev-guide 统一约定，管理页形态对齐 KnowledgePage）：
  // 20/页 + q/status 防抖服务端下沉 + total 页码控件
  const [templates, setTemplates] = useState<FormTemplateItem[]>([])
  const [loading, setLoading] = useState(true)
  const [tplPage, setTplPage] = useState(1)
  const [tplTotal, setTplTotal] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  // 轮询/边沿补拉保持当前页（KnowledgePage filePageRef 同款）：硬编码 page=1 会在
  // 后台分析完成时把正在浏览的页拉回首页
  const tplPageRef = useRef(1)
  tplPageRef.current = tplPage
  const [uploading, setUploading] = useState(false)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | TplStatus>('all')
  // 筛选防抖 300ms（保留输入即时性，请求下沉服务端 q/status）
  const [debounced, setDebounced] = useState<{ q: string; status: 'all' | TplStatus }>({ q: '', status: 'all' })
  const debounceRef = useRef<number | null>(null)
  useEffect(() => {
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current)
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null
      setDebounced({ q: search, status: statusFilter })
    }, 300)
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current)
        debounceRef.current = null
      }
    }
  }, [search, statusFilter])
  // 查看抽屉：字段清单 / Schema 原文双 Tab（对齐报告模板详情抽屉形态）
  const [detail, setDetail] = useState<FormTemplateDetail | null>(null)
  const [detailTab, setDetailTab] = useState<'fields' | 'json'>('fields')
  // 抽屉宽度/拖宽/防误关守卫统一契约（抽屉六处收编，2026-10-26）
  const dr = useDrawerWidth({ min: DRAWER_MIN_DETAIL })

  const loadTemplates = useCallback(async (page?: number) => {
    const p = page ?? tplPageRef.current
    const fetchPage = async (n: number) => window.electronAPI.invoke('form-template:list', {
      limit: TPL_PAGE_SIZE,
      offset: (n - 1) * TPL_PAGE_SIZE,
      q: debounced.q.trim() || undefined,
      status: debounced.status !== 'all' ? debounced.status : undefined,
    })
    try {
      const result: any = await fetchPage(p)
      const rows: FormTemplateItem[] = result.templates || []
      const total = result.total || 0
      // 越界回退（对齐 KnowledgePage）：删行/筛选收紧后当前页可能超出末页
      if (!rows.length && p > 1 && total > 0) {
        const lastPage = Math.max(1, Math.ceil(total / TPL_PAGE_SIZE))
        if (lastPage < p) {
          const retry: any = await fetchPage(lastPage)
          setTemplates(retry.templates || [])
          setTplTotal(retry.total || 0)
          setTplPage(lastPage)
          return
        }
      }
      setTemplates(rows)
      setTplTotal(total)
      setTplPage(p)
    } catch (err) {
      console.error('Failed to load form templates:', err)
    } finally {
      setLoading(false)
    }
  }, [debounced])

  // 挂载 + 防抖筛选变化（loadTemplates 依赖 debounced）→ 回第 1 页重拉
  useEffect(() => { void loadTemplates(1) }, [loadTemplates])

  // 轮询谓词从全局心跳派生（§5.4.8 纪律）；分析完成边沿补拉一次（0.4 §6.3）
  const { items: tplActivities } = useActivity('form_template')
  const hasAnalyzing = tplActivities.length > 0
  usePolling({
    name: 'form-templates-analyzing',
    enabled: hasAnalyzing,
    interval: 3000,
    task: () => loadTemplates(),
  })
  useEdgeRefetch(hasAnalyzing, () => { void loadTemplates() })

  // 页码切换（对齐 KnowledgePage handleFilePageChange）：越界 guard + in-flight 防重
  const handlePageChange = async (page: number) => {
    if (loadingMore || page < 1) return
    const totalPages = Math.max(1, Math.ceil(tplTotal / TPL_PAGE_SIZE))
    if (page > totalPages) return
    setLoadingMore(true)
    try {
      await loadTemplates(page)
    } finally {
      setLoadingMore(false)
    }
  }

  // ── 上传：dialog 选 .docx → multipart → pending 入队（后台分析，活动心跳驱动）──
  const handleUpload = async () => {
    try {
      const result: any = await window.electronAPI.invoke('dialog:openFile', {
        filters: [{ name: 'Word Document', extensions: ['docx'] }],
        properties: ['openFile'],
      })
      if (!result || result.canceled || !result.filePaths?.length) return
      const filePath: string = result.filePaths[0]
      if (!filePath.toLowerCase().endsWith('.docx')) {
        showToast(t('project.forms.tpl.uploadInvalid'), 'error')
        return
      }
      setUploading(true)
      try {
        const res: any = await window.electronAPI.invoke('form-template:upload', { filePath })
        if (res.error) {
          showToast(`${t('project.forms.tpl.uploadFailed')}: ${apiErrorText(res.code, res.error, t)}`, 'error')
        } else {
          showToast(t('project.forms.tpl.uploadQueued'), 'success')
          await loadTemplates(1) // 新模板回第一页立即可见
        }
      } finally {
        setUploading(false)
      }
    } catch (err) {
      setUploading(false)
      console.error('Failed to upload form template:', err)
      showToast(t('project.forms.tpl.uploadFailed'), 'error')
    }
  }

  // ── 查看抽屉（详情含 schema 大列，与列表分离按需拉取）──
  const handleView = async (tpl: FormTemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('form-template:detail', tpl.id)
      if (res.error || !res.template) {
        showToast(t('project.forms.tpl.detailLoadFailed'), 'error')
        return
      }
      setDetail(res.template as FormTemplateDetail)
      setDetailTab('fields')
    } catch (err) {
      console.error('Failed to load form template detail:', err)
      showToast(t('project.forms.tpl.detailLoadFailed'), 'error')
    }
  }

  // ── 重解析：补充说明必填（§2.3），提交后行状态回「分析中」──
  const handleReanalyze = async (tpl: FormTemplateItem) => {
    const hint = await prompt({
      title: t('project.forms.tpl.reanalyzeTitle'),
      message: t('project.forms.tpl.reanalyzeMessage'),
      placeholder: t('project.forms.tpl.reanalyzePlaceholder'),
    })
    if (hint == null) return
    if (!hint.trim()) {
      showToast(t('project.forms.tpl.reanalyzeRequired'), 'error')
      return
    }
    try {
      const res: any = await window.electronAPI.invoke('form-template:reanalyze', {
        templateId: tpl.id,
        userHint: hint.trim(),
      })
      if (res.error) {
        showToast(`${t('project.forms.tpl.reanalyzeFailed')}: ${apiErrorText(res.code, res.error, t)}`, 'error')
      } else {
        await loadTemplates()
      }
    } catch (err) {
      console.error('Failed to reanalyze form template:', err)
      showToast(t('project.forms.tpl.reanalyzeFailed'), 'error')
    }
  }

  // ── 删除：FK 已摘除（2.4 不变量 11），被引用仍可删、实例悬空；分析中 → 后端 409 ──
  const handleDelete = async (tpl: FormTemplateItem) => {
    if (!(await confirmDialog({
      title: t('project.forms.tpl.deleteConfirmTitle'),
      message: t('project.forms.tpl.deleteConfirm', { name: tpl.name }),
    }))) return
    try {
      const res: any = await window.electronAPI.invoke('form-template:delete', tpl.id)
      if (res.error) {
        showToast(apiErrorText(res.code, res.error, t), 'error')
      } else {
        showToast(t('project.forms.tpl.deleteSuccess'), 'success')
        if (detail?.id === tpl.id) setDetail(null)
        await loadTemplates()
      }
    } catch (err) {
      console.error('Failed to delete form template:', err)
      showToast(t('project.forms.tpl.deleteFailed'), 'error')
    }
  }

  const statusInfo = (tpl: FormTemplateItem): { dot: string; label: string; title?: string } => {
    switch (tpl.status) {
      case 'pending':
        return { dot: `${s.dot} ${s.dotPulse}`, label: t('project.forms.tpl.statusPending') }
      case 'analyzing':
        return { dot: `${s.dot} ${s.dotPulse}`, label: t('project.forms.tpl.statusAnalyzing') }
      case 'success':
        return { dot: `${s.dot} ${s.dotSuccess}`, label: t('project.forms.tpl.statusSuccess') }
      case 'invalid':
        return { dot: `${s.dot} ${s.dotInvalid}`, label: t('project.forms.tpl.statusInvalid'), title: t('project.forms.tpl.invalidHint') }
      default:
        return { dot: `${s.dot} ${s.dotFailed}`, label: t('project.forms.tpl.statusFailed'), title: tpl.error || undefined }
    }
  }

  const sourceLabel = (src: string) =>
    src === 'preset' ? t('project.forms.tpl.sourcePreset') : t('project.forms.tpl.sourceUpload')

  const confLabel = (conf: string | null | undefined) => {
    if (conf === 'high') return t('project.forms.tpl.confHigh')
    if (conf === 'medium') return t('project.forms.tpl.confMedium')
    if (conf === 'low') return t('project.forms.tpl.confLow')
    return null
  }

  const fields: FormField[] = detail?.template_schema?.fields ?? []
  const unmapped: unknown[] = detail?.template_schema?.unmapped_cells ?? []

  return (
    <div className={s.panel}>
      {/* 工具条 */}
      <div className={s.toolbar}>
        <div className={s.toolbarLeft}>
          <div className={s.searchBox}>
            <Search size={14} />
            <input
              type="text"
              placeholder={t('project.forms.tpl.searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className={s.statusSelect}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as 'all' | TplStatus)}
          >
            <option value="all">{t('project.forms.tpl.filterAll')}</option>
            {STATUS_FILTERS.map((st) => (
              <option key={st} value={st}>{t(`project.forms.tpl.status${st[0].toUpperCase()}${st.slice(1)}`)}</option>
            ))}
          </select>
        </div>
        <button className={s.uploadBtn} onClick={handleUpload} disabled={uploading}>
          {uploading ? <Loader2 size={14} className={s.spin} /> : <Upload size={14} />}
          {uploading ? t('project.forms.tpl.uploading') : t('project.forms.tpl.upload')}
        </button>
      </div>

      {/* 模板列表 */}
      {loading ? (
        <div className={s.emptyState}>{t('common.loading')}</div>
      ) : templates.length === 0 ? (
        <div className={s.emptyState}>{t('project.forms.tpl.empty')}</div>
      ) : (
        <div className={s.list}>
          <div className={s.listHeader}>
            <div className={s.nameCell}>{t('project.forms.tpl.col.name')}</div>
            <div className={s.cell}>{t('project.forms.tpl.col.source')}</div>
            <div className={s.cell}>{t('project.forms.tpl.col.fields')}</div>
            <div className={s.cell}>{t('project.forms.tpl.col.updatedAt')}</div>
            <div className={s.cell}>{t('project.forms.tpl.col.status')}</div>
            <div className={s.cell} style={{ justifyContent: 'flex-end' }}>{t('project.forms.tpl.col.actions')}</div>
          </div>
          {templates.map((tpl) => {
            const st = statusInfo(tpl)
            return (
              <div key={tpl.id} className={s.row} onClick={() => void handleView(tpl)}>
                <div className={s.nameCell}>
                  <FileText size={14} className={s.rowIcon} />
                  <span className={s.nameText}>{tpl.name}</span>
                </div>
                <div className={s.cell}>
                  <span className={s.sourceBadge} data-src={tpl.source}>{sourceLabel(tpl.source)}</span>
                </div>
                <div className={s.cell}>{tpl.field_count ?? '—'}</div>
                <div className={s.cell}>{new Date(tpl.updated_at).toLocaleDateString()}</div>
                <div className={s.statusCell} title={st.title}>
                  <span className={st.dot} />
                  <span className={s.statusLabel}>{st.label}</span>
                  {tpl.status === 'success' && tpl.file_changed && (
                    <span className={s.changedHint} title={t('project.forms.tpl.changedTitle')}>
                      {t('project.forms.tpl.changedHint')}
                    </span>
                  )}
                </div>
                <div className={s.actions}>
                  <button className={s.iconBtn} onClick={(e) => { e.stopPropagation(); void handleView(tpl) }} title={t('project.forms.tpl.view')}>
                    <Eye size={13} />
                  </button>
                  <button className={s.iconBtn} onClick={(e) => { e.stopPropagation(); void handleReanalyze(tpl) }} title={t('project.forms.tpl.reanalyze')}>
                    <RefreshCw size={13} />
                  </button>
                  <button className={`${s.iconBtn} ${s.iconBtnDanger}`} onClick={(e) => { e.stopPropagation(); void handleDelete(tpl) }} title={t('project.forms.tpl.delete')}>
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            )
          })}
          {/* 页码控件（对齐 FileUploader 范式：≤7 页全量，否则当前页±3 窗口） */}
          {(() => {
            const totalPages = Math.max(1, Math.ceil(tplTotal / TPL_PAGE_SIZE))
            if (totalPages <= 1) return null
            const pages: number[] = []
            if (totalPages <= 7) {
              for (let i = 1; i <= totalPages; i++) pages.push(i)
            } else if (tplPage <= 4) {
              for (let i = 1; i <= 7; i++) pages.push(i)
            } else if (tplPage >= totalPages - 3) {
              for (let i = totalPages - 6; i <= totalPages; i++) pages.push(i)
            } else {
              for (let i = tplPage - 3; i <= tplPage + 3; i++) pages.push(i)
            }
            return (
              <div className={s.filePagination}>
                <button className={s.pageBtn} disabled={tplPage <= 1} onClick={() => { void handlePageChange(tplPage - 1) }}>
                  <ChevronLeft size={14} />
                </button>
                {pages.map((pg) => (
                  <button key={pg} className={`${s.pageBtn} ${pg === tplPage ? s.pageBtnActive : ''}`} onClick={() => { void handlePageChange(pg) }}>
                    {pg}
                  </button>
                ))}
                <button className={s.pageBtn} disabled={tplPage >= totalPages} onClick={() => { void handlePageChange(tplPage + 1) }}>
                  <ChevronRight size={14} />
                </button>
                <span className={s.pageInfo}>{tplPage} / {totalPages}</span>
              </div>
            )
          })()}
        </div>
      )}

      {/* 查看抽屉 */}
      {detail && (
        <>
          <div className={s.drawerBackdrop} onClick={() => { if (!dr.suppressClick.current) setDetail(null) }} />
          <div className={s.drawer} style={{ width: dr.width }} onMouseDown={dr.onPanelMouseDown}>
            <div
              className={`${s.dragHandle} ${dr.dragging ? s.dragHandleActive : ''}`}
              onMouseDown={dr.onHandleMouseDown}
            />
            <div className={s.drawerHeader}>
              <h4>{detail.name}</h4>
              <button className={s.closeBtn} onClick={() => setDetail(null)} title={t('common.close')}>×</button>
            </div>
            <div className={s.drawerTabs}>
              <button
                className={detailTab === 'fields' ? `${s.drawerTab} ${s.drawerTabActive}` : s.drawerTab}
                onClick={() => setDetailTab('fields')}
              >{t('project.forms.tpl.fieldsTab')}</button>
              <button
                className={detailTab === 'json' ? `${s.drawerTab} ${s.drawerTabActive}` : s.drawerTab}
                onClick={() => setDetailTab('json')}
              >{t('project.forms.tpl.jsonTab')}</button>
            </div>
            <div className={s.drawerBody}>
              {detailTab === 'json' ? (
                <pre className={s.jsonView}>{JSON.stringify(detail.template_schema ?? {}, null, 2)}</pre>
              ) : (
              <>
              <p className={s.detailMeta}>
                {sourceLabel(detail.source)} · {t('project.forms.tpl.fieldCount', { count: detail.field_count ?? 0 })} · {new Date(detail.created_at).toLocaleDateString()}
              </p>

              {detail.user_hint && (
                <div className={s.detailBlock}>
                  <p className={s.detailSectionTitle}>{t('project.forms.tpl.hintLabel')}</p>
                  <p className={s.hintText}>{detail.user_hint}</p>
                </div>
              )}

              <div className={s.detailBlock}>
                <p className={s.detailSectionTitle}>{t('project.forms.tpl.unmappedTitle')}</p>
                {unmapped.length > 0 ? (
                  <ul className={s.hintText}>
                    {unmapped.map((u, i) => (
                      <li key={i}>{typeof u === 'string' ? u : JSON.stringify(u)}</li>
                    ))}
                  </ul>
                ) : (
                  <p className={s.hintText}>{t('project.forms.tpl.unmappedEmpty')}</p>
                )}
              </div>

              <div className={s.detailBlock}>
                <p className={s.detailSectionTitle}>{t('project.forms.tpl.fieldsTab')}</p>
                {fields.length === 0 ? (
                  <p className={s.hintText}>—</p>
                ) : (
                  fields.map((f) => {
                    const conf = confLabel(f.confidence)
                    return (
                      <div key={f.field_key} className={s.fieldItem}>
                        <div className={s.fieldHead}>
                          <span className={s.fieldName}>{f.label}</span>
                          <span className={s.typeBadge}>
                            {f.fill_type}{f.fill_mode ? ` · ${f.fill_mode}` : ''}
                          </span>
                          {conf && <span className={s.confBadge} data-conf={f.confidence}>{conf}</span>}
                        </div>
                        {f.instruction && <p className={s.fieldMeta}>{f.instruction}</p>}
                        {(f.kb_query ?? []).length > 0 && (
                          <p className={s.fieldMeta}>
                            {t('project.forms.tpl.kbQueryLabel')}: {(f.kb_query ?? []).join('、')}
                          </p>
                        )}
                      </div>
                    )
                  })
                )}
              </div>
              </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
