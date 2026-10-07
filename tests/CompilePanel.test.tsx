/** tests/CompilePanel.test.tsx — 编译明细内嵌面板回归（2.6 §10 修订）
 *  覆盖：无 task 不渲染、编译中进度与逐文件列表、错误详情、终态文案、
 *  摘要去重（同 file_id 保留最后一条）、点击头部/收起按钮触发 onCollapse、
 *  无模态遮罩（面板形态，不自动弹出由 WikiTab 强弹删除保证）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? key }),
}))

vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { CompilePanel } from '../src/features/project/tabs/CompilePanel'

const baseTask = {
  id: 't1',
  status: 'ingesting',
  pages_total: 3,
  pages_completed: 1,
  pages_failed: 0,
  error_message: null,
}

afterEach(() => cleanup())

describe('CompilePanel', () => {
  it('renders nothing without a task', () => {
    const { container } = render(
      <CompilePanel task={null} ingestSummaries={[]} onCollapse={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders progress and per-file list while ingesting', () => {
    const { container } = render(
      <CompilePanel
        task={baseTask}
        ingestSummaries={[
          { file_id: 'f1', filename: 'a.pdf', pages_created: 2 },
        ]}
        onCollapse={vi.fn()}
      />,
    )
    // 内嵌面板容器（非模态 overlay）
    expect(container.querySelector('.compilePanel')).toBeTruthy()
    expect(container.querySelector('.compilePanelOverlay')).toBeNull()
    // 进度文本与逐文件条目（t mock 无 defaultValue 时返回键名，与实现一致）
    expect(container.textContent).toContain('project.wiki.compileProgress')
    expect(container.textContent).toContain('a.pdf')
    expect(container.textContent).toContain('+2')
  })

  it('shows error detail for failed files', () => {
    const { container } = render(
      <CompilePanel
        task={baseTask}
        ingestSummaries={[
          { file_id: 'f1', filename: 'bad.pdf', error: 'API key 无效' },
        ]}
        onCollapse={vi.fn()}
      />,
    )
    expect(container.textContent).toContain('bad.pdf')
    expect(container.textContent).toContain('API key 无效')
    expect(container.textContent).toContain('1 个失败')
  })

  it('deduplicates summaries by file_id keeping the last entry', () => {
    const { container } = render(
      <CompilePanel
        task={baseTask}
        ingestSummaries={[
          { file_id: 'f1', filename: 'old.pdf', error: 'old error' },
          { file_id: 'f1', filename: 'new.pdf', pages_created: 1 },
        ]}
        onCollapse={vi.fn()}
      />,
    )
    expect(container.textContent).toContain('new.pdf')
    expect(container.textContent).not.toContain('old.pdf')
    expect(container.textContent).not.toContain('old error')
  })

  it('shows terminal copy for completed and stopped states', () => {
    const done = render(
      <CompilePanel
        task={{ ...baseTask, status: 'completed' }}
        ingestSummaries={[]}
        onCollapse={vi.fn()}
      />,
    )
    expect(done.container.textContent).toContain('project.wiki.compileAllDone')
    cleanup()

    const stopped = render(
      <CompilePanel
        task={{ ...baseTask, status: 'stopped' }}
        ingestSummaries={[]}
        onCollapse={vi.fn()}
      />,
    )
    expect(stopped.container.textContent).toContain('project.wiki.compileIngestStopped')
  })

  it('fires onCollapse from header and chevron click', () => {
    const onCollapse = vi.fn()
    const { container } = render(
      <CompilePanel task={baseTask} ingestSummaries={[]} onCollapse={onCollapse} />,
    )
    fireEvent.click(container.querySelector('.compilePanelCollapseIcon')!)
    expect(onCollapse).toHaveBeenCalledTimes(1)
    // 头部整行也可收起（stopPropagation 避免双触发）
    fireEvent.click(container.querySelector('.compilePanelHeader')!)
    expect(onCollapse).toHaveBeenCalledTimes(2)
  })

  it('shows task-level error message', () => {
    const { container } = render(
      <CompilePanel
        task={{ ...baseTask, status: 'failed', error_message: 'LLM 未配置' }}
        ingestSummaries={[]}
        onCollapse={vi.fn()}
      />,
    )
    expect(container.textContent).toContain('LLM 未配置')
  })
})
