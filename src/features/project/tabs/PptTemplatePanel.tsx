/** src/features/project/tabs/PptTemplatePanel.tsx — PPT 模板工坊分区（2.3 §4.2 / §7.1）。
 *  四类分组列表（索引即状态，GET /ppt-templates，条目带 builtin 标记）+ 模板详情抽屉
 *  （SVG 原型网格 / Review PPTX 另存 / 删除，内置模板无删除入口）+ 重扫
 *  （register --rebuild-all）+ 新建向导入口
 *  + 创建中任务卡（缩小的任务 agent：TaskHistoryReplay 全量回放；brief 文字门
 *  就地应答，提案置顶，ppt:answer 复用 E1 通道；取消/删除按钮走既有
 *  ppt:cancel / ppt:delete）。
 *  样式复用 TemplateManagement.module.css（同页分区视觉一致），特有类追加其尾部。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Eye, Trash2, RefreshCw, Plus, Loader2, AlertCircle, Download, ChevronDown, ChevronUp, FileText,
} from 'lucide-react'
import s from './TemplateManagement.module.css'
import { useDrawerWidth } from '../../../hooks/useDrawerWidth'
import { useToast } from '../../knowledge/Toast'
import { useConfirm } from '../../../components/ConfirmDialog'
import { Markdown } from '../../../components/Markdown'
import { apiErrorText } from '../../../i18n/errorCodes'
import { TaskHistoryReplay } from '../../task/TaskHistoryReplay'
import type { TaskMessage } from '../../../types'
import { PptTemplateWizard } from './PptTemplateWizard'

interface TplEntry {
  id: string
  kind: string
  summary: string | null
  primary_color?: string
  canvas_format?: string
  page_count?: number
  page_types?: string[]
  keywords?: string[]
  workspace_path: string
  has_workspace: boolean
  builtin?: boolean      // 随包内置（源同名目录存在）：不可删除，无删除入口
  page_svgs: string[]
  images: string[]       // 包内资产（images/：logo 与品牌视觉元素）
  files: { path: string; size: number }[]  // 包内全文件清单（零盲区盘点）
  preview_pptx: string | null
}

interface JobRow {
  id: string
  task_id: string        // tasks 行 id：拉取 agent 消息（brief 提案/时间线）的查询键
  title: string
  status: string          // running / awaiting_input / paused / completed / failed / cancelled
  stage: string | null    // intake / strategy / authoring / export
  error: string | null
  /** 工作区模板包是否完整（failed 行展示「重试收录」的判定，后端富化） */
  package_ready?: boolean
  template_kind: string | null
  template_id: string | null
}

const KINDS = ['brand', 'style', 'layout', 'deck'] as const
const JOBS_PAGE_SIZE = 50

/** 详情抽屉宽度下限（默认 60vw 随视口、上限 92vw 随视口，见 useDrawerWidth；与 CSS min-width 兜底同值） */
const DRAWER_MIN_DETAIL = 460

/** 本地文件 → inkcube:// 协议 URL（与 PptWizard/ImageGallery 同规则，CSP img-src 已放行） */
function localFileUrl(filePath: string): string {
  return `inkcube://app?file=${encodeURIComponent(filePath.replace(/\\/g, '/'))}`
}

/** 字节数 → 人类可读（文件清单展示） */
function formatSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 可预览的图片扩展名（文件清单点击预览判定） */
const VIEWABLE_IMAGE_RE = /\.(svg|png|jpe?g|webp|gif|bmp)$/i

/** 创建中任务卡：状态徽标 + 缩小的任务 agent（消息全量回放）
 *  + brief 门就地应答（最后一条 assistant 提案置顶，确认/意见按钮紧随其下）
 *  + 取消（running/awaiting_input）/ 删除（终态/paused，后端连带清任务记录与工作区）。 */
