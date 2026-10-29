/** tests/NoteWizardDrawer.test.tsx — 纪要两步向导抽屉（2.13 §4）组件契约单测。
 *
 * 覆盖：转写稿检测提示出现/不出现（免培训 §2.10）/ 生成门控（空材料禁用、
 * 图片解析 busy 禁用、材料就绪放开）/ 草稿恢复按 status 落步骤（draft 预填、
 * structuring 落进度态）/ 定稿驱动上传（选图零服务端痕迹、生成时才上传、
 * 失败中止）/ 确认步编辑行动项后 confirm 载荷正确（「待确认：」默认
 * 不勾、勾选与截止日可改）/ failed_tasks 红字清单渲染且重试保存重发同载荷
 * （saved_refs 幂等契约）。2.14 修复追加：存草稿（create/update 分流、新图不上传
 * 零痕迹、全空门控）/ 上一步回输入步（生成钮呈重新生成）/ 新图 gateHint 提示 /
 * settle 行消失整轮重置（noteId 不残留，存草稿走 create 回归保护）。
 * ConfirmDialog 恒确认；Toast 打桩捕获入参；IPC 全部
 * 经 invokeMock 捕获；useActivity 按 kind 分派可控 items（模式对齐 FormsTab.test）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

const activityByKind = vi.hoisted(() => ({
  note_structuring: { items: [] as Array<Record<string, unknown>> },
  file: { items: [] as Array<Record<string, unknown>> },
}))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind: string) =>
    (activityByKind as Record<string, { items: Array<Record<string, unknown>> }>)[kind] ?? { items: [] },
}))

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

vi.mock('../src/features/notes/NoteWizardDrawer.module.css', () => ({
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

import { NoteWizardDrawer } from '../src/features/notes/NoteWizardDrawer'

const invokeMock = vi.fn()
const onCloseMock = vi.fn()
const onChangedMock = vi.fn()

const draftRow = {
  id: 'n1', project_id: 'p1', title: '客户拜访', occurred_at: '2026-10-26',
  raw_text: '拜访要点记录', image_file_ids: ['f1'], status: 'draft',
  draft_md: null, saved_refs: null, last_error: null,
  created_at: '2026-10-26T10:00:00', updated_at: '2026-10-26T10:00:00',
}

const structuredRow = {
  ...draftRow,
  id: 'n2', title: '厂商会议', raw_text: null, image_file_ids: [], status: 'structured',
  draft_md: JSON.stringify({
    content_md: '拜访纪要正文',
    action_items: [{ title: '跟进合同修订' }, { title: '待确认：报价单审批' }],
  }),
}

const structuringRow = { ...draftRow, id: 'n3', status: 'structuring', image_file_ids: [] }

const openWizard = (resumeNoteId: string | null = null) => {
  render(
    <NoteWizardDrawer projectId="p1" open resumeNoteId={resumeNoteId}
      onClose={onCloseMock} onChanged={onChangedMock} />,
  )
}

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async () => ({}))
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(true)
  toastMock.mockClear()
  onCloseMock.mockClear()
  onChangedMock.mockClear()
  activityByKind.note_structuring.items = []
  activityByKind.file.items = []
  // jsdom 未实现 blob URL：选图预览 stub（定稿驱动用例依赖）
  ;(URL as unknown as { createObjectURL: (f: Blob) => string }).createObjectURL = vi.fn(() => 'blob:mock-url')
  ;(URL as unknown as { revokeObjectURL: (u: string) => void }).revokeObjectURL = vi.fn()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('NoteWizardDrawer（2.13 §4 纪要向导）', () => {
  it('转写稿检测：对话结构文本出现提示，普通记录不出现', async () => {
    openWizard()
    const raw = (await screen.findByTestId('note-raw-text')) as HTMLTextAreaElement
    fireEvent.change(raw, { target: { value: '会议讨论了预算与排期安排' } })
    expect(screen.queryByTestId('note-transcript-hint')).toBeNull()
    fireEvent.change(raw, { target: { value: '发言人张三：预算没问题\n发言人李四：排期下周同步' } })
    expect(await screen.findByTestId('note-transcript-hint')).toBeTruthy()
  })

  it('生成门控：无材料禁用，文本就绪放开；恢复草稿图片解析中再禁用', async () => {
    openWizard()
    const gen = (await screen.findByTestId('note-generate')) as HTMLButtonElement
    expect(gen.disabled).toBe(true) // 无文本无图
    fireEvent.change(screen.getByTestId('note-raw-text'), { target: { value: '拜访要点' } })
    expect(gen.disabled).toBe(false)

    // 图片解析中：项目 file 活动门控 + 活跃集 pending → busy → 禁用（spec §3 门控）
    activityByKind.file.items = [{ project_id: 'p1', count: 1 }]
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [draftRow] }
      if (channel === 'knowledge:getActiveFiles') return { files: [{ id: 'f1', image_status: 'pending' }] }
      return {}
    })
    cleanup()
    openWizard('n1')
    const gen2 = (await screen.findByTestId('note-generate')) as HTMLButtonElement
    await waitFor(() => expect(gen2.disabled).toBe(true))
    expect(screen.getByTestId('note-gate-hint').textContent).toContain('meetingNotes.imagesPending')
  })

  it('草稿恢复：draft 预填输入步（图片占位格），structuring 落进度态', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [draftRow] }
      return {}
    })
    openWizard('n1')
    const title = (await screen.findByTestId('note-title')) as HTMLInputElement
    await waitFor(() => expect(title.value).toBe('客户拜访'))
    expect((screen.getByTestId('note-raw-text') as HTMLTextAreaElement).value).toBe('拜访要点记录')
    expect(screen.getByTestId('note-thumbs')).toBeTruthy() // 已传图片占位格

    cleanup()
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [structuringRow] }
      return {}
    })
    openWizard('n3')
    expect(await screen.findByTestId('note-progress')).toBeTruthy()
  })

  it('草稿恢复回源缩略图：fileId → getImages → inkcube:// URL，无图保持占位', async () => {
    const twoImgs = { ...draftRow, id: 'n4', image_file_ids: ['f1', 'f2'] }
    invokeMock.mockImplementation(async (channel: string, payload?: { fileId?: string }) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [twoImgs] }
      if (channel === 'knowledge:getImages') {
        return payload?.fileId === 'f1'
          ? { images: [{ id: 'i1', file_id: 'f1', local_path: 'D:\\data\\p1\\images\\a.png' }] }
          : { images: [] } // f2 无图（未描述/已删）→ 占位保持
      }
      return {}
    })
    openWizard('n4')
    await screen.findByTestId('note-title')
    const thumb = await screen.findByTestId('note-thumbs')
    await waitFor(() => {
      const el = thumb.querySelector('img')
      expect(el?.getAttribute('src')).toBe(
        'inkcube://app?file=' + encodeURIComponent('D:/data/p1/images/a.png'))
    })
    expect(thumb.querySelectorAll('img')).toHaveLength(1) // 仅 f1 回源成功
  })

  it('确认步编辑保存：「待确认：」默认不勾，编辑后 confirm 载荷正确', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [structuredRow] }
      if (channel === 'meetingNotes:confirm') return { ok: true, created_tasks: 2, failed_tasks: [] }
      return {}
    })
    openWizard('n2')
    await screen.findByTestId('note-confirm')
    expect((screen.getByTestId('note-content-md') as HTMLTextAreaElement).value).toBe('拜访纪要正文')
    const t0 = screen.getByTestId('note-item-title-0') as HTMLInputElement
    const t1 = screen.getByTestId('note-item-title-1') as HTMLInputElement
    expect(t0.value).toBe('跟进合同修订')
    expect(t1.value).toBe('待确认：报价单审批')
    const boxes = screen.getByTestId('note-confirm').querySelectorAll('input[type="checkbox"]')
    expect((boxes[0] as HTMLInputElement).checked).toBe(true)
    expect((boxes[1] as HTMLInputElement).checked).toBe(false) // 「待确认：」默认不勾
    expect((boxes[2] as HTMLInputElement).checked).toBe(true) // 2.14：正文入库素材默认勾选
    // 编辑：改标题 + 勾选待确认项 + 设截止日
    fireEvent.change(t0, { target: { value: '跟进合同修订 V2' } })
    fireEvent.click(boxes[1])
    const dates = screen.getByTestId('note-confirm').querySelectorAll('input[type="date"]')
    fireEvent.change(dates[0], { target: { value: '2026-11-05' } })
    fireEvent.click(screen.getByTestId('note-save'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('meetingNotes:confirm', {
        projectId: 'p1', id: 'n2', contentMd: '拜访纪要正文', saveToMaterials: true,
        actionItems: [
          { title: '跟进合同修订 V2', selected: true, dueDate: '2026-11-05' },
          { title: '待确认：报价单审批', selected: true, dueDate: null },
        ],
      })
    })
    expect(toastMock).toHaveBeenCalledWith('meetingNotes.savedToast', 'success',
      expect.objectContaining({ action: expect.objectContaining({ label: 'meetingNotes.viewMaterials' }) }))
    expect(onChangedMock).toHaveBeenCalled()
    expect(onCloseMock).toHaveBeenCalled()
  })

  it('不勾存素材：载荷 saveToMaterials=false，toast 不引导去素材面板', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [structuredRow] }
      if (channel === 'meetingNotes:confirm') return { ok: true, created_tasks: 0, failed_tasks: [] }
      return {}
    })
    openWizard('n2')
    await screen.findByTestId('note-confirm')
    fireEvent.click(screen.getByTestId('note-save-materials')) // 取消默认勾选
    fireEvent.click(screen.getByTestId('note-save'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('meetingNotes:confirm',
        expect.objectContaining({ saveToMaterials: false }))
    })
    expect(toastMock).toHaveBeenCalledWith(
      'meetingNotes.savedToastNoMaterials', 'success', undefined)
  })

  it('进度态兜底轮询：心跳从未报活跃（LLM 秒完无边沿）也能流转确认步', async () => {
    // 首查 structuring（恢复落进度态），轮询 tick 后查到 structured——
    // 若删兜底 usePolling，用例卡进度态必红（真回归保护，复审抓修）
    let listCalls = 0
    const settled = { ...structuringRow, status: 'structured', draft_md: structuredRow.draft_md }
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') {
        listCalls++
        return { ok: true, notes: [listCalls === 1 ? structuringRow : settled] }
      }
      return {}
    })
    openWizard('n3') // 首查 structuring → 恢复落进度态；activityByKind 全空 = 心跳无活跃
    expect(await screen.findByTestId('note-progress')).toBeTruthy()
    await screen.findByTestId('note-confirm') // usePolling enabled 立即 tick → structured → 确认步
  })

  it('生成前复核活跃集：心跳空窗 busy 图拦截（imagesPending）不发 structure', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [draftRow] }
      if (channel === 'knowledge:getActiveFiles') return { files: [{ id: 'f1', image_status: 'pending' }] }
      return {}
    })
    openWizard('n1') // draft 恢复：预填文本 + 图片占位（f1）
    const gen = (await screen.findByTestId('note-generate')) as HTMLButtonElement
    await waitFor(() => expect(gen.disabled).toBe(false)) // 心跳无 file 活动 → 门控误判就绪
    fireEvent.click(gen)
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith(
      expect.stringContaining('meetingNotes.imagesPending'), 'error'))
    expect(invokeMock).not.toHaveBeenCalledWith('meetingNotes:update', expect.anything())
    expect(invokeMock).not.toHaveBeenCalledWith('meetingNotes:structure', expect.anything())
  })

  it('定稿驱动上传：选图零服务端痕迹，生成时才上传（失败中止），成功续跑 create/structure', async () => {
    // 上传成功链：uploadFile → getActiveFiles（空集）→ create → structure → 进度态
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:uploadFile') return { success: true, file_id: 'srv-f1' }
      if (channel === 'meetingNotes:create') return { ok: true, note: { id: 'n9' } }
      if (channel === 'meetingNotes:structure') return { ok: true }
      return {}
    })
    openWizard()
    const raw = screen.getByTestId('note-raw-text') as HTMLTextAreaElement
    fireEvent.change(raw, { target: { value: '拜访要点' } })
    // 选图（纯本地预览）：不触任何上传
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const img = new File(['x'], 'a.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [img] } })
    await waitFor(() => expect(screen.getByTestId('note-thumbs').querySelector('img')).toBeTruthy())
    expect(invokeMock).not.toHaveBeenCalledWith('knowledge:uploadFile', expect.anything())
    // 生成（定稿）：此刻才上传 → create 载荷带服务端 fid → structure 受理
    fireEvent.click(screen.getByTestId('note-generate'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'knowledge:uploadFile',
      expect.objectContaining({ projectId: 'p1', filename: 'a.png', fileType: '.png' })))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'meetingNotes:create', expect.objectContaining({ imageFileIds: ['srv-f1'] })))
    expect(invokeMock).toHaveBeenCalledWith('meetingNotes:structure', { projectId: 'p1', id: 'n9' })

    // 上传失败中止：留在输入步，不写 create/structure
    invokeMock.mockReset()
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:uploadFile') return { success: false, message: '磁盘已满' }
      return {}
    })
    cleanup()
    openWizard()
    fireEvent.change(screen.getByTestId('note-raw-text'), { target: { value: '拜访要点' } })
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement,
      { target: { files: [new File(['x'], 'b.png', { type: 'image/png' })] } })
    fireEvent.click(await screen.findByTestId('note-generate'))
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith('b.png: 磁盘已满', 'error'))
    expect(invokeMock).not.toHaveBeenCalledWith('meetingNotes:create', expect.anything())
    expect(invokeMock).not.toHaveBeenCalledWith('meetingNotes:structure', expect.anything())
  })

  it('failed_tasks 红字清单渲染，重试保存重发同载荷（saved_refs 幂等）', async () => {
    let calls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [structuredRow] }
      if (channel === 'meetingNotes:confirm') {
        calls++
        return calls === 1
          ? { ok: true, created_tasks: 1, failed_tasks: ['跟进合同修订'] }
          : { ok: true, created_tasks: 2, failed_tasks: [] }
      }
      return {}
    })
    openWizard('n2')
    await screen.findByTestId('note-confirm')
    fireEvent.click(screen.getByTestId('note-save'))
    const failedBox = await screen.findByTestId('note-failed-tasks')
    expect(failedBox.textContent).toContain('meetingNotes.partialFailed')
    expect(failedBox.textContent).toContain('跟进合同修订')
    expect(failedBox.textContent).toContain('meetingNotes.retryHint')
    const save = screen.getByTestId('note-save') as HTMLButtonElement
    expect(save.disabled).toBe(false) // 重试入口：保存钮仍在
    fireEvent.click(save)
    await waitFor(() => expect(calls).toBe(2))
    expect(toastMock).toHaveBeenCalledWith('meetingNotes.savedToast', 'success', expect.anything())
  })

  it('存草稿：新建走 create 落输入态字段，成功后 toast + 通知宿主 + 关闭', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:create') return { ok: true, note: { id: 'n8' } }
      return {}
    })
    openWizard()
    fireEvent.change(await screen.findByTestId('note-title'), { target: { value: '周五拜访' } })
    fireEvent.change(screen.getByTestId('note-raw-text'), { target: { value: '拜访要点' } })
    fireEvent.click(screen.getByTestId('note-save-draft'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('meetingNotes:create', {
      projectId: 'p1', title: '周五拜访', occurredAt: expect.any(String),
      rawText: '拜访要点', imageFileIds: [],
    }))
    expect(toastMock).toHaveBeenCalledWith('meetingNotes.draftSaved', 'success')
    expect(onChangedMock).toHaveBeenCalled()
    expect(onCloseMock).toHaveBeenCalled()
  })

  it('存草稿：恢复行走 update，新图不上传仅落已传 fids（定稿驱动零痕迹）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [draftRow] }
      if (channel === 'meetingNotes:update') return { ok: true }
      return {}
    })
    openWizard('n1')
    await waitFor(() => expect(
      (screen.getByTestId('note-title') as HTMLInputElement).value).toBe('客户拜访'))
    // 选新图（未上传，fileId 为空）
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement,
      { target: { files: [new File(['x'], 'b.png', { type: 'image/png' })] } })
    await waitFor(() => expect(screen.getByTestId('note-thumbs').children.length).toBe(2))
    fireEvent.click(screen.getByTestId('note-save-draft'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('meetingNotes:update', {
      projectId: 'p1', id: 'n1', title: '客户拜访', occurredAt: '2026-10-26',
      rawText: '拜访要点记录', imageFileIds: ['f1'], // 新图被滤掉，不上传
    }))
    expect(invokeMock).not.toHaveBeenCalledWith('knowledge:uploadFile', expect.anything())
  })

  it('存草稿门控：输入全空不渲染按钮', async () => {
    openWizard()
    await screen.findByTestId('note-title')
    expect(screen.queryByTestId('note-save-draft')).toBeNull()
  })

  it('上一步：structured 确认步回输入步，生成钮呈重新生成、存草稿可用', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [structuredRow] }
      return {}
    })
    openWizard('n2')
    await screen.findByTestId('note-confirm')
    fireEvent.click(screen.getByTestId('note-back-input'))
    const gen = await screen.findByTestId('note-generate')
    expect(gen.textContent).toContain('meetingNotes.regenerate')
    expect(screen.getByTestId('note-save-draft')).toBeTruthy()
  })

  it('新图提示：选本地图未上传，gateHint 呈 newImagesHint（定稿驱动本地即就绪）', async () => {
    openWizard()
    fireEvent.change(await screen.findByTestId('note-raw-text'), { target: { value: '拜访要点' } })
    expect(screen.getByTestId('note-gate-hint').textContent)
      .toContain('meetingNotes.outputHint')
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement,
      { target: { files: [new File(['x'], 'b.png', { type: 'image/png' })] } })
    await waitFor(() => expect(screen.getByTestId('note-gate-hint').textContent)
      .toContain('meetingNotes.newImagesHint'))
  })

  it('settle 行消失：整轮重置回输入步，存草稿走 create（noteId 不残留回归保护）', async () => {
    let listCalls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') {
        listCalls++
        return listCalls === 1
          ? { ok: true, notes: [structuringRow] }
          : { ok: true, notes: [] } // 行已消失（异常清理）
      }
      if (channel === 'meetingNotes:create') return { ok: true, note: { id: 'n8' } }
      return {}
    })
    openWizard('n3')
    expect(await screen.findByTestId('note-progress')).toBeTruthy()
    await screen.findByTestId('note-generate') // 兜底轮询 tick：行消失 → 整轮重置回输入步
    fireEvent.change(screen.getByTestId('note-raw-text'), { target: { value: '重来一份' } })
    fireEvent.click(screen.getByTestId('note-save-draft'))
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith(
      'meetingNotes:create', expect.objectContaining({ rawText: '重来一份' })))
    expect(invokeMock).not.toHaveBeenCalledWith('meetingNotes:update', expect.anything())
  })

  it('暂存（三态格式化态暂存）：人工修改正文 updateContent 存回行内，不走 confirm 不归档', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'meetingNotes:listPending') return { ok: true, notes: [structuredRow] }
      if (channel === 'meetingNotes:updateContent') return { ok: true, file_id: null }
      return {}
    })
    openWizard('n2')
    await screen.findByTestId('note-confirm')
    fireEvent.change(screen.getByTestId('note-content-md'), { target: { value: '人工修改稿' } })
    fireEvent.click(screen.getByTestId('note-stash'))
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith(
      'meetingNotes.stashSaved', 'success'))
    expect(invokeMock).toHaveBeenCalledWith('meetingNotes:updateContent',
      { projectId: 'p1', id: 'n2', contentMd: '人工修改稿' })
    expect(invokeMock).not.toHaveBeenCalledWith('meetingNotes:confirm', expect.anything())
    expect(onChangedMock).toHaveBeenCalled()
    expect(onCloseMock).toHaveBeenCalled()
  })
})
