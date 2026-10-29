/** tests/StickyDrawer.test.tsx — 便签全局轻抽屉（2.12 §4）组件契约单测。
 *
 * 覆盖：window 事件调起渲染收件箱卡片 / Enter 创建后刷新并清空输入 /
 * 归档成功 toast（带「查看」跳转）且卡片移出收件箱 / 归档 409 错误 toast 不刷新 /
 * 转事务成功 toast 且便签原地留存 / 删除经确认后移除 /
 * Ctrl+Shift+N 开关 / Esc 关闭（输入框聚焦豁免）。
 * ConfirmDialog 恒确认；Toast 打桩捕获入参；IPC 全部经 invokeMock 捕获
 * （模式对齐 WebLogins.test.tsx）。
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

vi.mock('../src/features/sticky/StickyDrawer.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

// 删除路径需要确认框；恒确认，捕获入参断言文案通道
const confirmMock = vi.hoisted(() => vi.fn())
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))

// toast 打桩：捕获 (message, type, options) 断言各动线反馈
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

import { StickyDrawer } from '../src/features/sticky/StickyDrawer'

const invokeMock = vi.fn()

const NOTE = {
  id: 'n1', content: '第一笔便签', color: 'yellow',
  project_id: null, archived_at: null,
  created_at: '2026-10-27T10:00:00', updated_at: '2026-10-27T10:00:00',
}

const PROJECTS = [{ id: 'p1', name: '华润项目' }]

const listCalls = () => invokeMock.mock.calls.filter(([c]) => c === 'sticky:list').length

/** 渲染并经 window 事件调起（NavTabs 入口同通道） */
const openDrawer = () => {
  render(<StickyDrawer />)
  fireEvent(window, new Event('sticky:toggle-drawer'))
}

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'sticky:list') return { notes: [NOTE], has_more: false, total: 1 }
    if (channel === 'knowledge:listProjects') return { projects: PROJECTS }
    return {}
  })
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(true)
  toastMock.mockClear()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('StickyDrawer（2.12 §4 便签抽屉）', () => {
  it('事件调起：拉收件箱列表渲染卡片，同时拉项目选择器数据', async () => {
    openDrawer()
    expect(await screen.findByText('第一笔便签')).toBeTruthy()
    expect(invokeMock).toHaveBeenCalledWith('sticky:list', { view: 'inbox', limit: 50 })
    expect(invokeMock).toHaveBeenCalledWith('knowledge:listProjects', { limit: 0 })
    // 入口按钮不在抽屉内（NavTabs 负责），抽屉自身仅承载面板
    expect(screen.getByTestId('sticky-drawer')).toBeTruthy()
  })

  it('Enter 创建：调 sticky:create，列表刷新且输入框清空', async () => {
    let calls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'sticky:list') {
        calls++
        return calls === 1
          ? { notes: [], has_more: false, total: 0 }
          : { notes: [NOTE], has_more: false, total: 1 }
      }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      return { note: NOTE }
    })
    openDrawer()
    await screen.findByText('sticky.emptyInbox')

    const input = screen.getByTestId('sticky-input') as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: '  新便签  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('sticky:create', { content: '新便签' })
    })
    expect(await screen.findByText('第一笔便签')).toBeTruthy()
    await waitFor(() => expect(listCalls()).toBe(2))
    expect(input.value).toBe('')
  })

  it('归档成功：toast 带项目名与「查看」动作，卡片移出收件箱', async () => {
    let calls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'sticky:list') {
        calls++
        return calls === 1
          ? { notes: [NOTE], has_more: false, total: 1 }
          : { notes: [], has_more: false, total: 0 }
      }
      if (channel === 'knowledge:listProjects') return { projects: PROJECTS }
      return { note: { ...NOTE, project_id: 'p1', archived_at: '2026-10-27T11:00:00' } }
    })
    openDrawer()
    await screen.findByText('第一笔便签')

    fireEvent.click(screen.getByText('sticky.archive'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'p1' } })
    fireEvent.click(screen.getByText('sticky.confirmArchive'))

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('sticky:archiveToProject', { id: 'n1', projectId: 'p1' })
    })
    expect(toastMock).toHaveBeenCalledWith('sticky.archivedToast', 'success',
      expect.objectContaining({ action: expect.objectContaining({ label: 'sticky.viewProject' }) }))
    await waitFor(() => expect(screen.queryByText('第一笔便签')).toBeNull())
  })

  it('归档 409：显示后端错误 toast，不刷新列表', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'sticky:list') return { notes: [NOTE], has_more: false, total: 1 }
      if (channel === 'knowledge:listProjects') return { projects: PROJECTS }
      // 动作通道 structuredPythonResponse 失败形态：{ok:false, code, error}
      return { ok: false, code: 'STICKY_ALREADY_ARCHIVED', error: 'note already archived' }
    })
    openDrawer()
    await screen.findByText('第一笔便签')

    fireEvent.click(screen.getByText('sticky.archive'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'p1' } })
    fireEvent.click(screen.getByText('sticky.confirmArchive'))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith('note already archived', 'error')
    })
    expect(listCalls()).toBe(1) // 失败不触发刷新
  })

  it('转事务成功：toast 反馈且便签原地留存（不重拉列表）', async () => {
    openDrawer()
    await screen.findByText('第一笔便签')

    fireEvent.click(screen.getByText('sticky.toAffair'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'p1' } })
    fireEvent.click(screen.getByText('sticky.confirmAffair'))

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('sticky:toAffair',
        { id: 'n1', projectId: 'p1', dueDate: null })
    })
    expect(toastMock).toHaveBeenCalledWith('sticky.affairToast', 'success',
      expect.objectContaining({ action: expect.objectContaining({ label: 'sticky.viewAffair' }) }))
    // 原地留存：不重拉列表，卡片仍在
    expect(listCalls()).toBe(1)
    expect(screen.getByText('第一笔便签')).toBeTruthy()
  })

  it('删除：经确认后调 sticky:delete 并刷新移除', async () => {
    let calls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'sticky:list') {
        calls++
        return calls === 1
          ? { notes: [NOTE], has_more: false, total: 1 }
          : { notes: [], has_more: false, total: 0 }
      }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      return { ok: true }
    })
    openDrawer()
    await screen.findByText('第一笔便签')

    fireEvent.click(screen.getByText('sticky.delete'))
    expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'sticky.deleteTitle' }))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('sticky:delete', { id: 'n1' })
    })
    await waitFor(() => expect(screen.queryByText('第一笔便签')).toBeNull())
  })

  it('超软限（5000 字）提示但不拦截保存（先接住原则）', async () => {
    let calls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'sticky:list') {
        calls++
        return calls === 1
          ? { notes: [], has_more: false, total: 0 }
          : { notes: [{ ...NOTE, content: 'x' }], has_more: false, total: 1 }
      }
      if (channel === 'knowledge:listProjects') return { projects: [] }
      return { note: NOTE }
    })
    openDrawer()
    await screen.findByText('sticky.emptyInbox')

    // 正常长度：显示常规 hint
    expect(screen.getByText('sticky.inputHint')).toBeTruthy()

    const input = screen.getByTestId('sticky-input') as HTMLTextAreaElement
    fireEvent.change(input, { target: { value: 'a'.repeat(5001) } })
    expect(screen.getByText('sticky.overLimit')).toBeTruthy()
    // 软限不禁存：存钮仍可点击，Enter 仍触发创建
    expect((screen.getByText('sticky.save') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('sticky:create', { content: 'a'.repeat(5001) })
    })
  })

  it('已归档视图：仅复制/编辑（无归档/转事务/删除），卡片显项目名', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'sticky:list') {
        return { notes: [{ ...NOTE, project_id: 'p1', archived_at: '2026-10-27T11:00:00' }], has_more: false, total: 1 }
      }
      if (channel === 'knowledge:listProjects') return { projects: PROJECTS }
      return {}
    })
    openDrawer()
    await screen.findByText('第一笔便签')
    fireEvent.click(screen.getByText('sticky.tab.archived'))
    // usePagedList 随 view 变化重拉（params: {view:'archived'}）
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('sticky:list', { view: 'archived', limit: 50 })
    })
    expect(screen.getByText('华润项目')).toBeTruthy() // 项目名角标
    expect(screen.getByText('sticky.copy')).toBeTruthy()
    expect(screen.getByText('sticky.edit')).toBeTruthy()
    expect(screen.queryByText('sticky.archive')).toBeNull()
    expect(screen.queryByText('sticky.toAffair')).toBeNull()
    expect(screen.queryByText('sticky.delete')).toBeNull()
  })

  it('Ctrl+Shift+N 开关抽屉', () => {
    render(<StickyDrawer />)
    expect(screen.queryByTestId('sticky-drawer-overlay')).toBeNull()
    fireEvent.keyDown(window, { key: 'N', ctrlKey: true, shiftKey: true })
    expect(screen.getByTestId('sticky-drawer-overlay')).toBeTruthy()
    fireEvent.keyDown(window, { key: 'N', ctrlKey: true, shiftKey: true })
    expect(screen.queryByTestId('sticky-drawer-overlay')).toBeNull()
  })

  it('Esc 关闭；输入框聚焦时豁免（两段式）', async () => {
    openDrawer()
    await screen.findByText('第一笔便签')
    // 输入框聚焦：Esc 不关
    const input = screen.getByTestId('sticky-input')
    input.focus()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.getByTestId('sticky-drawer-overlay')).toBeTruthy()
    // 非输入焦点：Esc 关
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByTestId('sticky-drawer-overlay')).toBeNull()
  })
})
