/** src/features/notes/NoteWizardDrawer.tsx — 纪要两步向导抽屉（2.13 §4）。
 *
 * 输入步（标题/日期/文本/现场图片本地预览；存草稿可随时暂存退出，新图不上传）
 * → 生成（定稿时才真正上传图片 → create/update → structure 202；structured 行
 * 重发=重新生成，解析与结构化全程进度步呈现，2.13 定稿驱动）
 * → 进度态（useActivity('note_structuring') 心跳 + listPending 轮询判定流转）
 * → 确认步（正文与行动项全可编辑，confirm 分步幂等；failed_tasks 红字 + 重试保存；
 * 上一步回输入步改原文后可重新生成）。
 * 草稿恢复：宿主经 resumeNoteId 指定行，按 status 落步骤（draft→输入预填、
 * structuring→进度、structured→确认）。形态对齐 StickyDrawer（遮罩 + 右侧面板
 * + 左缘拖宽，useDrawerWidth 60vw 契约）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  AlertCircle, Check, ImagePlus, Loader2, Plus, Trash2, X,
} from 'lucide-react'
import { useDrawerWidth } from '../../hooks/useDrawerWidth'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { useActivity } from '../activity/ActivityContext'
import { useToast } from '../knowledge/Toast'
import { useConfirm } from '../../components/ConfirmDialog'
import {
  detectTranscript,
  type ConfirmActionItem,
  type MeetingNote,
  type MeetingNoteErrorCode,
  type NoteDraftPayload,
} from './types'
import s from './NoteWizardDrawer.module.css'

/** 宽度下限（上限 92vw 随视口、默认 60vw 随视口，见 useDrawerWidth） */
const MIN_W = 460

/** 现场图片上限（防误选批量上传；超出提示） */
const MAX_IMAGES = 9

type Step = 'input' | 'progress' | 'failed' | 'confirm'

interface WizardImage {
  /** 上传完成后的素材 file id（定稿前未上传为 null；恢复草稿回源图有值） */
  fileId: string | null
  /** 本地文件引用（定稿时上传用；恢复草稿回源图为 null） */
  file: File | null
  name: string
  url: string
}

interface ActiveFileRow {
  id: string
  image_status?: string
  error_message?: string
}

/** invoke 结果错误归一（structuredPythonResponse 失败返回 {ok:false, error}） */
function errText(res: unknown, fallback: string): string {
  if (res && typeof res === 'object' && 'error' in res) {
    const e = (res as { error?: unknown }).error
    if (typeof e === 'string' && e) return e
  }
  return fallback
}

function invoke<T = Record<string, unknown>>(channel: string, payload?: unknown): Promise<T | null> {
  return window.electronAPI.invoke(channel, payload) as Promise<T | null>
}

function todayISO(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result ?? '')
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(file)
  })
}

/** 本地图片 → inkcube:// 协议 URL（与 ImageGallery.localFileUrl 同规则，CSP img-src 已放行）；
 *  用于恢复草稿时回源缩略图（fileId → images[0].local_path）。 */
function localFileUrl(filePath: string): string {
  // Normalize Windows backslashes to forward slashes
  const normalized = filePath.replace(/\\/g, '/')
  // Use query param to avoid drive letter (D:) being parsed as hostname
  return `inkcube://app?file=${encodeURIComponent(normalized)}`
}

/** 结构化失败错误码 → i18n 键（meeting_notes_service 落库值 + 启动清扫 interrupted） */
const ERROR_KEYS: Record<string, string> = {
  LLM_BAD_RESPONSE: 'meetingNotes.error.llmBadResponse',
  LLM_FAILED: 'meetingNotes.error.llmFailed',
  NO_MATERIAL: 'meetingNotes.error.noMaterial',
  IMAGE_FAILED: 'meetingNotes.error.imageFailed',
  IMAGES_TIMEOUT: 'meetingNotes.error.imagesTimeout',
  interrupted: 'meetingNotes.error.interrupted',
}

