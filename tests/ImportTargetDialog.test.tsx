/** tests/ImportTargetDialog.test.tsx — 0.5 E2 T5:导入目标弹层。
 *  覆盖:双 tab 列表(项目/基础库懒加载)、未选禁用、确认调 upload、
 *  duplicate 仍 onDone 并出「已存在」toast、失败 toast。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ToastProvider } from '../src/features/knowledge/Toast'
import { ImportTargetDialog } from '../src/features/task/ImportTargetDialog'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  ;(window as any).electronAPI = { invoke: invokeMock }
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'knowledge:listProjects') {
      return { projects: [{ id: 'p1', name: '项目A' }, { id: 'p2', name: '项目B' }] }
    }
    if (channel === 'baseLib:list') {
      return { base_libs: [{ id: 'b1', name: '通用库' }] }
    }
    if (channel === 'tasks:readFile') {
      return { type: 'file', data: 'aGVsbG8=', size: 5, filename: 'report.md' }
    }
    return { success: true, file_id: 'file_x' }
  })
})

function renderDialog(overrides = {}) {
  const props = {
    taskId: 't1',
    path: 'out/report.md',
    filename: 'report.md',
    onDone: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  }
  render(
    <ToastProvider>
      <ImportTargetDialog {...props} />
    </ToastProvider>,
  )
  return props
}

describe('ImportTargetDialog (0.5 E2 T5)', () => {
  it('默认项目 tab：拉取并列出项目；未选中时确认按钮 disabled', async () => {
    renderDialog()
    await waitFor(() => expect(screen.getByText('项目A')).toBeTruthy())
    const confirm = screen.getByRole('button', { name: 'task.import.confirm' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
  })

  it('选中目标后确认 enabled；确认调用 knowledge:uploadFile 并 onDone', async () => {
    const props = renderDialog()
    await waitFor(() => expect(screen.getByText('项目A')).toBeTruthy())
    fireEvent.click(screen.getByText('项目A'))
    const confirm = screen.getByRole('button', { name: 'task.import.confirm' }) as HTMLButtonElement
    expect(confirm.disabled).toBe(false)
    fireEvent.click(confirm)
    await waitFor(() => expect(props.onDone).toHaveBeenCalledWith('file_x'))
    expect(invokeMock).toHaveBeenCalledWith('knowledge:uploadFile', expect.objectContaining({
      projectId: 'p1', filename: 'report.md', fileType: 'md',
      data: 'data:application/octet-stream;base64,aGVsbG8=',
    }))
  })

  it('切基础库 tab 懒加载 baseLib:list 并列出库', async () => {
    renderDialog()
    await waitFor(() => expect(screen.getByText('项目A')).toBeTruthy())
    expect(invokeMock.mock.calls.filter(([c]) => c === 'baseLib:list').length).toBe(0)
    fireEvent.click(screen.getByText('task.import.tabBaseLib'))
    await waitFor(() => expect(screen.getByText('通用库')).toBeTruthy())
    expect(invokeMock).toHaveBeenCalledWith('baseLib:list', { limit: 0 })
  })

  it('duplicate 响应仍算完成：onDone 回调且出「已存在」toast', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:listProjects') {
        return { projects: [{ id: 'p1', name: '项目A' }] }
      }
      if (channel === 'tasks:readFile') {
        return { type: 'file', data: 'aGVsbG8=', size: 5, filename: 'report.md' }
      }
      return { success: true, file_id: 'file_dup', duplicate: true }
    })
    const props = renderDialog()
    await waitFor(() => expect(screen.getByText('项目A')).toBeTruthy())
    fireEvent.click(screen.getByText('项目A'))
    fireEvent.click(screen.getByRole('button', { name: 'task.import.confirm' }))
    await waitFor(() => expect(props.onDone).toHaveBeenCalledWith('file_dup'))
    expect(screen.getByText('task.import.duplicate')).toBeTruthy()
  })

  it('上传失败出 failed toast，不回调 onDone', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:listProjects') {
        return { projects: [{ id: 'p1', name: '项目A' }] }
      }
      if (channel === 'tasks:readFile') {
        return { type: 'file', data: 'aGVsbG8=', size: 5, filename: 'report.md' }
      }
      throw Object.assign(new Error('boom'), { errorCode: 'PARSE_MODEL_MISSING' })
    })
    const props = renderDialog()
    await waitFor(() => expect(screen.getByText('项目A')).toBeTruthy())
    fireEvent.click(screen.getByText('项目A'))
    fireEvent.click(screen.getByRole('button', { name: 'task.import.confirm' }))
    await waitFor(() => expect(screen.getByText(/task\.import\.failed/)).toBeTruthy())
    expect(props.onDone).not.toHaveBeenCalled()
  })

  it('项目列表加载失败 → import-targets-error + 重试恢复，不伪装零目标（静默 catch 排查）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:listProjects') throw new Error('boom')
      if (channel === 'baseLib:list') return { base_libs: [] }
      return { success: true, file_id: 'file_x' }
    })
    renderDialog()
    await waitFor(() => expect(screen.getByTestId('import-targets-error')).toBeTruthy())
    expect(screen.getByText('task.import.loadFailed')).toBeTruthy()
    // 不得伪装「暂无可用目标」（拉取失败 ≠ 零目标）
    expect(screen.queryByText('task.import.emptyTargets')).toBeNull()
    // 重试恢复
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'knowledge:listProjects') return { projects: [{ id: 'p1', name: '项目A' }] }
      if (channel === 'baseLib:list') return { base_libs: [] }
      return { success: true, file_id: 'file_x' }
    })
    fireEvent.click(screen.getByText('knowledge.retry'))
    await waitFor(() => expect(screen.getByText('项目A')).toBeTruthy())
    expect(screen.queryByTestId('import-targets-error')).toBeNull()
  })
})
