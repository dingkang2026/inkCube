/** src/features/sticky/StickyDrawer.tsx — 便签全局轻抽屉（2.12 §4）。
 *
 * 全局速记收件箱：顶栏动作按钮（TitleBar，与通知铃铛同区）/ Ctrl+Shift+N 经 window
 * 事件 `sticky:toggle-drawer` 调起（App 级遮罩抽屉，宿主页不卸载——任务页内调起不打断任务）。
 * 纯文本随手记 + 固定色板标记；收件箱卡片可归档进项目素材（复用上传链）、
 * 转为项目事务、复制、删除；已归档分区仅查看、复制与再编辑。
 * 形态对齐 ScheduleDrawer（遮罩 + 右侧面板 + 左缘拖宽，useDrawerWidth 60vw 契约）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive, ClipboardList, Copy, Pencil, Trash2, X,
} from 'lucide-react'
import { usePagedList } from '../../hooks/usePagedList'
import { useDrawerWidth } from '../../hooks/useDrawerWidth'
import { useToast } from '../knowledge/Toast'
import { useConfirm } from '../../components/ConfirmDialog'
import { STICKY_COLORS, STICKY_COLOR_HEX, type StickyColor, type StickyNote, type StickyView } from './types'
import s from './StickyDrawer.module.css'

/** 宽度下限（上限 92vw 随视口、默认 60vw 随视口，见 useDrawerWidth） */
const MIN_W = 420

/** 正文软限（spec §2：不硬限，超出提示；不禁存——「先接住」原则，保存永不因长度被拦） */
const SOFT_LIMIT = 5000

interface ProjectOption {
  id: string
  name: string
}

/** ISO 时间 → 短格式（列表元信息行） */
function fmtTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

/** invoke 结果错误归一：动作五通道（structuredPythonResponse）失败返回 {ok:false, error} */
function errText(res: unknown, fallback: string): string {
  if (res && typeof res === 'object' && 'error' in res) {
    const e = (res as { error?: unknown }).error
    if (typeof e === 'string' && e) return e
  }
  return fallback
}

/** electronAPI.invoke 收窄：preload 契约返回 Promise<unknown>，此处统一 cast */
function invoke<T = Record<string, unknown>>(channel: string, payload?: unknown): Promise<T | null> {
  return window.electronAPI.invoke(channel, payload) as Promise<T | null>
}

/** 便签卡操作面板模式：归档（项目选择）/ 转事务（项目+截止日）/ 编辑（正文+色板） */
type PanelMode = 'archive' | 'affair' | 'edit' | null