interface NoteWizardDrawerProps {
  projectId: string
  open: boolean
  /** 草稿恢复：宿主经提示条点击传入目标行 id（open 变 true 时消费） */
  resumeNoteId: string | null
  onClose: () => void
  /** 行集变化（保存成功/放弃草稿）后通知宿主刷新草稿提示条 */
  onChanged?: () => void
}

export function NoteWizardDrawer({ projectId, open, resumeNoteId, onClose, onChanged }: NoteWizardDrawerProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirm = useConfirm()
  const dr = useDrawerWidth({ min: MIN_W })
  // 心跳（0.4 §6.2）：note_structuring 驱动进度态与终态边沿；项目 file 活动
  // 门控图片状态轮询
  const { items: structuringItems } = useActivity('note_structuring')
  const { items: fileActItems } = useActivity('file')

  const [step, setStep] = useState<Step>('input')
  const [noteId, setNoteId] = useState<string | null>(null)
  // 当前操作行的 status（受理→structuring、resume/settle 按行值回填）；
  // structured 时输入步生成钮呈「重新生成」（行已有产物，重发=回 draft 重走）
  const [rowStatus, setRowStatus] = useState<MeetingNote['status'] | null>(null)
  // 输入步
  const [title, setTitle] = useState('')
  const [occurredAt, setOccurredAt] = useState(todayISO())
  const [rawText, setRawText] = useState('')
  const [images, setImages] = useState<WizardImage[]>([])
  const [submitting, setSubmitting] = useState(false)
  // 进度/失败步
  const [lastError, setLastError] = useState<MeetingNoteErrorCode | null>(null)
  // 确认步
  const [contentMd, setContentMd] = useState('')
  const [actionItems, setActionItems] = useState<ConfirmActionItem[]>([])
  const [saveToMaterials, setSaveToMaterials] = useState(true) // 2.14：正文入库素材，可退出
  const [saving, setSaving] = useState(false)
  const [failedTasks, setFailedTasks] = useState<string[] | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)

  const resetInput = useCallback(() => {
    setNoteId(null)
    setRowStatus(null)
    setTitle('')
    setOccurredAt(todayISO())
    setRawText('')
    // 函数式更新内 revoke 本地 blob URL（revoke 幂等；恢复草稿回源的
    // inkcube:// 协议 URL 非 blob，revoke 为 no-op 无害）
    setImages((prev) => {
      for (const i of prev) if (i.url) URL.revokeObjectURL(i.url)
      return []
    })
    setLastError(null)
    setContentMd('')
    setActionItems([])
    setSaveToMaterials(true)
    setFailedTasks(null)
  }, [])

  /** structured 行 → 确认步编辑模型（「待确认：」前缀项默认不勾，2.13 §4）。
   *  同时预填标题/日期：上一步回输入步后重新生成的 update 载荷不抹行标题与日期 */
  const enterConfirm = useCallback((row: MeetingNote) => {
    setTitle(row.title ?? '')
    setOccurredAt(row.occurred_at ?? todayISO())
    let payload: Partial<NoteDraftPayload> = {}
    try { payload = JSON.parse(row.draft_md ?? '{}') } catch { /* 容错空载荷 */ }
    setContentMd(payload.content_md ?? '')
    setActionItems((payload.action_items ?? []).map((it) => ({
      title: it?.title ?? '',
      selected: !(it?.title ?? '').startsWith('待确认：'),
      dueDate: null,
    })))
    setLastError(null)
    setFailedTasks(null)
    setStep('confirm')
  }, [])

  /** 打开即重置；带 resumeNoteId 时按 status 落步骤（行已删则停留输入步） */
  useEffect(() => {
    if (!open) return
    resetInput()
    setStep('input')
    if (!resumeNoteId) return
    let cancelled = false
    void (async () => {
      const r = await invoke<{ ok?: boolean; notes?: MeetingNote[] }>(
        'meetingNotes:listPending', { projectId })
      if (cancelled) return
      const row = (r?.notes ?? []).find((n) => n.id === resumeNoteId)
      if (!row) return
      setNoteId(row.id)
      setRowStatus(row.status)
      if (row.status === 'structured') { enterConfirm(row); return }
      if (row.status === 'structuring') { setStep('progress'); return }
      // draft：输入步预填；已传图片先落占位格再回源缩略图（fileId →
      // knowledge:getImages → images[0].local_path → inkcube:// URL；
      // 单图回源失败/无图静默保持占位，不阻断恢复）
      setTitle(row.title ?? '')
      setOccurredAt(row.occurred_at ?? todayISO())
      setRawText(row.raw_text ?? '')
      const fids = row.image_file_ids ?? []
      setImages(fids.map((fid) => (
        { fileId: fid, file: null, name: '', url: '' })))
      if (fids.length > 0) {
        const urls = await Promise.all(fids.map(async (fid) => {
          try {
            const g = await invoke<{ images?: { local_path?: string }[] }>(
              'knowledge:getImages', { projectId, fileId: fid })
            const p = (g?.images ?? [])[0]?.local_path
            return p ? localFileUrl(p) : ''
          } catch { return '' }
        }))
        if (cancelled) return
        setImages((prev) => prev.map((img) => {
          const idx = img.fileId ? fids.indexOf(img.fileId) : -1
          return idx >= 0 && urls[idx] ? { ...img, url: urls[idx] } : img
        }))
      }
    })()
    return () => { cancelled = true }
  }, [open, resumeNoteId, projectId, resetInput, enterConfirm])

  // Esc 关闭（输入框聚焦时忽略——对齐 ScheduleDrawer 两段式语义）；进度态可关（后台继续）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // ── 图片解析状态：项目有 file 活动时轮询活跃集（含 error 态），无活动即全就绪 ──
  const hasProjectFileActivity = fileActItems.some(
    (a) => a.project_id === projectId && (a.count ?? 0) > 0)
  const [activeFileMap, setActiveFileMap] = useState<Record<string, ActiveFileRow>>({})
  const hasTrackedImages = images.some((i) => i.fileId)

  usePolling({
    name: 'note-wizard-images',
    enabled: open && step === 'input' && hasTrackedImages && hasProjectFileActivity,
    interval: 2000,
    task: async () => {
      const r = await invoke<{ files?: ActiveFileRow[] }>('knowledge:getActiveFiles', projectId)
      const map: Record<string, ActiveFileRow> = {}
      for (const f of r?.files ?? []) map[f.id] = f
      setActiveFileMap(map)
    },
  })

  type ImageState = 'busy' | 'error' | 'ready'
  const imageState = (img: WizardImage): ImageState => {
    if (!img.fileId) return 'ready' // 定稿驱动（2.13）：未上传即本地就绪，定稿时才上传解析
    if (!hasProjectFileActivity) return 'ready'
    const af = activeFileMap[img.fileId]
    if (!af) return 'ready' // 不在活跃集 = 终态（解析/描述完成）
    if (af.image_status === 'error') return 'error'
    return 'busy'
  }

  const busyCount = images.filter((i) => imageState(i) === 'busy').length
  const errorCount = images.filter((i) => imageState(i) === 'error').length
  // 恢复草稿场景旧图解析中（busy）未就绪；新选图定稿驱动恒就绪
  const imagesReady = busyCount === 0
  // 生成门控（spec §3）：图片全就绪且（文本或图片非空）
  const canGenerate = !submitting && imagesReady
    && (rawText.trim().length > 0 || images.length > 0)
  // 新选图（未上传）：定稿驱动仅本地预览，生成时才上传解析（footer 提示）
  const newImages = images.filter((i) => !i.fileId)
  // 存草稿门控：任一输入非空即可暂存（标题/正文/图片）；新图不上传零痕迹
  const canSaveDraft = !submitting
    && (title.trim().length > 0 || rawText.trim().length > 0 || images.length > 0)
  // structured 行回输入步（上一步）→ 生成钮呈「重新生成」
  const isRegen = rowStatus === 'structured'

  /** 选图：仅本地 blob 预览（2.13 定稿驱动：定稿时才真正上传，未定稿零服务端痕迹） */
  const addImages = (files: FileList) => {
    const room = MAX_IMAGES - images.length
    if (room <= 0) { showToast(t('meetingNotes.tooManyImages', { max: MAX_IMAGES }), 'error'); return }
    const added: WizardImage[] = Array.from(files).slice(0, room).map((f) => ({
      fileId: null, file: f, name: f.name, url: URL.createObjectURL(f),
    }))
    setImages((prev) => [...prev, ...added])
  }

  const removeImage = (img: WizardImage) => {
    URL.revokeObjectURL(img.url)
    setImages((prev) => prev.filter((x) => x !== img))
  }

  /** 生成（定稿）：新图此刻真正上传（2.13 定稿驱动）→ update/create 补全载荷
   *  （imageFileIds 全集）→ structure 202；解析与结构化全程进度步呈现 */
  const generate = async () => {
    if (!canGenerate) return
    setSubmitting(true)
    try {
      // ① 定稿上传：恢复草稿的已传图复用 fileId，新图此刻上传（任一失败中止留在输入步）
      const ids: string[] = []
      for (let i = 0; i < images.length; i++) {
        const img = images[i]
        if (img.fileId) { ids.push(img.fileId); continue }
        if (!img.file) { showToast(t('meetingNotes.imageDataMissing'), 'error'); return }
        const base64 = await readFileAsBase64(img.file)
        const res = await invoke<{ success?: boolean; file_id?: string; message?: string; error?: string }>(
          'knowledge:uploadFile',
          { projectId, filename: img.file.name, fileType: img.file.name.slice(img.file.name.lastIndexOf('.')), data: base64 })
        const fid = res?.file_id
        if (!res || res.success !== true || !fid) {
          showToast(`${img.name}: ${res?.message || res?.error || t('meetingNotes.uploadFailed')}`, 'error'); return
        }
        ids.push(fid)
        setImages((prev) => prev.map((x, j) => (j === i ? { ...x, fileId: fid } : x)))
      }
      // ② 门控兜底（保留）：恢复草稿场景已传图可能仍在解析（心跳空闲档空窗时
      // imagesReady 误判就绪），单次 RPC 复核活跃集，仍 busy 即中止提示
      const chk = await invoke<{ files?: ActiveFileRow[] }>('knowledge:getActiveFiles', projectId)
      const busyIds = new Set((chk?.files ?? []).map((f) => f.id))
      const busyN = ids.filter((fid) => busyIds.has(fid)).length
      if (busyN > 0) {
        showToast(t('meetingNotes.imagesPending', { done: ids.length - busyN, total: ids.length }), 'error')
        return
      }
      let nid = noteId
      if (nid) {
        const u = await invoke('meetingNotes:update', {
          projectId, id: nid, title: title.trim() || null,
          occurredAt: occurredAt || null, rawText: rawText.trim() || null,
          imageFileIds: ids,
        })
        if (!u || (u as { ok?: boolean }).ok !== true) {
          showToast(errText(u, t('meetingNotes.saveFailed')), 'error'); return
        }
      } else {
        const c = await invoke<{ ok?: boolean; note?: { id: string } }>('meetingNotes:create', {
          projectId, title: title.trim() || null,
          occurredAt: occurredAt || null, rawText: rawText.trim() || null,
          imageFileIds: ids,
        })
        if (!c || c.ok !== true || !c.note?.id) {
          showToast(errText(c, t('meetingNotes.createFailed')), 'error'); return
        }
        nid = c.note.id
        setNoteId(nid)
      }
      const st = await invoke('meetingNotes:structure', { projectId, id: nid })
      if (!st || (st as { ok?: boolean }).ok !== true) {
        showToast(errText(st, t('meetingNotes.structureFailed')), 'error'); return
      }
      setLastError(null)
      setRowStatus('structuring')
      setStep('progress')
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  // 进度态流转（0.4 §3.1 零新增轮询）：心跳 active→终态边沿拉一次行状态——
  // structured → 确认步；draft+last_error → 失败态；行消失（异常）回输入步
  const structuringHere = !!noteId && step === 'progress'
    && structuringItems.some((a) => a.entity_id === noteId)

  const settleAfterStructuring = useCallback(async () => {
    if (!noteId) return
    const r = await invoke<{ ok?: boolean; notes?: MeetingNote[] }>(
      'meetingNotes:listPending', { projectId })
    const row = (r?.notes ?? []).find((n) => n.id === noteId)
    // 行消失（异常清理）：整轮重置（含 noteId/rowStatus，防残留断链），停留输入步
    if (!row) { resetInput(); setStep('input'); return }
    setRowStatus(row.status)
    if (row.status === 'structured') { enterConfirm(row); return }
    if (row.status === 'draft') {
      setLastError(row.last_error ?? 'LLM_FAILED')
      setStep('failed')
    }
    // status 仍 structuring（心跳间隙）→ 等待下一个边沿
  }, [noteId, projectId, enterConfirm, resetInput])

  useEdgeRefetch(structuringHere, () => { void settleAfterStructuring() })

  // 兜底轮询（§2.7 裁量扩展：页面级明细查询轮询，同 note-wizard-images 先例）——
  // 心跳空闲档 15s，LLM 秒完时 structuringHere 可能从未见过 true、无边沿可拉；
  // 4s 兜底保证进度态终态流转不依赖「曾观测到活跃」（enabled 翻 true 立即查一次）
  usePolling({
    name: 'note-wizard-settle',
    enabled: open && step === 'progress',
    interval: 4000,
    task: () => settleAfterStructuring(),
  })

  /** 失败态重试：status 已回 draft，直接重发 structure */
  const retryStructure = async () => {
    if (!noteId) return
    setSubmitting(true)
    try {
      const st = await invoke('meetingNotes:structure', { projectId, id: noteId })
      if (!st || (st as { ok?: boolean }).ok !== true) {
        showToast(errText(st, t('meetingNotes.structureFailed')), 'error'); return
      }
      setLastError(null)
      setRowStatus('structuring')
      setStep('progress')
    } finally {
      setSubmitting(false)
    }
  }

  /** 确认保存：confirm 幂等（后端分步落 saved_refs）；failed_tasks 非空红字 + 重试 */
  const save = async () => {
    if (!noteId || saving) return
    setSaving(true)
    setFailedTasks(null)
    try {
      const r = await invoke<{ ok?: boolean; created_tasks?: number; failed_tasks?: string[] }>(
        'meetingNotes:confirm', {
          projectId, id: noteId, contentMd, saveToMaterials,
          actionItems: actionItems.map((i) => ({
            title: i.title, selected: i.selected, dueDate: i.dueDate || null,
          })),
        })
      if (!r || r.ok !== true) {
        showToast(errText(r, t('meetingNotes.saveFailed')), 'error'); return
      }
      if ((r.failed_tasks ?? []).length > 0) { setFailedTasks(r.failed_tasks ?? []); return }
      showToast(t(saveToMaterials ? 'meetingNotes.savedToast'
        : 'meetingNotes.savedToastNoMaterials', { count: r.created_tasks ?? 0 }), 'success',
        // 未入库素材时不引导去素材面板（正文不在那里，跳转违背免培训）
        saveToMaterials ? {
          action: {
            label: t('meetingNotes.viewMaterials'),
            onClick: () => window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
              detail: { projectId, tab: 'materials', container: 'project' },
            })),
          },
        } : undefined)
      onChanged?.()
      onClose()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  /** 放弃草稿（确认/失败步）：删除行回输入步（重新生成走放弃重建，2.13 裁决） */
  const discard = async () => {
    if (!noteId) return
    const ok = await confirm({
      title: t('meetingNotes.discardTitle'),
      message: t('meetingNotes.discardMessage', { title }),
    })
    if (!ok) return
    const r = await invoke('meetingNotes:delete', { projectId, id: noteId })
    if (!r || (r as { ok?: boolean }).ok !== true) {
      showToast(errText(r, t('meetingNotes.deleteFailed')), 'error'); return
    }
    onChanged?.()
    resetInput()
    setStep('input')
  }

  /** 暂存（确认步，三态「格式化态暂存」）：人工修改正文存回行内、保持待确认
   *  （不入素材不建事务不归档）；后端 updateContent 已放宽 structured，重试
   *  残留 file_id 行照常联动删旧传新；暂存后的正文成为后续 confirm 幂等基准 */
  const stash = async () => {
    if (!noteId || saving || !contentMd.trim()) return
    setSaving(true)
    try {
      const r = await invoke<{ ok?: boolean }>('meetingNotes:updateContent',
        { projectId, id: noteId, contentMd })
      if (!r || r.ok !== true) {
        showToast(errText(r, t('meetingNotes.saveFailed')), 'error'); return
      }
      showToast(t('meetingNotes.stashSaved'), 'success')
      onChanged?.()
      onClose()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  /** 保存草稿：仅落输入态字段（标题/日期/正文 + 已传图 fids）；新图未上传不传
   *  （定稿驱动零痕迹）；成功后通知宿主刷新提示条并关闭抽屉（暂存不丢输入） */
  const saveDraft = async () => {
    if (!canSaveDraft) return
    setSubmitting(true)
    try {
      const payload = {
        projectId, title: title.trim() || null,
        occurredAt: occurredAt || null, rawText: rawText.trim() || null,
        imageFileIds: images.filter((i) => i.fileId).map((i) => i.fileId as string),
      }
      if (noteId) {
        const u = await invoke('meetingNotes:update', { ...payload, id: noteId })
        if (!u || (u as { ok?: boolean }).ok !== true) {
          showToast(errText(u, t('meetingNotes.saveFailed')), 'error'); return
        }
      } else {
        const c = await invoke<{ ok?: boolean; note?: { id: string } }>('meetingNotes:create', payload)
        if (!c || c.ok !== true || !c.note?.id) {
          showToast(errText(c, t('meetingNotes.createFailed')), 'error'); return
        }
      }
      showToast(t('meetingNotes.draftSaved'), 'success')
      onChanged?.()
      onClose()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  const stepNum = (n: 1 | 2) => {
    const active = n === 1 ? step !== 'confirm' : step === 'confirm'
    const done = n === 1 && step === 'confirm'
    return `${s.stepDot} ${active ? s.stepDotActive : ''} ${done ? s.stepDotDone : ''}`
  }

  return (
    <div className={s.overlay} data-testid="note-wizard-overlay"
      onClick={() => { if (!dr.suppressClick.current) onClose() }}>
      <div className={s.panel} style={{ width: dr.width }} data-testid="note-wizard"
        onMouseDown={dr.onPanelMouseDown}
        onClick={(e) => e.stopPropagation()}>
        <div className={s.dragHandle} onMouseDown={dr.onHandleMouseDown} />

        <div className={s.header}>
          <span className={s.title}>{t('meetingNotes.title')}</span>
          <button className={s.closeBtn} aria-label={t('common.close')}
            onClick={onClose}><X size={16} /></button>
        </div>
        <div className={s.stepper}>
          <span className={stepNum(1)}>1</span>
          <span className={`${s.stepLabel} ${step !== 'confirm' ? s.stepLabelActive : ''}`}>
            {t('meetingNotes.stepInput')}
          </span>
          <span className={s.stepLine} />
          <span className={stepNum(2)}>2</span>
          <span className={`${s.stepLabel} ${step === 'confirm' ? s.stepLabelActive : ''}`}>
            {t('meetingNotes.stepConfirm')}
          </span>
        </div>

        {step === 'input' && (
          <>
            <div className={s.body}>
              <div className={s.fieldRow}>
                <div className={s.field}>
                  <label className={s.label}>{t('meetingNotes.titleLabel')}</label>
                  <input className={s.input} value={title} data-testid="note-title"
                    placeholder={t('meetingNotes.titlePlaceholder')}
                    onChange={(e) => setTitle(e.target.value)} />
                </div>
                <div className={`${s.field} ${s.fieldNarrow}`}>
                  <label className={s.label}>{t('meetingNotes.dateLabel')}</label>
                  <input type="date" className={s.input} value={occurredAt}
                    onChange={(e) => setOccurredAt(e.target.value)} />
                </div>
              </div>

              <div className={s.field}>
                <textarea className={s.textarea} value={rawText} data-testid="note-raw-text"
                  placeholder={t('meetingNotes.rawTextPlaceholder')}
                  onChange={(e) => setRawText(e.target.value)} />
              </div>

              {detectTranscript(rawText) && (
                <div className={s.transcriptHint} data-testid="note-transcript-hint">
                  <Check size={14} />{t('meetingNotes.transcriptHint')}
                </div>
              )}

              <div className={s.imagesBlock}>
                <div className={s.imagesHeader}>
                  <span className={s.label}>{t('meetingNotes.imagesLabel')}</span>
                  <button className={s.uploadBtn} disabled={images.length >= MAX_IMAGES}
                    onClick={() => fileInputRef.current?.click()}>
                    <ImagePlus size={14} />{t('meetingNotes.addImages')}
                  </button>
                  <input ref={fileInputRef} type="file" accept="image/*" multiple
                    style={{ display: 'none' }}
                    onChange={(e) => { if (e.target.files?.length) addImages(e.target.files); e.target.value = '' }} />
                </div>
                {images.length > 0 && (
                  <div className={s.thumbs} data-testid="note-thumbs">
                    {images.map((img, idx) => {
                      const st = imageState(img)
                      return (
                        <div key={`${img.fileId ?? 'pending'}-${idx}`} className={s.thumb}
                          title={img.name || t('meetingNotes.imageFallback')}>
                          {img.url
                            ? <img className={s.thumbImg} src={img.url} alt={img.name} />
                            : <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)' }}><ImagePlus size={18} /></span>}
                          <span className={`${s.badge} ${st === 'ready' ? s.badgeReady : st === 'error' ? s.badgeError : s.badgeBusy}`}>
                            {st === 'ready' && <Check size={11} />}
                            {st === 'error' && <X size={11} />}
                            {st === 'busy' && <Loader2 size={11} className={s.spin} />}
                          </span>
                          <button className={s.thumbRemove} aria-label={t('common.delete')}
                            onClick={() => removeImage(img)}><X size={10} /></button>
                        </div>
                      )
                    })}
                  </div>
                )}
                {errorCount > 0 && (
                  <span className={s.label} style={{ color: 'var(--danger, #ef4444)' }}>
                    {t('meetingNotes.imageError')}
                  </span>
                )}
              </div>
            </div>

            <div className={s.footer}>
              <span className={s.gateHint} data-testid="note-gate-hint">
                {busyCount > 0
                  ? <><Loader2 size={12} className={s.spin} />{t('meetingNotes.imagesPending', { done: images.length - busyCount, total: images.length })}</>
                  : newImages.length > 0
                    ? t('meetingNotes.newImagesHint', { count: newImages.length })
                    : t('meetingNotes.outputHint')}
              </span>
              {canSaveDraft && (
                <button className={s.linkBtn} disabled={submitting} data-testid="note-save-draft"
                  onClick={() => void saveDraft()}>{t('meetingNotes.saveDraft')}</button>
              )}
              <button className={s.primaryBtn} disabled={!canGenerate} data-testid="note-generate"
                onClick={() => void generate()}>
                <Plus size={14} />{t(isRegen ? 'meetingNotes.regenerate' : 'meetingNotes.generate')}
              </button>
            </div>
          </>
        )}

        {(step === 'progress' || step === 'failed') && (
          <>
            <div className={s.body} data-testid="note-progress">
              {step === 'progress' ? (
                <div className={s.progressBody}>
                  <span className={s.spinner} />
                  <span>{t('meetingNotes.structuring')}</span>
                </div>
              ) : (
                <div className={s.errorBox} data-testid="note-error">
                  <span className={s.errorTitle}><AlertCircle size={14} />{t('meetingNotes.structureFailed')}</span>
                  <span>{lastError ? t(ERROR_KEYS[lastError] ?? 'meetingNotes.error.llmFailed') : ''}</span>
                </div>
              )}
            </div>
            <div className={s.footer}>
              {step === 'failed' && (
                <>
                  <button className={s.linkBtn} disabled={submitting}
                    onClick={() => void discard()}>{t('meetingNotes.discard')}</button>
                  <button className={s.primaryBtn} disabled={submitting} data-testid="note-retry"
                    onClick={() => void retryStructure()}>{t('meetingNotes.retryStructure')}</button>
                </>
              )}
              {step === 'progress' && (
                <span className={s.gateHint}>{t('meetingNotes.progressHint')}</span>
              )}
            </div>
          </>
        )}

        {step === 'confirm' && (
          <>
            <div className={s.body} data-testid="note-confirm">
              <span className={s.sectionLabel}>
                {t('meetingNotes.contentLabel')}
                <span className={s.sectionHint}>{t('meetingNotes.contentHint')}</span>
              </span>
              <textarea className={s.textarea} value={contentMd} data-testid="note-content-md"
                onChange={(e) => setContentMd(e.target.value)} />

              <span className={s.sectionLabel}>
                {t('meetingNotes.itemsLabel')}
                <span className={s.sectionHint}>{t('meetingNotes.itemsHint')}</span>
              </span>
              {actionItems.length === 0 ? (
                <span className={s.emptyItems}>{t('meetingNotes.emptyItems')}</span>
              ) : (
                <div className={s.itemsList}>
                  {actionItems.map((item, idx) => (
                    <div key={idx} className={s.itemRow}>
                      <input type="checkbox" className={s.itemCheck} checked={item.selected}
                        onChange={(e) => setActionItems((prev) => prev.map((it, i) => (
                          i === idx ? { ...it, selected: e.target.checked } : it)))} />
                      <input className={s.itemTitle} value={item.title} data-testid={`note-item-title-${idx}`}
                        onChange={(e) => setActionItems((prev) => prev.map((it, i) => (
                          i === idx ? { ...it, title: e.target.value } : it)))} />
                      <input type="date" className={s.itemDue} value={item.dueDate ?? ''}
                        title={t('meetingNotes.dueDateOptional')}
                        onChange={(e) => setActionItems((prev) => prev.map((it, i) => (
                          i === idx ? { ...it, dueDate: e.target.value || null } : it)))} />
                      <button className={s.linkBtn} style={{ padding: '4px' }} title={t('common.delete')}
                        onClick={() => setActionItems((prev) => prev.filter((_, i) => i !== idx))}>
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {failedTasks && failedTasks.length > 0 && (
                <div className={s.failedBox} data-testid="note-failed-tasks">
                  <span>{t('meetingNotes.partialFailed')}</span>
                  <ul className={s.failedList}>
                    {failedTasks.map((ft, i) => <li key={i}>{ft}</li>)}
                  </ul>
                  <span>{t('meetingNotes.retryHint')}</span>
                </div>
              )}

              <label className={s.materialsRow}>
                <input type="checkbox" className={s.itemCheck} checked={saveToMaterials}
                  data-testid="note-save-materials"
                  onChange={(e) => setSaveToMaterials(e.target.checked)} />
                <span className={s.materialsLabel}>{t('meetingNotes.saveToMaterials')}</span>
                <span className={s.materialsHint}>{t('meetingNotes.saveToMaterialsHint')}</span>
              </label>
            </div>

            <div className={s.footer}>
              <button className={s.linkBtn} disabled={saving} data-testid="note-back-input"
                onClick={() => setStep('input')}>{t('meetingNotes.backToInput')}</button>
              <button className={`${s.linkBtn} ${s.linkBtnDanger}`} disabled={saving}
                onClick={() => void discard()}>{t('meetingNotes.discard')}</button>
              <button className={s.linkBtn} disabled={saving || !contentMd.trim()}
                data-testid="note-stash" onClick={() => void stash()}>
                {t('meetingNotes.stash')}
              </button>
              <button className={s.primaryBtn} disabled={saving || !contentMd.trim()}
                data-testid="note-save" onClick={() => void save()}>
                {saving ? t('meetingNotes.saving') : t('meetingNotes.save')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
