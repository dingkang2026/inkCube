/** src/features/notes/NotesTab.tsx — 项目「纪要」Tab（2.14 纪要中心）。
 *
 * 待处理区（draft/structuring/structured，N=0 不渲染；行点击恢复向导对应步骤，
 * draft/structured 行内可直接放弃——structuring 行不提供放弃：LLM 已受理，
 * 等 structuring 完成或失败后处置，避免已发出请求的浪费与语义模糊）
 * + 已归档时间线（拜访日期倒序分页，加载更多按钮）+ 详情浮层
 * （react-markdown 渲染正文 + 行动项只读清单 + 正文编辑 + toggle）+ 向导抽屉挂载。
 * 素材 toggle 对齐素材删除语义：移出 = 完整级联删素材文件（向量/图片索引/磁盘），
 * 纪要行保留可重新加入；删除纪要级联删素材 md + 未共享现场图片（已建事务保留）。
 * toggle/编辑保存与素材联动（2.13 §4）：updateContent 后端按行内 draft_md 内容比对，
 * 已入素材时删旧文件重传新文件（刷新 RAG 索引）并回写，未入仅回写正文。
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileMinus2, FilePlus2, Loader2, Plus, Trash2, X } from 'lucide-react'
import { useConfirm } from '../../components/ConfirmDialog'
import { useToast } from '../knowledge/Toast'
import { NoteWizardDrawer } from './NoteWizardDrawer'
import type { MeetingNote } from './types'
import s from './NotesTab.module.css'

/** 归档分页页大小（0.1 §16 契约；加载更多按 offset 步进） */
const ARCHIVE_PAGE = 50

function invoke<T = Record<string, unknown>>(channel: string, payload?: unknown): Promise<T | null> {
  return window.electronAPI.invoke(channel, payload) as Promise<T | null>
}

/** invoke 结果错误归一（structuredPythonResponse 失败返回 {ok:false, error}） */
function errText(res: unknown, fallback: string): string {
  if (res && typeof res === 'object' && 'error' in res) {
    const e = (res as { error?: unknown }).error
    if (typeof e === 'string' && e) return e
  }
  return fallback
}

/** saved_refs JSON → file_id（坏数据容错 null；null = 未入素材） */
function refFileId(row: MeetingNote): string | null {
  try {
    return (JSON.parse(row.saved_refs ?? '{}') as { file_id?: string | null }).file_id ?? null
  } catch { return null }
}

/** saved_refs JSON 解析（坏数据容错空对象，保留 task_ids 供 toggle 重写） */
function parseRefs(row: MeetingNote): { file_id: string | null; task_ids: number[] } {
  try {
    const r = JSON.parse(row.saved_refs ?? '{}')
    return { file_id: r?.file_id ?? null, task_ids: Array.isArray(r?.task_ids) ? r.task_ids : [] }
  } catch { return { file_id: null, task_ids: [] } }
}

/** draft_md JSON → {content_md, action_items}（坏数据容错空） */
function draftPayload(row: MeetingNote): { content_md: string; action_items: { title: string }[] } {
  try {
    const p = JSON.parse(row.draft_md ?? '{}')
    return {
      content_md: p?.content_md ?? '',
      action_items: Array.isArray(p?.action_items) ? p.action_items : [],
    }
  } catch { return { content_md: '', action_items: [] } }
}

/** 待处理行状态角标 i18n 键（archived 不出现在待处理区，无需键） */
const STATUS_KEY: Record<string, string> = {
  draft: 'notesHub.statusDraft',
  structuring: 'notesHub.statusStructuring',
  structured: 'notesHub.statusStructured',
}

/** 行显示日期：拜访日期优先，缺省回落创建日期 */
function rowDate(row: MeetingNote): string {
  return row.occurred_at ?? row.created_at.slice(0, 10)
}

