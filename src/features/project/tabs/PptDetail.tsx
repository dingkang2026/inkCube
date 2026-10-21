/** src/features/project/tabs/PptDetail.tsx — PPT 详情页（2.3 §7.1 PptDetail + PptConfirmCard）
 *  状态条（阶段四段 + 页数进度）+ 门禁摘要条（awaiting 时：Generate 内嵌原生
 *  confirm_ui iframe 方案 A；Enhance 为结构化确认卡 §7.2）+ 创作页 SVG 预览网格
 *  + 执行时间线（缩小的任务 agent：TaskHistoryReplay 全量回放）
 *  + 操作组（含 E3 成品增强入口：deck + completed → 发起增强）。
 *  活跃期经 useActivity('ppt') 心跳驱动 3s 轮询；终态自动停轮（0.4 §6.2）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Download, FolderOpen, Pause, Play, Square, Trash2, AlertCircle, Wand2 } from 'lucide-react'
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
  /** E3 成品增强：从此交付物发起增强成功后回调（父组件切到新任务详情跟踪） */
  onEnhanced?: (newPptId: string) => void
}

export function PptDetail({ pptId, onBack, onDeleted, onEnhanced }: PptDetailProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  const [ppt, setPpt] = useState<PptRecord | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [messages, setMessages] = useState<TaskMessage[]>([])
  // 页面预览缓存：path → data URL；已拉取的页在轮询中不重复读
  const pageUrlsRef = useRef<Map<string, string>>(new Map())
  const [pageUrls, setPageUrls] = useState<Map<string, string>>(new Map())
  // E3 增强门禁卡：调整意见输入 + 发起中防抖
  const [gateNotes, setGateNotes] = useState('')
  const [enhancing, setEnhancing] = useState(false)

  const load = async () => {
    try {
      const result: any = await window.electronAPI.invoke('ppt:get', pptId)
      if (result?.ppt) {
        setPpt(result.ppt)
        setLoadError(false)
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

  // 执行时间线（TaskHistoryReplay 全量渲染；依赖 [task_id, status]，running 中途
  // 不随心跳刷新——增量以 JobCard 面板为准，状态跃迁/重进页面时重拉）
  useEffect(() => {
    if (!ppt?.task_id) return
    let cancelled = false
    ;(async () => {
      try {
        const result: any = await window.electronAPI.invoke('tasks:getMessages', ppt.task_id)
        if (!cancelled) setMessages(result.messages || [])
      } catch (err) {
        console.error('Failed to load ppt messages:', err)
      }
    })()
    return () => { cancelled = true }
  }, [ppt?.task_id, ppt?.status])

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

  // 删除时清预览缓存（不同 ppt 的 task workspace 不同）
  useEffect(() => {
    pageUrlsRef.current = new Map()
    setPageUrls(new Map())
  }, [pptId])

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

  // E3 成品增强：以此 deck 交付物为源创建增强任务，成功后切换到新任务详情
  const handleEnhance = async () => {
    if (enhancing) return
    if (!(await confirmDialog({
      title: t('project.ppt.detail.enhanceBtn'),
      message: t('project.ppt.detail.enhanceConfirm'),
    }))) return
    setEnhancing(true)
    try {
      const result: any = await window.electronAPI.invoke('ppt:enhance', { pptId })
      if (result?.ppt?.id) {
        showToast(t('project.ppt.toast.enhanceStarted'), 'success')
        onEnhanced?.(result.ppt.id)
      } else {
        showToast(t('project.ppt.toast.enhanceFailed', {
          error: apiErrorText(result?.code, result?.error || 'unknown', t),
        }), 'error')
      }
    } catch (err: any) {
      showToast(t('project.ppt.toast.enhanceFailed', { error: String(err?.message || err) }), 'error')
    } finally {
      setEnhancing(false)
    }
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
        {ppt.kind === 'enhanced' && <span className={s.kindBadge}>{t('project.ppt.enhancedBadge')}</span>}
        <span className={`${s.statusChip} ${s[`status_${ppt.status}`] || ''}`}>{t(statusKey)}</span>
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
        <div className={s.progressLine}>
          {!!ppt.page_count && (
            <span className={s.pageProgress}>
              {t('project.ppt.pageProgress', { done: ppt.page_done || 0, count: ppt.page_count })}
            </span>
          )}
          {(ppt.status === 'running' || ppt.status === 'paused') && (
            <span className={s.progressPercent}>
              {t('project.ppt.progressPercent', { percent: Math.round((ppt.progress || 0) * 100) })}
            </span>
          )}
        </div>
      </div>

      {/* 门禁摘要条（2.3 §7.1 PptConfirmCard）：Generate=内嵌确认页；
          Enhance=结构化确认卡（stage 区分门禁类型，§7.2 awaiting 行） */}
      {awaiting && ppt.kind === 'enhanced' && (
        <div className={s.confirmBanner}>
          <p className={s.confirmBannerText}>
            {ppt.stage === 'authoring'
              ? t('project.ppt.detail.gateAudioDesc')
              : t('project.ppt.detail.gatePlanDesc')}
          </p>
          <input
            className={s.gateNotes}
            value={gateNotes}
            onChange={(e) => setGateNotes(e.target.value)}
            placeholder={t('project.ppt.detail.gatePlaceholder')}
          />
          <div className={s.gateActions}>
            <button
              className={s.primaryBtn}
              onClick={() => {
                setGateNotes('')
                action(
                  () => window.electronAPI.invoke('ppt:answer', { pptId, text: null }),
                  t('project.ppt.toast.resumed'), t('project.ppt.toast.resumeFailed'),
                )
              }}
            >
              {ppt.stage === 'authoring'
                ? t('project.ppt.detail.gateAudioConfirm')
                : t('project.ppt.detail.gatePlanConfirm')}
            </button>
            <button
              className={s.secondaryBtn}
              disabled={!gateNotes.trim()}
              onClick={() => {
                const text = gateNotes.trim()
                setGateNotes('')
                action(
                  () => window.electronAPI.invoke('ppt:answer', { pptId, text }),
                  t('project.ppt.toast.resumed'), t('project.ppt.toast.resumeFailed'),
                )
              }}
            >
              {t('project.ppt.detail.gateSendNotes')}
            </button>
          </div>
        </div>
      )}
      {awaiting && ppt.kind !== 'enhanced' && (
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

      {/* 执行时间线（2.3 §7.1，缩小的任务 agent：全量迭代卡回放） */}
      <div className={s.sectionTitle}>{t('project.ppt.detail.timeline')}</div>
      {messages.length === 0 ? (
        <div className={s.emptyHint}>{t('project.ppt.detail.noTimeline')}</div>
      ) : (
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

      {/* 操作组 */}
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
            {ppt.kind === 'deck' && (
              <button
                className={s.secondaryBtn}
                onClick={handleEnhance}
                disabled={enhancing}
                title={t('project.ppt.detail.enhanceTip')}
              >
                <Wand2 size={14} /> {t('project.ppt.detail.enhanceBtn')}
              </button>
            )}
          </>
        )}
        <button className={s.dangerBtn} onClick={handleDelete}><Trash2 size={14} /> {t('project.ppt.detail.delete')}</button>
      </div>

      {/* MIT 归因说明（2.3 §1.3 合规） */}
      <div className={s.mitNote}>{t('project.ppt.detail.mitNote')}</div>
    </div>
  )
}

