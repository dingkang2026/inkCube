/** src/features/project/tabs/PptDetail.tsx — PPT 详情页（2.3 §7.1 PptDetail）
 *  状态条（阶段四段 + 页数进度）+ 门禁摘要条（awaiting 时内嵌原生
 *  confirm_ui iframe 方案 A）+ 创作页 SVG 预览网格
 *  + 执行时间线（缩小的任务 agent：TaskHistoryReplay 全量回放）。
 *  活跃期经 useActivity('ppt') 心跳驱动 3s 轮询；终态自动停轮（0.4 §6.2）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Download, FolderOpen, Pause, Play, Square, Trash2, AlertCircle } from 'lucide-react'
import s from './ppt.module.css'
import { useToast } from '../../knowledge/Toast'
import { useConfirm } from '../../../components/ConfirmDialog'
import { usePolling } from '../../../hooks/usePolling'
import { useEdgeRefetch } from '../../../hooks/useEdgeRefetch'
import { useActivity } from '../../activity/ActivityContext'
import { apiErrorText, translateStoredError } from '../../../i18n/errorCodes'
import { TaskHistoryReplay } from '../../task/TaskHistoryReplay'
import type { TaskMessage } from '../../../types'

interface PptRecord {
  id: string
  task_id: string
  title: string
  kind: string | null
  status: string
  stage: string | null
  progress: number | null
  page_count: number | null
  page_done: number | null
  workspace_path: string | null
  export_path: string | null
  error: string | null
  created_at: string
  task_status?: string | null
  confirm_ui_port?: number | null
  pages?: string[]
}

const STAGE_ORDER = ['intake', 'strategy', 'authoring', 'export'] as const

interface PptDetailProps {
  pptId: string
  onBack: () => void
  onDeleted: () => void
}

export function PptDetail({ pptId, onBack, onDeleted }: PptDetailProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  const [ppt, setPpt] = useState<PptRecord | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [messages, setMessages] = useState<TaskMessage[]>([])
  // 页面预览缓存：path → data URL；已拉取的页在轮询中不重复读
  const pageUrlsRef = useRef<Map<string, string>>(new Map())
  const [pageUrls, setPageUrls] = useState<Map<string, string>>(new Map())
  // 最新 pptId 快照：loadMessages 迟到响应丢弃（快速切换详情时旧任务
  // 在飞 IPC 不得回写新详情的时间线，review 2026-10-28）
  const pptIdRef = useRef(pptId)
  // 执行时间线默认展开态按任务阶段区分（2026-10-28 用户反馈）：进行中
  // （running/awaiting/paused/failed）默认展开便于跟踪，终态（completed/
  // cancelled）默认收起；null = 待首次拿到 ppt 状态后定初值，此后用户切换
  // 自由、状态翻转不再改写（避免正在查看时被强制收起打断）
  const [timelineOpen, setTimelineOpen] = useState<boolean | null>(null)

  // 执行时间线消息拉取：任务消息只增不减，按条数判变化（相等时返回原引用，
  // TaskHistoryReplay 的 useMemo 不重算）；失败不阻断主数据，下次轮询重试
  const loadMessages = async (taskId: string) => {
    try {
      const result: any = await window.electronAPI.invoke('tasks:getMessages', taskId)
      if (taskId !== pptIdRef.current) return // 已切走：迟到响应丢弃
      const next: TaskMessage[] = result?.messages || []
      setMessages((prev) => (next.length === prev.length ? prev : next))
    } catch (err) {
      console.error('Failed to load ppt messages:', err)
    }
  }

  const load = async () => {
    try {
      const result: any = await window.electronAPI.invoke('ppt:get', pptId)
      if (result?.ppt) {
        setPpt(result.ppt)
        setLoadError(false)
        // 执行时间线随本页轮询刷新（2026-10-28 复盘：原实现仅依赖
        // [task_id, status]，running 回合中时间线静止被误读为卡死；
        // 现每次 load 顺带拉消息，终态停轮后由 useEdgeRefetch 补拉兼底）
        if (result.ppt.task_id) await loadMessages(result.ppt.task_id)
      } else {
        setLoadError(true)
      }
    } catch (err) {
      console.error('Failed to load ppt detail:', err)
      setLoadError(true)
    }
  }

  useEffect(() => { void load() }, [pptId])

  // 活跃期轮询：心跳驱动（0.4 §6.2 谓词收编），终态自动停轮
  const { items: activePpts } = useActivity('ppt')
  const isActive = activePpts.some((a) => a.entity_id === pptId)
  usePolling({ name: 'ppt-detail', enabled: isActive, interval: 3000, task: () => load() })
  // 终态边沿补拉：心跳停轮可能早于最后一次状态翻转
  useEdgeRefetch(isActive, () => { void load() })

  // 新页出现（pages 增长）→ 拉取未缓存的页（SVG 内容 base64 已由后端编码）
  useEffect(() => {
    const pages = ppt?.pages || []
    const taskId = ppt?.task_id
    if (!taskId || pages.length === 0) return
    let cancelled = false
    ;(async () => {
      for (const p of pages) {
        if (cancelled) return
        if (pageUrlsRef.current.has(p)) continue
        try {
          const result: any = await window.electronAPI.invoke('tasks:readFile', { taskId, path: p })
          if (result?.type === 'image' && result.data) {
            pageUrlsRef.current.set(p, `data:${result.mime};base64,${result.data}`)
            setPageUrls(new Map(pageUrlsRef.current))
          } else {
            pageUrlsRef.current.set(p, '') // 拉取失败也缓存空串，避免轮询期反复重试
            setPageUrls(new Map(pageUrlsRef.current))
          }
        } catch {
          pageUrlsRef.current.set(p, '')
          setPageUrls(new Map(pageUrlsRef.current))
        }
      }
    })()
    return () => { cancelled = true }
  }, [ppt?.pages, ppt?.task_id])

  // 切换 ppt：清预览缓存（不同 ppt 的 task workspace 不同）+ 重置时间线展开态
  // + 同步 pptId 快照（迟到响应守卫基准）
  useEffect(() => {
    pptIdRef.current = pptId
    pageUrlsRef.current = new Map()
    setPageUrls(new Map())
    setTimelineOpen(null)
  }, [pptId])

  // 首次拿到 ppt 状态时定时间线初始展开态（详见 timelineOpen 注释）
  useEffect(() => {
    if (!ppt || timelineOpen !== null) return
    setTimelineOpen(ppt.status !== 'completed' && ppt.status !== 'cancelled')
  }, [ppt, timelineOpen])

  const stageIndex = useMemo(
    () => Math.max(0, STAGE_ORDER.indexOf((ppt?.stage || 'intake') as typeof STAGE_ORDER[number])),
    [ppt?.stage],
  )

  const action = async (fn: () => Promise<any>, okText: string, failText: string) => {
    try {
      const result: any = await fn()
      if (result?.error) {
        showToast(failText.replace('{error}', apiErrorText(result.code, result.error, t)), 'error')
      } else {
        showToast(okText, 'success')
        await load()
      }
    } catch (err: any) {
      showToast(failText.replace('{error}', String(err?.message || err)), 'error')
    }
  }

  const handleResume = () => action(
    () => window.electronAPI.invoke('ppt:resume', pptId),
    t('project.ppt.toast.resumed'), t('project.ppt.toast.resumeFailed'),
  )
  const handlePause = () => action(
    () => window.electronAPI.invoke('ppt:pause', pptId),
    t('project.ppt.toast.pauseRequested'), t('project.ppt.toast.resumeFailed'),
  )
  const handleCancel = async () => {
    if (!(await confirmDialog({ title: t('project.ppt.detail.cancel'), message: t('project.ppt.detail.cancelConfirm'), danger: true }))) return
    action(
      () => window.electronAPI.invoke('ppt:cancel', pptId),
      t('project.ppt.toast.cancelled'), t('project.ppt.toast.cancelFailed'),
    )
  }
  const handleDelete = async () => {
    if (!(await confirmDialog({ title: t('project.ppt.detail.delete'), message: t('project.ppt.detail.deleteConfirm'), danger: true }))) return
    try {
      const result: any = await window.electronAPI.invoke('ppt:delete', pptId)
      if (result?.error) {
        showToast(t('project.ppt.toast.deleteFailed', { error: apiErrorText(result.code, result.error, t) }), 'error')
        return
      }
      showToast(t('project.ppt.toast.deleted'), 'success')
      onDeleted()
    } catch (err: any) {
      showToast(t('project.ppt.toast.deleteFailed', { error: String(err?.message || err) }), 'error')
    }
  }
  const handleDownload = async () => {
    if (!ppt?.workspace_path || !ppt.export_path) return
    try {
      const result: any = await window.electronAPI.invoke('ppt:downloadExport', {
        workspacePath: ppt.workspace_path, exportPath: ppt.export_path, title: ppt.title,
      })
      if (result?.canceled) return
      if (result?.error) {
        showToast(t('project.ppt.toast.downloadFailed', { error: result.error }), 'error')
      } else {
        showToast(t('project.ppt.toast.downloaded', { path: result.path }), 'success')
      }
    } catch (err: any) {
      showToast(t('project.ppt.toast.downloadFailed', { error: String(err?.message || err) }), 'error')
    }
  }
  const handleReveal = () => {
    if (!ppt?.workspace_path || !ppt.export_path) return
    void window.electronAPI.invoke('ppt:revealExport', {
      workspacePath: ppt.workspace_path, exportPath: ppt.export_path,
    })
  }

  if (loadError) {
    return (
      <div className={s.detail}>
        <div className={s.detailHeader}>
          <button className={s.backBtn} onClick={onBack}><ArrowLeft size={16} /> {t('project.ppt.detail.back')}</button>
        </div>
        <div className={s.detailError}><AlertCircle size={16} /> {t('project.ppt.detail.loadFailed')}</div>
      </div>
    )
  }
  if (!ppt) {
    return <div className={s.detail}><div className={s.detailLoading}>{t('common.loading')}</div></div>
  }

  const awaiting = ppt.status === 'awaiting_input'
  const showIframe = awaiting && !!ppt.confirm_ui_port
  const statusKey = `project.ppt.status.${ppt.status}`

  return (
    <div className={s.detail}>
      <div className={s.detailHeader}>
        <button className={s.backBtn} onClick={onBack}><ArrowLeft size={16} /> {t('project.ppt.detail.back')}</button>
        <h3 className={s.detailTitle}>{ppt.title}</h3>
        <span className={`${s.statusChip} ${s[`status_${ppt.status}`] || ''}`}>{t(statusKey)}</span>
        {/* 操作组随头部常驻（2026-10-28：原沉底，长页滚动后不可达） */}
        <div className={s.detailActions}>
          {(ppt.status === 'paused' || ppt.status === 'failed' || awaiting) && (
            <button className={s.primaryBtn} onClick={handleResume}><Play size={14} /> {t('project.ppt.detail.resume')}</button>
          )}
          {ppt.status === 'running' && (
            <button className={s.secondaryBtn} onClick={handlePause} title={t('project.ppt.detail.pauseTip')}>
              <Pause size={14} /> {t('project.ppt.detail.pause')}
            </button>
          )}
          {(ppt.status === 'running' || ppt.status === 'paused' || awaiting) && (
            <button className={s.dangerBtn} onClick={handleCancel}><Square size={14} /> {t('project.ppt.detail.cancel')}</button>
          )}
          {ppt.status === 'completed' && ppt.export_path && (
            <>
              <button className={s.primaryBtn} onClick={handleDownload}><Download size={14} /> {t('project.ppt.detail.download')}</button>
              <button className={s.secondaryBtn} onClick={handleReveal}><FolderOpen size={14} /> {t('project.ppt.detail.reveal')}</button>
            </>
          )}
          <button className={s.dangerBtn} onClick={handleDelete}><Trash2 size={14} /> {t('project.ppt.detail.delete')}</button>
        </div>
      </div>

      {/* 状态条：阶段四段 + 页数进度（2.3 §7.2 running 行） */}
      <div className={s.statusBar}>
        <div className={s.stageTrack}>
          {STAGE_ORDER.map((st, i) => (
            <div
              key={st}
              className={`${s.stageCell} ${i < stageIndex ? s.stageDone : ''} ${i === stageIndex ? s.stageActive : ''}`}
            >
              {t(`project.ppt.stage.${st}`)}
            </div>
          ))}
        </div>
        {/* 进度行：页数已规划（authoring）显示页码+百分比；其余阶段显示阶段语义文案
            ——progress 语义为 authoring 按页计（2.3 §5.1），intake/strategy/export 恒 0，
            裸展示 0% 会被读作卡死（2026-10-28 复盘） */}
        <div className={s.progressLine}>
          {(ppt.status === 'running' || ppt.status === 'paused') && (
            !!ppt.page_count ? (
              <>
                <span className={s.pageProgress}>
                  {t('project.ppt.pageProgress', { done: ppt.page_done || 0, count: ppt.page_count })}
                </span>
                <span className={s.progressPercent}>
                  {t('project.ppt.progressPercent', { percent: Math.round((ppt.progress || 0) * 100) })}
                </span>
              </>
            ) : (
              <span className={s.pageProgress}>
                {t(`project.ppt.stageHint.${ppt.stage || 'intake'}`)}
              </span>
            )
          )}
        </div>
      </div>

      {/* 门禁摘要条（2.3 §7.1 PptConfirmCard）：awaiting 时内嵌确认页 */}
      {awaiting && (
        <div className={s.confirmBanner}>
          <p className={s.confirmBannerText}>{showIframe ? t('project.ppt.detail.confirmBanner') : t('project.ppt.detail.confirmClosed')}</p>
          {showIframe && (
            <iframe
              className={s.confirmFrame}
              src={`http://127.0.0.1:${ppt.confirm_ui_port}/`}
              title="ppt confirm"
            />
          )}
          {!showIframe && (
            <button className={s.primaryBtn} onClick={handleResume}>
              <Play size={14} /> {t('project.ppt.detail.resume')}
            </button>
          )}
        </div>
      )}

      {/* 创作页预览网格 */}
      <div className={s.sectionTitle}>{t('project.ppt.detail.pages')}</div>
      {(ppt.pages?.length || 0) === 0 ? (
        <div className={s.emptyHint}>{t('project.ppt.detail.noPages')}</div>
      ) : (
        <div className={s.pageGrid}>
          {(ppt.pages || []).map((p, i) => (
            <figure key={p} className={s.pageCell}>
              {pageUrls.get(p)
                ? <img className={s.pageImg} src={pageUrls.get(p)} alt={`page ${i + 1}`} />
                : <div className={s.pagePlaceholder}>{i + 1}</div>}
              <figcaption className={s.pageNo}>{i + 1}</figcaption>
            </figure>
          ))}
        </div>
      )}

      {/* 执行时间线（2.3 §7.1，缩小的任务 agent：全量迭代卡回放）——
          进行中默认展开/终态默认收起（状态感知初值，见 timelineOpen 注释），
          展开后 70vh 内滚（规模无关布局，2026-10-28） */}
      <div className={s.sectionTitle}>{t('project.ppt.detail.timeline')}</div>
      {messages.length === 0 ? (
        <div className={s.emptyHint}>{t('project.ppt.detail.noTimeline')}</div>
      ) : (
        <button
          type="button"
          className={s.timelineSummary}
          onClick={() => setTimelineOpen((v) => !v)}
        >
          <span className={`${s.timelineChevron} ${timelineOpen ? s.timelineChevronOpen : ''}`}>▸</span>
          {t('project.ppt.detail.timelineSummary', { count: messages.length })}
        </button>
      )}
      {timelineOpen && messages.length > 0 && (
        <div className={s.timeline}>
          <TaskHistoryReplay messages={messages} />
        </div>
      )}

      {ppt.error && (
        <div className={s.errorBox}>
          <div className={s.errorTitle}>{t('project.ppt.detail.errorTitle')}</div>
          <div className={s.errorText}>{translateStoredError(ppt.error, t)}</div>
        </div>
      )}

      {/* MIT 归因说明（2.3 §1.3 合规） */}
      <div className={s.mitNote}>{t('project.ppt.detail.mitNote')}</div>
    </div>
  )
}

