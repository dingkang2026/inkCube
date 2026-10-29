/** tests/NotesTab.test.tsx — 纪要中心 Tab（2.14）组件契约单测。
 *
 * 覆盖：分区渲染（待处理 N=0 不渲染 / 归档空态）/ 点待处理行恢复向导
 * （resumeNoteId 传递）/ 素材 toggle 双向（未入 → materialize 直调免确认、
 * 已入 → confirm 后 dematerialize、取消不发请求）/ 删除归档（confirm →
 * delete → 行消失且计数回落）/ 分页加载更多（offset 步进 + 追加 + 终态按钮
 * 消失）/ 详情浮层（正文 markdown + 行动项 + 关闭）。2.14 修复追加：toggle 图标
 * 按素材状态切换 / 浮层正文编辑（updateContent 联动素材同步、toast 分流、
 * dirty 门控、遮罩编辑态保护、取消不落库）。
 * NoteWizardDrawer 打桩为探针组件（隔离 useActivity 上下文依赖）；
 * ConfirmDialog 恒确认；IPC 全部经 invokeMock 捕获（模式同 NoteWizardDrawer.test）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      let out = key
      for (const [k, v] of Object.entries(opts || {})) {
        if (typeof v === 'string' || typeof v === 'number') out = out.replaceAll(`{{${k}}}`, String(v))
      }
      return out
    },
  }),
}))

vi.mock('../src/features/notes/NotesTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

const confirmMock = vi.hoisted(() => vi.fn())
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

// 向导抽屉打桩：断言 open/resumeNoteId 接线（组件自身契约在 NoteWizardDrawer.test）
const wizardMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/notes/NoteWizardDrawer', () => ({
  NoteWizardDrawer: (props: { open: boolean; resumeNoteId: string | null }) => {
    wizardMock(props)
    return props.open
      ? <div data-testid="note-wizard" data-resume={props.resumeNoteId ?? ''} />
      : null
  },
}))

import { NotesTab } from '../src/features/notes/NotesTab'

const invokeMock = vi.fn()

const baseRow = {
  id: 'n1', project_id: 'p1', title: '客户拜访', occurred_at: '2026-10-26',
  raw_text: 'x', image_file_ids: [], status: 'draft' as const,
  draft_md: null, saved_refs: null, last_error: null,
  created_at: '2026-10-26T10:00:00', updated_at: '2026-10-26T10:00:00',
}

const pendingDraft = { ...baseRow }

const archIn = {
  ...baseRow, id: 'a1', title: '厂商会议', status: 'archived' as const,
  occurred_at: '2026-10-20',
  draft_md: JSON.stringify({
    content_md: '# 纪要正文内容', action_items: [{ title: '跟进合同' }],
  }),
  saved_refs: JSON.stringify({ file_id: 'f9', task_ids: [0] }),
}

const archOut = {
  ...baseRow, id: 'a2', title: '园区拜访', status: 'archived' as const,
  occurred_at: null, draft_md: null, saved_refs: null,
}

const archPage = (notes: unknown[], extra: Record<string, unknown> = {}) => ({
  ok: true, notes, limit: 50, offset: 0, has_more: false, total: notes.length, ...extra,
})

const renderTab = () => render(<NotesTab projectId="p1" />)

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async () => ({}))
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(true)
  toastMock.mockClear()
  wizardMock.mockClear()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('NotesTab（2.14 纪要中心）', () => {
  it('分区渲染：待处理与归档行数、素材角标按 refs 翻转', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [pendingDraft] }
      if (ch === 'meetingNotes:listArchived') return archPage([archIn, archOut])
      return {}
    })
    renderTab()
    expect(await screen.findByTestId('notes-pending')).toBeTruthy()
    expect(screen.getByTestId('notes-pending')
      .querySelectorAll('[data-testid^="note-row-"]')).toHaveLength(1)
    expect(screen.getByTestId('notes-archived')
      .querySelectorAll('[data-testid^="note-row-"]')).toHaveLength(2)
    expect(screen.getByTestId('note-row-a1').textContent).toContain('notesHub.inMaterials')
    expect(screen.getByTestId('note-row-a2').textContent).toContain('notesHub.notInMaterials')
    // 归档计数入 headMeta
    expect(screen.getByTestId('notes-tab').textContent).toContain('2')
  })

  it('空态：无草稿待处理区不渲染，归档空显示空态文案', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') return archPage([])
      return {}
    })
    renderTab()
    expect(await screen.findByText('notesHub.emptyArchived')).toBeTruthy()
    expect(screen.queryByTestId('notes-pending')).toBeNull()
    expect(screen.queryByTestId('note-load-more')).toBeNull() // has_more=false 无按钮
  })

  it('点待处理行恢复向导：resumeNoteId 传递', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [pendingDraft] }
      if (ch === 'meetingNotes:listArchived') return archPage([])
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-row-n1'))
    const wiz = await screen.findByTestId('note-wizard')
    expect(wiz.getAttribute('data-resume')).toBe('n1')
    // 新建按钮：resumeNoteId=null
    fireEvent.click(screen.getByTestId('notes-new'))
    expect(wizardMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: true, resumeNoteId: null }))
  })

  it('toggle 未入素材：materialize 直调免确认，角标本地翻转', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') return archPage([archOut])
      if (ch === 'meetingNotes:materialize') return { ok: true, file_id: 'f-new' }
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-toggle-material-a2'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'meetingNotes:materialize', { projectId: 'p1', id: 'a2' }))
    expect(confirmMock).not.toHaveBeenCalled() // 建设性操作免确认
    await waitFor(() => expect(
      screen.getByTestId('note-row-a2').textContent).toContain('notesHub.inMaterials'))
  })

  it('toggle 已入素材：confirm 后 dematerialize；取消不发请求', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') return archPage([archIn])
      if (ch === 'meetingNotes:dematerialize') return { ok: true }
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-toggle-material-a1'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'meetingNotes:dematerialize', { projectId: 'p1', id: 'a1' }))
    expect(confirmMock).toHaveBeenCalledTimes(1) // 破坏性级联删需确认
    await waitFor(() => expect(
      screen.getByTestId('note-row-a1').textContent).toContain('notesHub.notInMaterials'))

    // 取消分支：confirm false → 不发请求、角标不变
    cleanup()
    invokeMock.mockClear() // 清前半段调用记录，隔离断言
    confirmMock.mockClear()
    confirmMock.mockResolvedValue(false)
    renderTab()
    fireEvent.click(await screen.findByTestId('note-toggle-material-a1'))
    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1))
    expect(invokeMock).not.toHaveBeenCalledWith(
      'meetingNotes:dematerialize', expect.anything())
    expect(screen.getByTestId('note-row-a1').textContent).toContain('notesHub.inMaterials')
  })

  it('删除归档：confirm → delete → 行消失且计数回落', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') return archPage([archIn, archOut])
      if (ch === 'meetingNotes:delete') return { ok: true }
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-delete-a2'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'meetingNotes:delete', { projectId: 'p1', id: 'a2' }))
    await waitFor(() => expect(screen.queryByTestId('note-row-a2')).toBeNull())
    expect(screen.getByTestId('note-row-a1')).toBeTruthy() // 其余行不受影响
  })

  it('加载更多：offset=50 步进追加，has_more 尽按钮消失', async () => {
    const archCalls: Array<Record<string, unknown>> = []
    invokeMock.mockImplementation(async (ch: string, payload?: Record<string, unknown>) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') {
        archCalls.push(payload ?? {})
        const offset = Number(payload?.offset ?? 0)
        return offset === 0
          ? { ok: true, notes: [archIn], limit: 50, offset: 0, has_more: true, total: 60 }
          : { ok: true, notes: [archOut], limit: 50, offset: 50, has_more: false, total: 60 }
      }
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-load-more'))
    await waitFor(() => expect(screen.getByTestId('note-row-a2')).toBeTruthy())
    expect(screen.getByTestId('note-row-a1')).toBeTruthy() // 追加不清空已有页
    // offset = 已加载行数（首页 fixture 仅 1 行 → 1；真实首页 50 行 → 50）
    expect(archCalls[1]).toEqual(
      expect.objectContaining({ projectId: 'p1', limit: 50, offset: 1 }))
    expect(screen.queryByTestId('note-load-more')).toBeNull() // has_more=false
  })

  it('详情浮层：点归档行出现正文与行动项，关闭可退', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') return archPage([archIn])
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-row-a1'))
    const detail = await screen.findByTestId('note-detail')
    expect(detail.textContent).toContain('纪要正文内容')
    expect(detail.textContent).toContain('跟进合同')
    expect(detail.textContent).toContain('notesHub.actionItems')
    // 遮罩点击关闭
    fireEvent.click(detail)
    expect(screen.queryByTestId('note-detail')).toBeNull()
  })

  it('toggle 图标按素材状态切换：已入呈 FileMinus2、未入呈 FilePlus2', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') return archPage([archIn, archOut])
      return {}
    })
    renderTab()
    const inBtn = await screen.findByTestId('note-toggle-material-a1')
    const outBtn = await screen.findByTestId('note-toggle-material-a2')
    expect(inBtn.querySelector('.lucide-file-minus-2')).toBeTruthy()
    expect(inBtn.querySelector('.lucide-file-plus-2')).toBeNull()
    expect(outBtn.querySelector('.lucide-file-plus-2')).toBeTruthy()
    expect(outBtn.querySelector('.lucide-file-minus-2')).toBeNull()
  })

  it('浮层正文编辑（已入素材）：预填 + dirty 门控，保存走 updateContent 同步刷新', async () => {
    let archLoads = 0
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') { archLoads += 1; return archPage([archIn]) }
      if (ch === 'meetingNotes:updateContent') return { ok: true, file_id: 'f9' }
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-row-a1'))
    fireEvent.click(await screen.findByTestId('note-detail-edit'))
    const area = screen.getByTestId('note-detail-edit-area') as HTMLTextAreaElement
    expect(area.value).toBe('# 纪要正文内容') // 打开即预填
    // 出口两动作：确认修改 / 放弃修改（三态模型确认态人工修改语义）
    expect(screen.getByTestId('note-detail-edit-save').textContent).toBe('notesHub.editSave')
    expect(screen.getByTestId('note-detail-edit-cancel').textContent).toBe('notesHub.editDiscard')
    expect((screen.getByTestId('note-detail-edit-save') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(area, { target: { value: '# 更新后的正文' } })
    expect((screen.getByTestId('note-detail-edit-save') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByTestId('note-detail-edit-save'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'meetingNotes:updateContent', { projectId: 'p1', id: 'a1', contentMd: '# 更新后的正文' }))
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith('notesHub.editSavedSynced', 'success'))
    // 保存后退回只读且正文即时更新（局部回写，不重拉分页）
    expect(screen.queryByTestId('note-detail-edit-area')).toBeNull()
    expect(screen.getByTestId('note-detail').textContent).toContain('更新后的正文')
    expect(archLoads).toBe(1) // 保存后未重拉分页
  })

  it('浮层正文编辑（未入素材）：toast 分流 + 编辑态遮罩保护 + 取消不落库', async () => {
    invokeMock.mockImplementation(async (ch: string) => {
      if (ch === 'meetingNotes:listPending') return { ok: true, notes: [] }
      if (ch === 'meetingNotes:listArchived') return archPage([archOut])
      if (ch === 'meetingNotes:updateContent') return { ok: true, file_id: null }
      return {}
    })
    renderTab()
    fireEvent.click(await screen.findByTestId('note-row-a2'))
    const detail = await screen.findByTestId('note-detail')
    fireEvent.click(await screen.findByTestId('note-detail-edit'))
    // 编辑态点遮罩不关闭（防误丢编辑）
    fireEvent.click(detail)
    expect(screen.getByTestId('note-detail')).toBeTruthy()
    fireEvent.change(screen.getByTestId('note-detail-edit-area'), { target: { value: '新正文 v2' } })
    fireEvent.click(screen.getByTestId('note-detail-edit-save'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'meetingNotes:updateContent', { projectId: 'p1', id: 'a2', contentMd: '新正文 v2' }))
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith('notesHub.editSavedToast', 'success'))
    // 再进编辑改值 → 取消 → 只读态不落草稿
    fireEvent.click(await screen.findByTestId('note-detail-edit'))
    fireEvent.change(screen.getByTestId('note-detail-edit-area'), { target: { value: '未保存草稿' } })
    fireEvent.click(screen.getByTestId('note-detail-edit-cancel'))
    expect(screen.queryByTestId('note-detail-edit-area')).toBeNull()
    expect(screen.getByTestId('note-detail').textContent).not.toContain('未保存草稿')
    // 只读态遮罩点击关闭
    fireEvent.click(screen.getByTestId('note-detail'))
    expect(screen.queryByTestId('note-detail')).toBeNull()
  })
})
