/** tests/TaskLogViewer.test.tsx — 执行日志查看器截断提示回归测试。
 *  回归背景：此前固定取最后 500 行但头部只显示总行数（如「1500 行」），
 *  用户会误以为看到全量；修复为取满后端上限 2000 行 + 截断时显示
 *  「显示最后 N / 共 M 行」。
 *  覆盖：截断态提示（showing < total）、未截断态行数直显、取数上限 lines=2000。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

vi.mock('../src/features/task/TaskLogViewer.module.css', () => ({
  default: { meta: 'meta' },
}))

// t 直出 key；带插值对象时附带参数，便于断言传参正确
// （注意区分 t(key, '默认文案') 的字符串第二参——不得误格式化）
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown> | string) =>
      opts && typeof opts === 'object'
        ? `${key}:${opts.shown}/${opts.total}`
        : key,
  }),
}))

vi.mock('../src/hooks/usePolling', () => ({
  usePolling: () => {},
}))

import { TaskLogViewer } from '../src/features/task/TaskLogViewer'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  ;(window as any).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as any).electronAPI
})

function renderViewer(visible = true) {
  return render(
    <TaskLogViewer taskId="t1" visible={visible} onClose={() => {}} />,
  )
}

describe('TaskLogViewer 截断提示', () => {
  it('日志超上限（showing < total）→ 显示截断提示而非总行数', async () => {
    invokeMock.mockResolvedValue({
      lines: Array.from({ length: 2000 }, (_, i) => `line-${i}`),
      total_lines: 3000,
      showing: 2000,
    })
    renderViewer()

    // 截断文案带插值：显示最后 2000 / 共 3000 行
    expect(await screen.findByText('task.execLogTruncated:2000/3000')).toBeTruthy()
    // 不渲染 3000 行，只渲染取回的 2000 行
    expect(screen.getByText('line-1999')).toBeTruthy()
    expect(screen.queryByText('line-2000')).toBeNull()
  })

  it('日志未超上限（showing = total）→ 保持原「N 行」展示', async () => {
    invokeMock.mockResolvedValue({
      lines: ['a', 'b'],
      total_lines: 2,
      showing: 2,
    })
    renderViewer()

    expect(await screen.findByText('2 task.execLogLines')).toBeTruthy()
  })

  it('取数上限提满后端 lines 上限 2000（此前 500，日志长时静默丢前段）', async () => {
    invokeMock.mockResolvedValue({ lines: ['x'], total_lines: 1, showing: 1 })
    renderViewer()

    await screen.findByText('1 task.execLogLines')
    expect(invokeMock).toHaveBeenCalledWith(
      'tasks:getLogs',
      expect.objectContaining({ lines: 2000 }),
    )
  })
})