function JobCard({ job, onChanged }: { job: JobRow; onChanged: () => void }) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  const [expanded, setExpanded] = useState(false)
  const [messages, setMessages] = useState<TaskMessage[]>([])
  const [notes, setNotes] = useState('')
  const [acting, setActing] = useState(false)

  // brief 提案：最后一条 assistant 消息（awaiting_input 时置顶展示，免翻执行记录）
  const proposal = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') return messages[i]
    }
    return null
  }, [messages])

  const loadMessages = useCallback(async () => {
    try {
      const res: any = await window.electronAPI.invoke('tasks:getMessages', job.task_id)
      if (!res?.error) setMessages(res.messages || [])
    } catch (err) {
      console.error('Failed to load template job messages:', err)
    }
  }, [job.task_id])

  useEffect(() => {
    if (expanded && messages.length === 0) void loadMessages()
  }, [expanded, messages.length, loadMessages])

  const act = async (fn: () => Promise<any>, okKey?: string) => {
    setActing(true)
    try {
      const res: any = await fn()
      if (res?.error) {
        showToast(t('project.pptTpl.job.actionFailed', {
          error: apiErrorText(res.code, res.error, t),
        }), 'error')
      } else if (okKey) {
        showToast(t(okKey), 'success')
      }
    } catch (err: any) {
      showToast(t('project.pptTpl.job.actionFailed', { error: String(err?.message || err) }), 'error')
    } finally {
      setActing(false)
      onChanged()
    }
  }

  const statusLabel = t(`project.pptTpl.job.status.${job.status}`, { defaultValue: job.status })
  const stageLabel = job.stage
    ? t(`project.pptTpl.job.stage.${job.stage}`, { defaultValue: job.stage }) : ''

  // 取消/删除：确认对话同 PptDetail 口径（danger）；删除由后端连带清任务记录与工作区
  const handleCancel = async () => {
    if (!(await confirmDialog({
      title: t('project.pptTpl.job.cancel'),
      message: t('project.pptTpl.job.cancelConfirm'),
      danger: true,
    }))) return
    await act(() => window.electronAPI.invoke('ppt:cancel', job.id), 'project.pptTpl.job.cancelled')
  }

  const handleDelete = async () => {
    if (!(await confirmDialog({
      title: t('project.pptTpl.job.delete'),
      message: t('project.pptTpl.job.deleteConfirm'),
      danger: true,
    }))) return
    await act(() => window.electronAPI.invoke('ppt:delete', job.id), 'project.pptTpl.job.deleted')
  }

  return (
    <div className={s.pptJobCard} data-status={job.status}>
      <div className={s.pptJobHead} onClick={() => setExpanded(v => !v)}>
        {job.status === 'running' && <Loader2 size={14} className={s.spinIcon} />}
        {job.status === 'awaiting_input' && <AlertCircle size={14} className={s.pptJobGate} />}
        <span className={s.pptJobTitle}>{job.title}</span>
        <span className={s.pptJobStatus}>{statusLabel}{stageLabel ? ` · ${stageLabel}` : ''}</span>
        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </div>
      {job.status === 'failed' && job.error && (
        <p className={s.pptJobError}>{job.error}</p>
      )}
      {expanded && (
        <div className={s.pptJobBody}>
          {/* brief 提案置顶（awaiting 时门禁内容一眼可见），确认/意见按钮紧随其下 */}
          {job.status === 'awaiting_input' && proposal && (
            <div className={s.pptJobProposal}>
              <p className={s.pptJobProposalTitle}>{t('project.pptTpl.job.proposalTitle')}</p>
              <Markdown content={proposal.content} className={s.pptJobProposalBody} />
            </div>
          )}
          <div className={s.pptJobMsgs}>
            {messages.length === 0
              ? <p className={s.emptyState}>{t('project.pptTpl.job.noMessages')}</p>
              : <TaskHistoryReplay messages={messages} />}
          </div>
          <div className={s.pptJobActions}>
            {job.status === 'awaiting_input' && (
              <>
                <p className={s.pptJobHint}>{t('project.pptTpl.job.briefHint')}</p>
                <button
                  className={s.primaryBtn} disabled={acting}
                  onClick={() => act(() => window.electronAPI.invoke('ppt:answer', { pptId: job.id, text: null }))}
                >{t('project.pptTpl.job.confirmBrief')}</button>
                <input
                  className={s.pptJobNotes}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={t('project.pptTpl.job.notesPlaceholder')}
                />
                <button
                  className={s.actionBtn} disabled={acting || !notes.trim()}
                  onClick={() => act(() => window.electronAPI.invoke('ppt:answer', { pptId: job.id, text: notes.trim() }))}
                >{t('project.pptTpl.job.sendNotes')}</button>
              </>
            )}
            {job.status === 'paused' && (
              <button
                className={s.primaryBtn} disabled={acting}
                onClick={() => act(() => window.electronAPI.invoke('ppt:resume', job.id))}
              >{t('project.pptTpl.job.resume')}</button>
            )}
            {/* 失败态重试（P2-3/P2-4）：工作区包完整 → 重试收录（纯本地幂等，
                不跑 agent）；否则 → 继续（agent 从历史重建上下文续跑） */}
            {job.status === 'failed' && (job.package_ready ? (
              <button
                className={s.primaryBtn} disabled={acting}
                title={t('project.pptTpl.job.retryCollectTip')}
                onClick={() => act(() => window.electronAPI.invoke('ppt:retryCollect', job.id), 'project.pptTpl.job.collected')}
              >{t('project.pptTpl.job.retryCollect')}</button>
            ) : (
              <button
                className={s.primaryBtn} disabled={acting}
                onClick={() => act(() => window.electronAPI.invoke('ppt:resume', job.id))}
              >{t('project.pptTpl.job.resume')}</button>
            ))}
            {/* 取消（running/awaiting_input）与删除（终态/paused）；后端 delete 对
                在跑任务 bounded 等待取消后再删，paused 直接删 */}
            {(job.status === 'running' || job.status === 'awaiting_input') && (
              <button
                className={s.actionBtn} disabled={acting}
                onClick={() => { void handleCancel() }}
              >{t('project.pptTpl.job.cancel')}</button>
            )}
            {(job.status === 'completed' || job.status === 'failed'
              || job.status === 'cancelled' || job.status === 'paused') && (
              <button
                className={`${s.actionBtn} ${s.actionBtnDanger}`} disabled={acting}
                onClick={() => { void handleDelete() }}
              ><Trash2 size={13} /> {t('project.pptTpl.job.delete')}</button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export function PptTemplatePanel() {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  const [templates, setTemplates] = useState<Record<string, TplEntry[]>>({})
  // jobs 分页（0.1dev-guide 统一约定）：活跃段后端保持全量（天然小），终态段 50/页
  // 「加载更多」；手写游标（loadJobs 承载轮询/边沿重拉语义，按已加载量重拉）
  const [jobs, setJobs] = useState<JobRow[]>([])
  const [jobsHasMore, setJobsHasMore] = useState(false)
  const [jobsLoadingMore, setJobsLoadingMore] = useState(false)
  const jobsLimitRef = useRef(JOBS_PAGE_SIZE)
  const [viewing, setViewing] = useState<TplEntry | null>(null)
  // 详情抽屉宽度/拖宽/防误关守卫统一契约（2026-10-26 由 CSS 固定 46% 不可拖改统一契约，
  // 下限对齐原 min-width 460）
  const dr = useDrawerWidth({ min: DRAWER_MIN_DETAIL })
  const [rescanning, setRescanning] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(false)
  // 模板规范（design_spec.md 正文）：抽屉打开时按需拉取，Brand/Style 的主体内容
  const [spec, setSpec] = useState<string | null>(null)
  const [specLoading, setSpecLoading] = useState(false)
  // 页面原型/品牌资产放大查看（lightbox）：存完整 URL 与显示名，null = 关闭
  const [zoomedSvg, setZoomedSvg] = useState<{ url: string; name: string } | null>(null)

  const loadTemplates = useCallback(async () => {
    try {
      const res: any = await window.electronAPI.invoke('ppt-template:list')
      setTemplates(res.templates || {})
    } catch (err) {
      console.error('Failed to load ppt templates:', err)
    }
  }, [])

  const jobsReqRef = useRef(0)
  const loadJobs = useCallback(async () => {
    const seq = ++jobsReqRef.current
    try {
      const res: any = await window.electronAPI.invoke('ppt-template:jobs', { limit: jobsLimitRef.current })
      if (seq === jobsReqRef.current) {
        setJobs(res.jobs || [])
        setJobsHasMore(!!res.has_more)
      }
    } catch (err) {
      console.error('Failed to load template jobs:', err)
    }
  }, [])

  const loadMoreJobs = useCallback(async () => {
    if (jobsLoadingMore) return
    setJobsLoadingMore(true)
    try {
      jobsLimitRef.current += JOBS_PAGE_SIZE
      await loadJobs()
    } finally {
      setJobsLoadingMore(false)
    }
  }, [jobsLoadingMore, loadJobs])

  useEffect(() => {
    void loadTemplates()
    void loadJobs()
  }, [loadTemplates, loadJobs])

  // 详情抽屉打开 → 拉取模板规范（404 = 纯索引条目无 spec，静默空态；其它错误 toast）
  useEffect(() => {
    if (!viewing) { setSpec(null); return }
    let cancelled = false
    setSpec(null)
    setSpecLoading(true)
    window.electronAPI
      .invoke('ppt-template:spec', { kind: viewing.kind, templateId: viewing.id })
      .then((res: any) => {
        if (cancelled) return
        if (res?.error) {
          setSpec(null)
          if (res.code !== 'SPEC_NOT_FOUND') {
            showToast(t('project.pptTpl.detail.specFailed', {
              error: apiErrorText(res.code, res.error, t),
            }), 'error')
          }
        } else {
          setSpec(res?.spec || null)
        }
      })
      .catch((err: any) => {
        if (!cancelled) console.error('Failed to load template spec:', err)
      })
      .finally(() => { if (!cancelled) setSpecLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewing?.id, viewing?.kind])

  // lightbox：ESC 关闭；抽屉关闭时同步复位
  useEffect(() => {
    if (!zoomedSvg) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setZoomedSvg(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [zoomedSvg])

  useEffect(() => {
    if (!viewing) setZoomedSvg(null)
  }, [viewing])

  // 创建中任务存在时轮询（页面级明细，0.4 §3.1：非全局常驻心跳）
  const hasActiveJob = jobs.some((j) => ['running', 'awaiting_input'].includes(j.status))
  useEffect(() => {
    if (!hasActiveJob) return
    const timer = setInterval(() => { void loadJobs() }, 5000)
    return () => clearInterval(timer)
  }, [hasActiveJob, loadJobs])

  // 活跃 → 终态边沿：模板可能已入库，刷新库列表
  const prevActiveRef = useRef(false)
  useEffect(() => {
    if (prevActiveRef.current && !hasActiveJob) void loadTemplates()
    prevActiveRef.current = hasActiveJob
  }, [hasActiveJob, loadTemplates])

  const handleRescan = async () => {
    setRescanning(true)
    try {
      const res: any = await window.electronAPI.invoke('ppt-template:rescan')
      await loadTemplates()
      if (res?.ok) showToast(t('project.pptTpl.rescanDone'), 'success')
      else showToast(t('project.pptTpl.rescanPartial', {
        kinds: (res?.results || []).filter((r: any) => !r.ok).map((r: any) => r.kind).join(', '),
      }), 'error')
    } catch (err: any) {
      showToast(t('project.pptTpl.rescanFailed', { error: String(err?.message || err) }), 'error')
    } finally {
      setRescanning(false)
    }
  }

  const handleDelete = async (tpl: TplEntry) => {
    if (!(await confirmDialog({
      title: t('project.pptTpl.delete'),
      message: t('project.pptTpl.confirmDelete', { id: tpl.id }),
    }))) return
    try {
      const res: any = await window.electronAPI.invoke('ppt-template:delete', { kind: tpl.kind, templateId: tpl.id })
      if (res?.error) throw new Error(apiErrorText(res.code, res.error, t))
      setViewing(null)
      await loadTemplates()
      showToast(t('project.pptTpl.deleteDone'), 'success')
    } catch (err: any) {
      showToast(t('project.pptTpl.deleteFailed', { error: String(err?.message || err) }), 'error')
    }
  }

  const handleDownloadPreview = async (tpl: TplEntry) => {
    await window.electronAPI.invoke('ppt-template:downloadPreview', {
      previewPath: tpl.preview_pptx, title: tpl.id,
    })
  }

  const kindLabel = (kind: string) => t(`project.pptTpl.kind.${kind}`)

  return (
    <div className={s.pptPanel}>
      <div className={s.filterBar}>
        <div className={s.filters}>
          <button className={s.refreshBtn} onClick={() => setWizardOpen(true)}>
            <Plus size={14} /> {t('project.pptTpl.create')}
          </button>
          <button className={s.refreshBtn} onClick={handleRescan} disabled={rescanning}>
            {rescanning ? <Loader2 size={14} className={s.spinIcon} /> : <RefreshCw size={14} />}
            {t('project.pptTpl.rescan')}
          </button>
        </div>
      </div>

      {/* 创建中/最近任务（brief 门就地应答） */}
      {jobs.length > 0 && (
        <div className={s.section}>
          <div className={s.sectionTitle}>{t('project.pptTpl.jobs')}</div>
          {jobs.map((j) => (
            <JobCard key={j.id} job={j} onChanged={() => { void loadJobs(); void loadTemplates() }} />
          ))}
          {jobsHasMore && (
            <button
              className={s.loadMoreBtn}
              onClick={() => { void loadMoreJobs() }}
              disabled={jobsLoadingMore}
            >
              {jobsLoadingMore ? t('common.loading') : t('common.loadMore')}
            </button>
          )}
        </div>
      )}

      {/* 四类分组列表（索引即状态） */}
      {KINDS.map((kind) => {
        const list = templates[kind] || []
        return (
          <div className={s.section} key={kind}>
            <div className={s.sectionTitle}>{kindLabel(kind)} ({list.length})</div>
            {list.length === 0 ? (
              <div className={s.emptyState}>{t('project.pptTpl.emptyKind')}</div>
            ) : (
              <div className={s.pptTplGrid}>
                {list.map((tpl) => (
                  <div key={tpl.id} className={s.pptTplCard} onClick={() => setViewing(tpl)}>
                    <span className={s.pptTplSwatch} style={{ background: tpl.primary_color || 'var(--bg-hover)' }} />
                    <div className={s.pptTplMeta}>
                      <span className={s.pptTplNameRow}>
                        <span className={s.pptTplName}>{tpl.id}</span>
                        {tpl.builtin && (
                          <span className={s.pptTplBadge}>{t('project.pptTpl.builtin')}</span>
                        )}
                      </span>
                      <span className={s.pptTplSummary}>{tpl.summary || '—'}</span>
                      <span className={s.pptTplSub}>
                        {tpl.canvas_format || ''}{tpl.page_count ? ` · ${tpl.page_count}p` : ''}
                      </span>
                    </div>
                    <div className={s.pptTplActions}>
                      <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); setViewing(tpl) }} title={t('project.pptTpl.detail')}>
                        <Eye size={14} />
                      </button>
                      {!tpl.builtin && (
                        <button
                          className={`${s.actionBtn} ${s.actionBtnDanger}`}
                          onClick={(e) => { e.stopPropagation(); void handleDelete(tpl) }}
                          title={t('project.pptTpl.delete')}
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}

      {/* 模板详情抽屉（复用同页 drawer 视觉；宽度/拖宽/守卫统一契约） */}
      {viewing && (
        <>
          <div className={s.drawerBackdrop} onClick={() => { if (!dr.suppressClick.current) setViewing(null) }} />
          <div className={s.drawer} style={{ width: dr.width }} onMouseDown={dr.onPanelMouseDown}>
            <div
              className={`${s.dragHandle} ${dr.dragging ? s.dragHandleActive : ''}`}
              onMouseDown={dr.onHandleMouseDown}
            />
            <div className={s.drawerHeader}>
              <h4>{viewing.id}</h4>
              <button className={s.closeBtn} onClick={() => setViewing(null)} title={t('common.close')}>×</button>
            </div>
            <div className={s.drawerBody}>
              <p className={s.detailMeta}>
                {kindLabel(viewing.kind)}
                {viewing.canvas_format ? ` | ${viewing.canvas_format}` : ''}
                {viewing.page_count ? ` | ${viewing.page_count}p` : ''}
              </p>
              <div className={s.detailFields}>
                <div className={s.detailField}>
                  <span className={s.fieldLabel}>{t('project.pptTpl.detail.summary')}</span>
                  <span>{viewing.summary || '—'}</span>
                </div>
                {viewing.primary_color && (
                  <div className={s.detailField}>
                    <span className={s.fieldLabel}>{t('project.pptTpl.detail.primaryColor')}</span>
                    <span className={s.pptColorCell}>
                      <span className={s.pptColorSwatch} style={{ background: viewing.primary_color }} />
                      <span className={s.mono}>{viewing.primary_color}</span>
                    </span>
                  </div>
                )}
                {viewing.keywords && viewing.keywords.length > 0 && (
                  <div className={s.detailField}>
                    <span className={s.fieldLabel}>{t('project.pptTpl.detail.keywords')}</span>
                    <span>{viewing.keywords.join('、')}</span>
                  </div>
                )}
                {viewing.page_types && viewing.page_types.length > 0 && (
                  <div className={s.detailField}>
                    <span className={s.fieldLabel}>{t('project.pptTpl.detail.pageTypes')}</span>
                    <span>{viewing.page_types.join('、')}</span>
                  </div>
                )}
              </div>

              {/* 模板规范（design_spec.md 正文：色板/字体/语气/页面清单，Brand/Style 主体内容） */}
              <div className={s.detailBlock}>
                <p className={s.detailSectionTitle}>{t('project.pptTpl.detail.spec')}</p>
                {specLoading ? (
                  <div className={s.emptyState}>
                    <Loader2 size={14} className={s.spinIcon} /> {t('project.pptTpl.detail.specLoading')}
                  </div>
                ) : spec ? (
                  <Markdown content={spec} className={s.pptSpecBody} />
                ) : (
                  <div className={s.emptyState}>{t('project.pptTpl.detail.noSpec')}</div>
                )}
              </div>

              {viewing.preview_pptx && (
                <div className={s.pptPreviewRow}>
                  <button className={s.primaryBtn} onClick={() => void handleDownloadPreview(viewing)}>
                    <Download size={14} /> {t('project.pptTpl.detail.downloadPreview')}
                  </button>
                </div>
              )}

              {viewing.page_svgs.length > 0 && (
                <div className={s.detailBlock}>
                  <p className={s.detailSectionTitle}>
                    {t('project.pptTpl.detail.pagePrototypes')} ({viewing.page_svgs.length})
                  </p>
                  <div className={s.pptPageGrid}>
                    {viewing.page_svgs.map((name) => (
                      <figure
                        key={name}
                        className={s.pptPageCell}
                        onClick={() => setZoomedSvg({
                          url: localFileUrl(`${viewing.workspace_path}/templates/${name}`),
                          name,
                        })}
                        title={t('project.pptTpl.detail.zoomHint')}
                      >
                        <img
                          className={s.pptPageThumb}
                          src={localFileUrl(`${viewing.workspace_path}/templates/${name}`)}
                          alt={name}
                          loading="lazy"
                        />
                        <figcaption className={s.pptPageName}>{name.replace(/\.svg$/, '')}</figcaption>
                      </figure>
                    ))}
                  </div>
                </div>
              )}
              {viewing.images.length > 0 && (
                <div className={s.detailBlock}>
                  <p className={s.detailSectionTitle}>
                    {t('project.pptTpl.detail.assets')} ({viewing.images.length})
                  </p>
                  <div className={s.pptAssetGrid}>
                    {viewing.images.map((name) => (
                      <figure
                        key={name}
                        className={s.pptPageCell}
                        onClick={() => setZoomedSvg({
                          url: localFileUrl(`${viewing.workspace_path}/images/${name}`),
                          name,
                        })}
                        title={t('project.pptTpl.detail.zoomHint')}
                      >
                        <img
                          className={s.pptAssetThumb}
                          src={localFileUrl(`${viewing.workspace_path}/images/${name}`)}
                          alt={name}
                          loading="lazy"
                        />
                        <figcaption className={s.pptPageName}>{name}</figcaption>
                      </figure>
                    ))}
                  </div>
                </div>
              )}
              {viewing.files.length > 0 && (
                <div className={s.detailBlock}>
                  <p className={s.detailSectionTitle}>
                    {t('project.pptTpl.detail.files')} ({viewing.files.length})
                  </p>
                  <div className={s.pptFileList}>
                    {viewing.files.map((f) => {
                      const name = f.path.split('/').pop() || f.path
                      const viewable = VIEWABLE_IMAGE_RE.test(f.path)
                      return (
                        <div
                          key={f.path}
                          className={s.pptFileRow}
                          onClick={viewable ? () => setZoomedSvg({
                            url: localFileUrl(`${viewing.workspace_path}/${f.path}`),
                            name,
                          }) : undefined}
                          title={viewable ? t('project.pptTpl.detail.zoomHint') : undefined}
                          data-viewable={viewable}
                        >
                          <span className={s.pptFileExt}>{name.split('.').pop()?.toUpperCase()}</span>
                          <span className={s.pptFilePath}>{f.path}</span>
                          <span className={s.pptFileSize}>{formatSize(f.size)}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
              {viewing.page_svgs.length === 0 && !viewing.has_workspace && (
                <div className={s.emptyState}>
                  <FileText size={14} /> {t('project.pptTpl.detail.noWorkspace')}
                </div>
              )}

              {!viewing.builtin && (
                <div className={s.pptPreviewRow}>
                  <button className={s.dangerBtn} onClick={() => void handleDelete(viewing)}>
                    <Trash2 size={14} /> {t('project.pptTpl.delete')}
                  </button>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* 页面原型放大查看（lightbox） */}
      {zoomedSvg && viewing && (
        <div
          className={s.pptSvgLightbox}
          onClick={() => setZoomedSvg(null)}
          role="dialog"
          aria-modal="true"
        >
          <img
            className={s.pptSvgLightboxImg}
            src={zoomedSvg.url}
            alt={zoomedSvg.name}
          />
          <span className={s.pptSvgLightboxName}>{zoomedSvg.name.replace(/\.svg$/, '')}</span>
        </div>
      )}

      {/* 新建模板向导 */}
      {wizardOpen && (
        <PptTemplateWizard
          onCreated={() => { setWizardOpen(false); void loadJobs(); void loadTemplates() }}
          onClose={() => setWizardOpen(false)}
        />
      )}
    </div>
  )
}
