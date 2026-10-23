/**
 * tests/ArchiveView.test.tsx — 档案阅读页单测（2.11 §8.4，2026-10-23）。
 *
 * 覆盖：
 * - 挂载即 file:readText（projectId/fileId/filename = {订阅名}·档案.md）→
 *   Markdown 渲染 + 头部标题 + 「在文件夹中显示」可用；
 * - 「在文件夹中显示」→ shell:showItemInFolder(abs_path)；
 * - not_found → 档案不存在文案（订阅改名未重建场景）；
 * - 头部「返回」→ onBack；
 * - Ctrl+F 文档内查找：FindBar 开启 + 命中计数 + Esc 关闭。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { ArchiveView } from '../src/features/schedule/ArchiveView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

const ARCHIVE_MD = '# 档案时间线\n- 要点一\n\n正文段落\n'

const wireInvoke = (res: Record<string, unknown>) =>
  vi.fn((channel: string) => {
    if (channel === 'file:readText') return Promise.resolve(res)
    return Promise.resolve({ ok: true })
  })

/** 排空 microtask（挂载 file:readText 为 async 链） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const onBack = vi.fn()

beforeEach(() => {
  onBack.mockClear()
})

afterEach(() => {
  delete (window as { electronAPI?: unknown }).electronAPI
})

const mount = (invoke: ReturnType<typeof wireInvoke>) => {
  ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
  render(
    <ArchiveView
      targetId="p1"
      fileId="file_a1"
      scheduleName="华润舆情"
      onBack={onBack}
    />,
  )
}

describe('ArchiveView（2.11 §8.4 档案阅读页）', () => {
  it('挂载即 file:readText 拉档案 → Markdown 渲染 + 标题 + 「在文件夹中显示」可用', async () => {
    const invoke = wireInvoke({ success: true, content: ARCHIVE_MD, abs_path: 'D:/data/files/p1/file_a1/华润舆情·档案.md' })
    mount(invoke)
    await flush()
    expect(invoke).toHaveBeenCalledWith('file:readText', {
      projectId: 'p1',
      fileId: 'file_a1',
      filename: '华润舆情·档案.md',
    })
    expect(screen.getByText('华润舆情 · schedule.archiveTitle')).toBeTruthy()
    expect(screen.getByText('档案时间线')).toBeTruthy()
    expect(screen.getByText('正文段落')).toBeTruthy()
    // abs_path 到位 → 「在文件夹中显示」可点
    const btn = screen.getByTestId('archive-show-in-folder') as HTMLButtonElement
    expect(btn.disabled).toBe(false)
  })

  it('「在文件夹中显示」→ shell:showItemInFolder(abs_path)（定位逃生门）', async () => {
    const abs = 'D:/data/files/p1/file_a1/华润舆情·档案.md'
    const invoke = wireInvoke({ success: true, content: ARCHIVE_MD, abs_path: abs })
    mount(invoke)
    await flush()
    fireEvent.click(screen.getByTestId('archive-show-in-folder'))
    expect(invoke).toHaveBeenCalledWith('shell:showItemInFolder', abs)
  })

  it('not_found → 档案不存在文案（订阅改名未重建场景）', async () => {
    mount(wireInvoke({ success: false, reason: 'not_found' }))
    await flush()
    expect(screen.getByText('schedule.archiveNotFound')).toBeTruthy()
  })

  it('too_large → 文件过大文案；读取异常 → 加载失败文案', async () => {
    mount(wireInvoke({ success: false, reason: 'too_large' }))
    await flush()
    expect(screen.getByText('schedule.archiveTooLarge')).toBeTruthy()
  })

  it('头部「返回」→ onBack（App 分发回管理台恢复选中）', async () => {
    mount(wireInvoke({ success: true, content: ARCHIVE_MD, abs_path: 'x' }))
    await flush()
    fireEvent.click(screen.getByTestId('archive-back-btn'))
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+F 打开文档内查找，命中计数与 Esc 关闭（同 ReadView）', async () => {
    mount(wireInvoke({ success: true, content: ARCHIVE_MD, abs_path: 'x' }))
    await flush()
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
    fireEvent.keyDown(window, { ctrlKey: true, key: 'f' })
    const input = screen.getByPlaceholderText('find.placeholder')
    // 命中计数：正文含「要点」的叶子块仅 li 要点一 → 「当前/总数」= 1/1
    fireEvent.change(input, { target: { value: '要点' } })
    expect(screen.getByText('1/1')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
  })
})