export function NotesTab({ projectId }: { projectId: string }) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirm = useConfirm()

  const [pending, setPending] = useState<MeetingNote[]>([])
  const [archived, setArchived] = useState<MeetingNote[]>([])
  const [archivedTotal, setArchivedTotal] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [resumeNoteId, setResumeNoteId] = useState<string | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null) // toggle/删除在途防重复点击
  // 详情浮层正文编辑（仅正文；行动项在向导确认步编辑，浮层只读）
  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)

  const loadPending = useCallback(async () => {
    const r = await invoke<{ ok?: boolean; notes?: MeetingNote[] }>(
      'meetingNotes:listPending', { projectId })
    setPending(r?.notes ?? [])
  }, [projectId])

  const loadArchived = useCallback(async (offset: number, append: boolean) => {
    // 泛型收单行（materialize 同款形态）：跨行泛型触发审计脚本 v3.2 已知盲区（禁跨行）
    const r = await invoke<{ ok?: boolean; notes?: MeetingNote[]; has_more?: boolean; total?: number }>(
      'meetingNotes:listArchived', { projectId, limit: ARCHIVE_PAGE, offset })
    const notes = r?.notes ?? []
    setArchived((prev) => (append ? [...prev, ...notes] : notes))
    setArchivedTotal(r?.total ?? 0)
    setHasMore(r?.has_more ?? false)
    if (!append) setLoaded(true)
  }, [projectId])

  // 初始/项目切换：并行拉两表（ProjectDetailPage 以 key={project.id} 重挂载，无跨项目竞态）
  useEffect(() => {
    setLoaded(false)
    setArchived([])
    setPending([])
    void loadPending()
    void loadArchived(0, false)
  }, [projectId, loadPending, loadArchived])

  /** 向导行集变化（保存/放弃）后重拉两表 */
  const refresh = useCallback(async () => {
    await Promise.all([loadPending(), loadArchived(0, false)])
  }, [loadPending, loadArchived])

  const loadMore = async () => {
    if (loadingMore) return
    setLoadingMore(true)
    try { await loadArchived(archived.length, true) } finally { setLoadingMore(false) }
  }

  const openWizard = (noteId: string | null) => {
    setResumeNoteId(noteId)
    setWizardOpen(true)
  }

  /** 放弃草稿（待处理行内，draft/structured only；语义与向导内放弃一致） */
  const discardDraft = async (row: MeetingNote) => {
    const ok = await confirm({
      title: t('meetingNotes.discardTitle'),
      message: t('meetingNotes.discardMessage', { title: row.title }),
    })
    if (!ok) return
    const r = await invoke('meetingNotes:delete', { projectId, id: row.id })
    if (!r || (r as { ok?: boolean }).ok !== true) {
      showToast(errText(r, t('meetingNotes.deleteFailed')), 'error'); return
    }
    await loadPending()
  }

  /** toggle 后本地重写该行 refs（不整表重拉，保留已加载分页） */
  const applyRefs = (id: string, fileId: string | null) => {
    setArchived((prev) => prev.map((n) => {
      if (n.id !== id) return n
      const refs = parseRefs(n)
      return { ...n, saved_refs: JSON.stringify({ ...refs, file_id: fileId }) }
    }))
  }

  /** 素材 toggle：未入 → materialize 直调（建设性操作免确认）；已入 →
   *  confirm 级联删说明后 dematerialize（对齐素材删除语义，破坏性需确认） */
  const toggleMaterial = async (row: MeetingNote) => {
    if (busyId) return
    setBusyId(row.id)
    try {
      if (refFileId(row)) {
        const ok = await confirm({
          title: t('notesHub.removeConfirmTitle'),
          message: t('notesHub.removeConfirmMessage'),
        })
        if (!ok) return
        const r = await invoke('meetingNotes:dematerialize', { projectId, id: row.id })
        if (!r || (r as { ok?: boolean }).ok !== true) {
          showToast(errText(r, t('notesHub.dematerializeFailed')), 'error'); return
        }
        applyRefs(row.id, null)
      } else {
        const r = await invoke<{ ok?: boolean; file_id?: string }>(
          'meetingNotes:materialize', { projectId, id: row.id })
        if (!r || r.ok !== true || !r.file_id) {
          showToast(errText(r, t('notesHub.materializeFailed')), 'error'); return
        }
        applyRefs(row.id, r.file_id)
      }
    } finally {
      setBusyId(null)
    }
  }

  /** 删除归档纪要：级联删素材 md + 未共享现场图片，已建事务保留（确认文案明示，免培训） */
  const deleteArchived = async (row: MeetingNote) => {
    const ok = await confirm({
      title: t('notesHub.deleteConfirmTitle'),
      message: t('notesHub.deleteConfirmMessage', { title: row.title }),
    })
    if (!ok) return
    const r = await invoke('meetingNotes:delete', { projectId, id: row.id })
    if (!r || (r as { ok?: boolean }).ok !== true) {
      showToast(errText(r, t('notesHub.deleteFailed')), 'error'); return
    }
    setArchived((prev) => prev.filter((n) => n.id !== row.id))
    setArchivedTotal((n) => Math.max(0, n - 1))
    if (detailId === row.id) setDetailId(null)
  }

  // 浮层行切换/关闭时重置编辑态（不留旧 draft）
  useEffect(() => {
    setEditing(false)
    setEditDraft('')
  }, [detailId])

  /** 编辑保存后局部回写该行（draft_md 正文 + refs.file_id），不整表重拉（保留分页） */
  const applyContentEdit = (id: string, contentMd: string, fileId: string | null) => {
    setArchived((prev) => prev.map((n) => {
      if (n.id !== id) return n
      return {
        ...n,
        draft_md: JSON.stringify({ content_md: contentMd, action_items: draftPayload(n).action_items }),
        saved_refs: JSON.stringify({ ...parseRefs(n), file_id: fileId }),
      }
    }))
  }

  /** 浮层正文编辑保存：updateContent 后端内容比对（与行内一致才幂等跳过），
   *  已入素材删旧文件重传新文件（刷新 RAG 索引），返回体 file_id 即同步后终态 */
  const saveContentEdit = async () => {
    if (!detailNote || savingEdit) return
    setSavingEdit(true)
    try {
      const r = await invoke<{ ok?: boolean; file_id?: string | null }>(
        'meetingNotes:updateContent', { projectId, id: detailNote.id, contentMd: editDraft })
      if (!r || r.ok !== true) {
        showToast(errText(r, t('notesHub.editFailed')), 'error'); return
      }
      applyContentEdit(detailNote.id, editDraft, r.file_id ?? null)
      showToast(t(r.file_id ? 'notesHub.editSavedSynced' : 'notesHub.editSavedToast'), 'success')
      setEditing(false)
    } finally {
      setSavingEdit(false)
    }
  }

  const detailNote = archived.find((n) => n.id === detailId) ?? null
  const detailPayload = detailNote ? draftPayload(detailNote) : null
  const editDirty = detailPayload ? editDraft.trim() !== detailPayload.content_md.trim() : false

  return (
    <div className={s.container} data-testid="notes-tab">
      <div className={s.header}>
        <span className={s.headMeta}>{t('notesHub.headMeta', { total: archivedTotal })}</span>
        <button className={s.primaryBtn} data-testid="notes-new"
          onClick={() => openWizard(null)}>
          <Plus size={14} />{t('notesHub.newButton')}
        </button>
      </div>

      {pending.length > 0 && (
        <section className={s.section} data-testid="notes-pending">
          <span className={s.sectionTitle}>
            {t('notesHub.pendingTitle', { count: pending.length })}
          </span>
          <div className={s.list}>
            {pending.map((row) => (
              <div key={row.id} className={s.row} data-testid={`note-row-${row.id}`}
                onClick={() => openWizard(row.id)}>
                <span className={`${s.statusTag} ${row.status === 'structuring' ? s.statusBusy : ''}`}>
                  {row.status === 'structuring' && <Loader2 size={11} className={s.spin} />}
                  {t(STATUS_KEY[row.status] ?? 'notesHub.statusDraft')}
                </span>
                <div className={s.rowInfo}>
                  <span className={s.rowTitle}>{row.title}</span>
                  <span className={s.rowMeta}>{rowDate(row)}</span>
                </div>
                {row.status !== 'structuring' && (
                  <button className={s.linkDanger} title={t('meetingNotes.discard')}
                    onClick={(e) => { e.stopPropagation(); void discardDraft(row) }}>
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      <section className={s.section} data-testid="notes-archived">
        {!loaded ? (
          <div className={s.emptyState}><Loader2 size={16} className={s.spin} /></div>
        ) : archived.length === 0 ? (
          <div className={s.emptyState}>{t('notesHub.emptyArchived')}</div>
        ) : (
          <div className={s.list}>
            {archived.map((row) => {
              const fid = refFileId(row)
              return (
                <div key={row.id} className={s.row} data-testid={`note-row-${row.id}`}
                  onClick={() => setDetailId(row.id)}>
                  <span className={`${s.materialTag} ${fid ? s.materialIn : ''}`}>
                    {fid ? t('notesHub.inMaterials') : t('notesHub.notInMaterials')}
                  </span>
                  <div className={s.rowInfo}>
                    <span className={s.rowTitle}>{row.title}</span>
                    <span className={s.rowMeta}>{rowDate(row)}</span>
                  </div>
                  <button className={s.linkBtn} data-testid={`note-toggle-material-${row.id}`}
                    title={fid ? t('notesHub.removeFromMaterials') : t('notesHub.addToMaterials')}
                    disabled={busyId === row.id}
                    onClick={(e) => { e.stopPropagation(); void toggleMaterial(row) }}>
                    {fid ? <FileMinus2 size={13} /> : <FilePlus2 size={13} />}
                  </button>
                  <button className={s.linkDanger} data-testid={`note-delete-${row.id}`}
                    title={t('notesHub.delete')}
                    onClick={(e) => { e.stopPropagation(); void deleteArchived(row) }}>
                    <Trash2 size={13} />
                  </button>
                </div>
              )
            })}
          </div>
        )}
        {hasMore && (
          <button className={s.loadMore} data-testid="note-load-more" disabled={loadingMore}
            onClick={() => void loadMore()}>
            {loadingMore && <Loader2 size={13} className={s.spin} />}
            {t('notesHub.loadMore')}
          </button>
        )}
      </section>

      {detailNote && detailPayload && (
        <div className={s.overlay} data-testid="note-detail"
          onClick={() => { if (!editing) setDetailId(null) }}>
          <div className={s.detailPanel} onClick={(e) => e.stopPropagation()}>
            <div className={s.detailHeader}>
              <span className={s.detailTitle}>{detailNote.title}</span>
              <span className={s.detailDate}>{rowDate(detailNote)}</span>
              <button className={s.linkBtn} aria-label={t('common.close')}
                onClick={() => setDetailId(null)}><X size={16} /></button>
            </div>
            <div className={s.detailBody}>
              {editing ? (
                <textarea className={s.editArea} value={editDraft} data-testid="note-detail-edit-area"
                  onChange={(e) => setEditDraft(e.target.value)} />
              ) : (
                <div className={s.md}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {detailPayload.content_md}
                  </ReactMarkdown>
                </div>
              )}
              <span className={s.sectionTitle}>{t('notesHub.actionItems')}</span>
              {detailPayload.action_items.length === 0 ? (
                <span className={s.emptyItems}>{t('notesHub.noActionItems')}</span>
              ) : (
                <ul className={s.itemList}>
                  {detailPayload.action_items.map((it, i) => (
                    <li key={i}>{it.title}</li>
                  ))}
                </ul>
              )}
            </div>
            <div className={s.detailFooter}>
              {editing ? (
                <>
                  <button className={s.footerTextBtn} data-testid="note-detail-edit-cancel"
                    disabled={savingEdit}
                    onClick={() => setEditing(false)}>{t('notesHub.editDiscard')}</button>
                  <button className={s.primaryBtn} data-testid="note-detail-edit-save"
                    disabled={savingEdit || !editDirty || !editDraft.trim()}
                    onClick={() => void saveContentEdit()}>
                    {savingEdit ? t('meetingNotes.saving') : t('notesHub.editSave')}
                  </button>
                </>
              ) : (
                <>
                  <button className={s.footerTextBtn} data-testid="note-detail-edit"
                    title={t('notesHub.editContent')}
                    onClick={() => { setEditDraft(detailPayload.content_md); setEditing(true) }}>
                    {t('notesHub.editContent')}
                  </button>
                  <button className={s.primaryBtn} data-testid="note-detail-toggle"
                    disabled={busyId === detailNote.id}
                    onClick={() => void toggleMaterial(detailNote)}>
                    {refFileId(detailNote)
                      ? <FileMinus2 size={14} />
                      : <FilePlus2 size={14} />}
                    {refFileId(detailNote)
                      ? t('notesHub.removeFromMaterials')
                      : t('notesHub.addToMaterials')}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      <NoteWizardDrawer projectId={projectId} open={wizardOpen} resumeNoteId={resumeNoteId}
        onClose={() => setWizardOpen(false)} onChanged={() => { void refresh() }} />
    </div>
  )
}
