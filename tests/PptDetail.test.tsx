/** tests/PptDetail.test.tsx — PPT 详情页（2.3 §7.1 PptDetail + PptConfirmCard Generate 形态）
 *  覆盖：running 状态条与操作组显隐、awaiting 门禁内嵌 iframe（端口锁 → 127.0.0.1:port）、
 *  无端口时 confirmClosed + 继续按钮走 ppt:resume 兜底、completed 下载载荷上抛、
 *  详情拉取失败 loadFailed。轮询/心跳基建（usePolling/useEdgeRefetch/ActivityContext）打桩隔离。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const activityState = { items: [] as Array<{ entity_id?: string }> }
const toastMock = vi.fn()

vi.mock('react-i18next', () => ({
  // i18n.language：TaskHistoryReplay 气泡时间戳格式化需要
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh-CN' } }),
}))

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => activityState,
}))

vi.mock('../src/hooks/usePolling', () => ({
  usePolling: () => undefined,
}))

vi.mock('../src/hooks/useEdgeRefetch', () => ({
  useEdgeRefetch: () => undefined,
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('../src/features/project/tabs/ppt.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { PptDetail } from '../src/features/project/tabs/PptDetail'

const invokeMock = vi.fn()

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const basePpt = {
  id: 'p1',
  task_id: 't1',
  title: '季度汇报',
  status: 'running',
  stage: 'authoring',
  progress: 0.4,
  page_count: 10,
  page_done: 4,
  workspace_path: 'D:/ws/t1',
  export_path: null as string | null,
  error: null as string | null,
  created_at: '2026-10-16T10:00:00Z',
  task_status: 'running',
  confirm_ui_port: null as number | null,
  pages: [] as string[],
}

beforeEach(() => {
  activityState.items = [{ entity_id: 'p1' }]
  toastMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'ppt:get') return { ppt: { ...basePpt } }
    if (channel === 'tasks:getMessages') {
      return { messages: [{ id: 'm1', task_id: 't1', role: 'user', content: '[InkCube PPT 任务]', created_at: '2026-10-16T10:00:00Z' }] }
    }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('PptDetail', () => {
  it('running：状态条渲染、无 iframe、暂停/取消可用、无下载', async () => {
    render(<PptDetail pptId="p1" onBack={() => {}} onDeleted={() => {}} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('ppt:get', 'p1')
    expect(screen.getByText('project.ppt.status.running')).toBeTruthy()
    expect(screen.getByText('project.ppt.stage.authoring')).toBeTruthy()
    expect(screen.getByText('project.ppt.pageProgress')).toBeTruthy()
    expect(document.querySelector('iframe')).toBeNull()
    expect(screen.getByText('project.ppt.detail.pause')).toBeTruthy()
    expect(screen.getByText('project.ppt.detail.cancel')).toBeTruthy()
    expect(screen.queryByText('project.ppt.detail.download')).toBeNull()
  })

  it('awaiting + 端口锁 → 内嵌原生确认页 iframe（127.0.0.1:port）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt:get') {
        return { ppt: { ...basePpt, status: 'awaiting_input', confirm_ui_port: 5051 } }
      }
      if (channel === 'tasks:getMessages') return { messages: [] }
      return {}
    })
    render(<PptDetail pptId="p1" onBack={() => {}} onDeleted={() => {}} />)
    await flush()
    const frame = document.querySelector('iframe')
    expect(frame).not.toBeNull()
    expect(frame!.getAttribute('src')).toBe('http://127.0.0.1:5051/')
    expect(screen.getByText('project.ppt.detail.confirmBanner')).toBeTruthy()
  })

  it('awaiting 无端口 → confirmClosed + 继续按钮走 ppt:resume 兜底', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt:get') {
        return { ppt: { ...basePpt, status: 'awaiting_input', confirm_ui_port: null } }
      }
      if (channel === 'tasks:getMessages') return { messages: [] }
      return {}
    })
    render(<PptDetail pptId="p1" onBack={() => {}} onDeleted={() => {}} />)
    await flush()
    expect(document.querySelector('iframe')).toBeNull()
    expect(screen.getByText('project.ppt.detail.confirmClosed')).toBeTruthy()
    // 门禁条内 + 操作组各一枚继续按钮
    fireEvent.click(screen.getAllByText('project.ppt.detail.resume')[0])
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('ppt:resume', 'p1')
  })

  it('completed：下载/打开按钮出现，下载载荷上抛 IPC', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt:get') {
        return { ppt: { ...basePpt, status: 'completed', export_path: 'exports/deck.pptx' } }
      }
      if (channel === 'tasks:getMessages') return { messages: [] }
      return {}
    })
    render(<PptDetail pptId="p1" onBack={() => {}} onDeleted={() => {}} />)
    await flush()
    expect(screen.getByText('project.ppt.status.completed')).toBeTruthy()
    expect(screen.queryByText('project.ppt.detail.pause')).toBeNull()
    fireEvent.click(screen.getByText('project.ppt.detail.download'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('ppt:downloadExport', {
      workspacePath: 'D:/ws/t1',
      exportPath: 'exports/deck.pptx',
      title: '季度汇报',
    })
  })

  it('详情拉取失败 → loadFailed 文案', async () => {
    invokeMock.mockImplementation(async () => ({}))
    render(<PptDetail pptId="p1" onBack={() => {}} onDeleted={() => {}} />)
    await flush()
    expect(screen.getByText('project.ppt.detail.loadFailed')).toBeTruthy()
  })
})