export function StickyDrawer() {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirm = useConfirm()

  // 自含 open state：入口（NavTabs/快捷键）经 window 事件 toggle，组件内部零 props
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<StickyView>('inbox')
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [panelNoteId, setPanelNoteId] = useState<string | null>(null)
  const [panelMode, setPanelMode] = useState<PanelMode>(null)
  const [editContent, setEditContent] = useState('')
  const [pickProjectId, setPickProjectId] = useState('')
  const [pickDueDate, setPickDueDate] = useState('')
  const [busy, setBusy] = useState(false)
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const dr = useDrawerWidth({ min: MIN_W })

  const list = usePagedList<StickyNote>({
    channel: 'sticky:list', itemsKey: 'notes', params: { view }, enabled: open,
  })

  const toggle = useCallback(() => setOpen((v) => !v), [])

  useEffect(() => {
    window.addEventListener('sticky:toggle-drawer', toggle)
    return () => window.removeEventListener('sticky:toggle-drawer', toggle)
  }, [toggle])

  // 应用内快捷键 Ctrl+Shift+N（spec §4：任意页面可调起；与抽屉 toggle 同一收口）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'n') {
        e.preventDefault()
        setOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 打开即聚焦输入框（速记动线零点击）
  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  // Esc 关闭（输入框聚焦时忽略——对齐 ScheduleDrawer 两段式语义）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // 项目选择器数据：抽屉打开时拉一次（limit=0 全量，选择器形态不截断）
  useEffect(() => {
    if (!open) return
    let cancelled = false
    window.electronAPI.invoke('knowledge:listProjects', { limit: 0 })
      .then((raw: unknown) => {
        const res = raw as { projects?: ProjectOption[] } | null
        if (!cancelled) setProjects(res?.projects ?? [])
      })
      .catch(() => { /* 选择器置空即可，归档/转事务在确认时仍会被后端 404 拦截 */ })
    return () => { cancelled = true }
  }, [open])

  const closePanel = () => {
    setPanelNoteId(null)
    setPanelMode(null)
    setPickProjectId('')
    setPickDueDate('')
    setEditContent('')
  }

  const openPanel = (note: StickyNote, mode: Exclude<PanelMode, null>) => {
    setPanelNoteId(note.id)
    setPanelMode(mode)
    setEditContent(note.content)
    setPickProjectId('')
    setPickDueDate('')
  }

  /** 新建：Enter 或存钮；成功清空输入框并刷新列表头部 */
  const create = async () => {
    const content = draft.trim()
    if (!content || saving) return
    setSaving(true)
    try {
      const res = await invoke('sticky:create', { content })
      if (res?.error) { showToast(errText(res, t('sticky.createFailed')), 'error'); return }
      setDraft('')
      await list.reload()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setSaving(false)
    }
  }

  const onDraftKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void create()
    }
  }

  const copyNote = async (note: StickyNote) => {
    try {
      await navigator.clipboard.writeText(note.content)
      showToast(t('sticky.copied'), 'success')
    } catch {
      showToast(t('sticky.copyFailed'), 'error')
    }
  }

  const saveEdit = async (note: StickyNote, color: StickyColor | null) => {
    const content = editContent.trim()
    if (!content) return
    setBusy(true)
    try {
      const res = await invoke('sticky:update', {
        id: note.id, content, color,
      })
      if (res?.error) { showToast(errText(res, t('sticky.saveFailed')), 'error'); return }
      closePanel()
      await list.reload()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  const archiveNote = async (note: StickyNote) => {
    if (!pickProjectId) return
    setBusy(true)
    try {
      const res = await invoke('sticky:archiveToProject', {
        id: note.id, projectId: pickProjectId,
      })
      if (res?.error) { showToast(errText(res, t('sticky.archiveFailed')), 'error'); return }
      const name = projects.find((p) => p.id === pickProjectId)?.name ?? ''
      showToast(t('sticky.archivedToast', { project: name }), 'success', {
        action: {
          label: t('sticky.viewProject'),
          onClick: () => window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
            detail: { projectId: pickProjectId, tab: 'materials', container: 'project' },
          })),
        },
      })
      closePanel()
      await list.reload()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  const toAffair = async (note: StickyNote) => {
    if (!pickProjectId) return
    setBusy(true)
    try {
      const res = await invoke('sticky:toAffair', {
        id: note.id, projectId: pickProjectId, dueDate: pickDueDate || null,
      })
      if (res?.error) { showToast(errText(res, t('sticky.affairFailed')), 'error'); return }
      showToast(t('sticky.affairToast'), 'success', {
        action: {
          label: t('sticky.viewAffair'),
          onClick: () => window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
            detail: { projectId: pickProjectId, tab: 'affairs', container: 'project' },
          })),
        },
      })
      closePanel()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    } finally {
      setBusy(false)
    }
  }

  const deleteNote = async (note: StickyNote) => {
    const ok = await confirm({
      title: t('sticky.deleteTitle'),
      message: t('sticky.deleteMessage', { preview: note.content.slice(0, 40) }),
    })
    if (!ok) return
    try {
      const res = await invoke('sticky:delete', { id: note.id })
      if (res?.error) { showToast(errText(res, t('sticky.deleteFailed')), 'error'); return }
      await list.reload()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 'error')
    }
  }

  if (!open) return null

  return (
    <div className={s.overlay} data-testid="sticky-drawer-overlay"
      onClick={() => { if (!dr.suppressClick.current) setOpen(false) }}>
      <div className={s.panel} style={{ width: dr.width }} data-testid="sticky-drawer"
        onMouseDown={dr.onPanelMouseDown}
        onClick={(e) => e.stopPropagation()}>
        <div className={s.dragHandle} onMouseDown={dr.onHandleMouseDown} />

        <div className={s.header}>
          <span className={s.title}>{t('sticky.title')}</span>
          <button className={s.closeBtn} aria-label={t('common.close')}
            onClick={() => setOpen(false)}><X size={16} /></button>
        </div>

        <div className={s.inputArea}>
          <div className={s.inputRow}>
            <textarea
              ref={inputRef}
              className={s.input}
              value={draft}
              placeholder={t('sticky.inputPlaceholder')}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onDraftKeyDown}
              data-testid="sticky-input"
            />
            <button className={s.saveBtn} disabled={!draft.trim() || saving}
              onClick={() => void create()}>{t('sticky.save')}</button>
          </div>
          <div className={`${s.hint} ${draft.length > SOFT_LIMIT ? s.hintWarn : ''}`}>
            {draft.length > SOFT_LIMIT ? t('sticky.overLimit') : t('sticky.inputHint')}
          </div>
        </div>

        <div className={s.viewToggle} role="tablist">
          {(['inbox', 'archived'] as const).map((v) => (
            <button key={v} role="tab" aria-selected={view === v}
              className={`${s.viewBtn} ${view === v ? s.viewBtnActive : ''}`}
              onClick={() => { setView(v); closePanel() }}>
              {t(`sticky.tab.${v}`)}
            </button>
          ))}
        </div>

        <div className={s.list} data-testid="sticky-list">
          {list.error ? (
            <div className={s.empty}>{t('common.listLoadFailed')}</div>
          ) : list.items.length === 0 && !list.loading ? (
            <div className={s.empty}>
              {t(view === 'inbox' ? 'sticky.emptyInbox' : 'sticky.emptyArchived')}
            </div>
          ) : list.items.map((note) => {
            const panelOpen = panelNoteId === note.id && panelMode !== null
            return (
              <div key={note.id}
                className={`${s.card} ${note.color ? s[`color${note.color[0].toUpperCase()}${note.color.slice(1)}` as 'colorYellow'] : ''}`}
                data-testid="sticky-card">
                {panelOpen && panelMode === 'edit' ? (
                  <>
                    <textarea className={s.editInput} value={editContent}
                      onChange={(e) => setEditContent(e.target.value)} autoFocus />
                    <div className={s.panel_row}>
                      <div className={s.colorRow}>
                        <button className={`${s.colorDot} ${s.colorNone} ${note.color === null ? s.colorDotActive : ''}`}
                          title={t('sticky.colorNone')}
                          onClick={() => void saveEdit(note, null)} />
                        {STICKY_COLORS.map((c) => (
                          <button key={c} title={t(`sticky.color.${c}`)}
                            className={`${s.colorDot} ${note.color === c ? s.colorDotActive : ''}`}
                            style={{ background: STICKY_COLOR_HEX[c] }}
                            onClick={() => void saveEdit(note, c)} />
                        ))}
                      </div>
                      <div className={s.editActions}>
                        <button className={s.linkBtn} onClick={closePanel}>{t('common.cancel')}</button>
                        <button className={s.primaryMini} disabled={!editContent.trim() || busy}
                          onClick={() => void saveEdit(note, note.color)}>{t('common.confirm')}</button>
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className={s.content}>{note.content}</div>
                    <div className={s.meta}>
                      <span>{fmtTime(note.updated_at)}</span>
                      {view === 'archived' && note.project_id && (
                        <span className={s.projectTag}>
                          {projects.find((p) => p.id === note.project_id)?.name ?? note.project_id}
                        </span>
                      )}
                    </div>
                    {panelOpen && (panelMode === 'archive' || panelMode === 'affair') ? (
                      <div className={s.panel_row}>
                        <div className={s.selectRow}>
                          <select className={s.select} value={pickProjectId}
                            onChange={(e) => setPickProjectId(e.target.value)}>
                            <option value="">{t('sticky.pickProject')}</option>
                            {projects.map((p) => (
                              <option key={p.id} value={p.id}>{p.name}</option>
                            ))}
                          </select>
                          {panelMode === 'affair' && (
                            <input type="date" className={s.dateInput}
                              value={pickDueDate} title={t('sticky.dueDateOptional')}
                              onChange={(e) => setPickDueDate(e.target.value)} />
                          )}
                        </div>
                        <div className={s.selectRow}>
                          <button className={s.primaryMini} disabled={!pickProjectId || busy}
                            onClick={() => void (panelMode === 'archive'
                              ? archiveNote(note) : toAffair(note))}>
                            {t(`sticky.${panelMode === 'archive' ? 'confirmArchive' : 'confirmAffair'}`)}
                          </button>
                          <button className={s.linkBtn} onClick={closePanel}>{t('common.cancel')}</button>
                        </div>
                      </div>
                    ) : (
                      <div className={s.actions}>
                        <button className={s.actBtn} title={t('sticky.copy')}
                          onClick={() => void copyNote(note)}><Copy size={12} />{t('sticky.copy')}</button>
                        <button className={s.actBtn} title={t('sticky.edit')}
                          onClick={() => openPanel(note, 'edit')}><Pencil size={12} />{t('sticky.edit')}</button>
                        {view === 'inbox' && (
                          <>
                            <button className={s.actBtn} title={t('sticky.archive')}
                              onClick={() => openPanel(note, 'archive')}><Archive size={12} />{t('sticky.archive')}</button>
                            <button className={s.actBtn} title={t('sticky.toAffair')}
                              onClick={() => openPanel(note, 'affair')}><ClipboardList size={12} />{t('sticky.toAffair')}</button>
                            <button className={`${s.actBtn} ${s.actBtnDanger}`} title={t('sticky.delete')}
                              onClick={() => void deleteNote(note)}><Trash2 size={12} />{t('sticky.delete')}</button>
                          </>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            )
          })}
          {list.hasMore && (
            <button className={s.loadMore} disabled={list.loadingMore}
              onClick={() => { void list.loadMore() }}>
              {list.loadingMore ? t('common.loading') : t('common.loadMore')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
