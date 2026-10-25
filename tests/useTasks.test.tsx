/** tests/useTasks.test.tsx — handleRenameTask 错误路径回归测试。
 *
 * 回归背景：rename 失败（后端 404/400 走 handlePythonResponse 抛错通道）此前
 * 无 catch（unhandled rejection）且本地状态照常更新（UI 与 DB 不一致）。
 * 现约定：失败 → toast + 不更新本地任务列表。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useTasks } from '../src/features/task/useTasks'

// 稳定引用 mock：返回新 vi.fn() 会因 useCallback 依赖失效触发无限重渲染
const invokeMock = vi.hoisted(() => vi.fn())
const showToastMock = vi.hoisted(() => vi.fn())

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => showToastMock,
}))

vi.stubGlobal('electronAPI', { invoke: invokeMock })

const baseTask = { id: 't1', title: 'old', status: 'active' }

beforeEach(() => {
  invokeMock.mockReset()
  showToastMock.mockClear()
  invokeMock.mockImplementation((channel: string) => {
    if (channel === 'tasks:listFiltered') {
      return Promise.resolve({ tasks: [baseTask], has_more: false })
    }
    return Promise.resolve({ ok: true })
  })
})

afterEach(cleanup)

async function setup() {
  const { result } = renderHook(() => useTasks())
  await waitFor(() => expect(result.current.loading).toBe(false))
  return result
}

describe('useTasks.handleRenameTask', () => {
  it('updates local task state on success', async () => {
    const result = await setup()

    await act(async () => {
      await result.current.handleRenameTask('t1', 'renamed')
    })

    expect(invokeMock).toHaveBeenCalledWith('tasks:rename', { taskId: 't1', title: 'renamed' })
    expect(result.current.tasks[0].title).toBe('renamed')
    expect(showToastMock).not.toHaveBeenCalled()
  })

  it('shows toast and keeps local state on backend error', async () => {
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'tasks:listFiltered') {
        return Promise.resolve({ tasks: [baseTask], has_more: false })
      }
      // 404 TASK_NOT_FOUND → handlePythonResponse 抛 InkCubeIPCError
      return Promise.reject(new Error('Python backend error (404)'))
    })
    const result = await setup()

    await act(async () => {
      await result.current.handleRenameTask('t1', 'renamed')
    })

    expect(showToastMock).toHaveBeenCalledWith('task.renameFailed', 'error')
    // 失败不更新本地状态，避免 UI 与 DB 不一致
    expect(result.current.tasks[0].title).toBe('old')
  })
})

describe('useTasks.handleDeleteTask（页面遍历 review M2-1 回归）', () => {
  it('shows toast and keeps task on backend error（原实现无 catch 静默）', async () => {
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'tasks:listFiltered') {
        return Promise.resolve({ tasks: [baseTask], has_more: false })
      }
      // 运行中拒删 409 → 抛错通道
      return Promise.reject(new Error('Python backend error (409)'))
    })
    const result = await setup()

    await act(async () => {
      await result.current.handleDeleteTask('t1')
    })

    expect(showToastMock).toHaveBeenCalledWith('task.deleteFailed', 'error')
    // 失败不更新本地列表
    expect(result.current.tasks).toHaveLength(1)
  })

  it('removes task locally on success', async () => {
    const result = await setup()

    await act(async () => {
      await result.current.handleDeleteTask('t1')
    })

    expect(showToastMock).not.toHaveBeenCalled()
    expect(result.current.tasks).toHaveLength(0)
  })
})
