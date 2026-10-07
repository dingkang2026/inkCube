/** tests/ReportPreview.test.tsx — #8 谓词心跳化 + 终态边沿补拉回归（0.4 §6.3，顺序 4）
 *  背景 bug：生成中打开预览后，content 仅在 mount 拉取一次且父级快照不更新
 *  （ReportsTab previewReport 是点击时快照）→ 生成完成时正文永远停在
 *  「生成中」占位。修复链：父级 applyReports 同步快照 + ReportPreview 在
 *  generating（useActivity('report') entity 级心跳派生）true→false 边沿重拉
 *  content + logs；挂载即拉 logs（启动间隙：心跳 5s 粒度未发现时也先有日志基线）。
 *  另覆盖报告级暂停回归（2.5 §3.1 修订）：生成中暂停 → reports:pauseReport；
 *  paused 态双出口（恢复 = retry(resume) / 重新生成 = 确认后 retry(restart)）。
 *  时序约定（同 tests/usePolling.test.ts）：fake timers + flush 排空 microtask。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const activityState = { items: [] as Array<{ entity_id?: string }> }

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => activityState,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('../src/features/project/tabs/ReportPreview.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, act, fireEvent } from '@testing-library/react'
import { ReportPreview } from '../src/features/project/tabs/ReportPreview'
import type { Report } from '../src/features/project/types'

const baseReport = {
  id: 'r1',
  title: 'T',
  template_id: 'tpl',
  template_name: 'TPL',
  output_format: 'docx',
  created_at: '2026-01-01T00:00:00Z',
}
const genReport: Report = { ...baseReport, status: 'generating', progress: 50 }
const doneReport: Report = { ...baseReport, status: 'done' }

let contentSnapshot: { content: string; registry: object; report: object } | undefined
const invokeMock = vi.fn()

const contentInvokeCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'reports:getReportContent').length
const logsInvokeCount = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'reports:getReportLogs').length

/** 排空 microtask（loadContent/loadLogs 与轮询 tick 均为 async，fake timers 不自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  activityState.items = []
  contentSnapshot = { content: '# Done body', registry: {}, report: { id: 'r1', title: 'T', status: 'done' } }
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'reports:getReportContent') return contentSnapshot
    if (channel === 'reports:getReportLogs') return { logs: [] }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('ReportPreview terminal refetch', () => {
  it('loads content once for a done report and does not refetch on unrelated rerender', async () => {
    const { rerender } = render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    expect(contentInvokeCount()).toBe(1)
    expect(screen.getByText('Done body')).toBeTruthy()

    // 父级刷新（心跳仍无活跃条目的 rerender）不触发边沿
    rerender(<ReportPreview report={{ ...doneReport }} onBack={() => {}} />)
    await flush()
    expect(contentInvokeCount()).toBe(1)
  })

  it('refetches content and logs once when heartbeat flips generating→done and renders the body', async () => {
    // 生成中打开：心跳有本报告条目（entity 级匹配）→ 轮询首跑拉一次 logs
    activityState.items = [{ entity_id: 'r1' }]
    const { rerender } = render(<ReportPreview report={genReport} onBack={() => {}} />)
    await flush()
    // mount 拉取 content 1 次；挂载即拉 logs 1 次 + 轮询首跑 1 次
    expect(contentInvokeCount()).toBe(1)
    expect(logsInvokeCount()).toBe(2)
    // 生成中正文分支渲染（文案带 progress 拼接，用正则匹配）
    expect(screen.queryByText('Done body')).toBeNull()
    expect(screen.getByText(/project\.report\.wizard\.generating/)).toBeTruthy()

    // 生成完成：心跳先行翻假（快照同步前）→ 边沿补拉 content + logs 恰一次
    activityState.items = []
    rerender(<ReportPreview report={doneReport} onBack={() => {}} />)
    expect(contentInvokeCount()).toBe(2)
    expect(logsInvokeCount()).toBe(3)

    // 补拉结果上屏：正文替换「生成中」占位
    await flush()
    expect(screen.queryByText(/project\.report\.wizard\.generating/)).toBeNull()
    expect(screen.getByText('Done body')).toBeTruthy()
  })

  it('does not fire the edge for a report opened after completion', async () => {
    // 完成后打开：心跳无条目 prev=false，无补拉（logs 仅挂载即拉 1 次）
    render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    expect(contentInvokeCount()).toBe(1)
    expect(logsInvokeCount()).toBe(1)
  })

  it('loads a log baseline on mount during the startup gap (heartbeat not yet aware)', async () => {
    // 启动间隙（0.4 §6.3）：触发生成后立即打开预览，心跳 5s 粒度尚未发现该报告
    // （items 空 → 轮询未开），挂载即拉保证日志基线可见
    render(<ReportPreview report={genReport} onBack={() => {}} />)
    await flush()
    expect(logsInvokeCount()).toBe(1)
  })
})

describe('ReportPreview report-level pause (2.5 §3.1 修订)', () => {
  it('shows pause button while generating and invokes reports:pauseReport on click', async () => {
    render(<ReportPreview report={genReport} onBack={() => {}} />)
    await flush()
    expect(screen.getByText('project.report.pauseReport')).toBeTruthy()
    fireEvent.click(screen.getByTitle('project.report.pauseTip'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('reports:pauseReport', { reportId: 'r1' })
  })

  it('renders paused state with resume (retry resume) and restart (retry restart) exits', async () => {
    const pausedReport: Report = { ...baseReport, status: 'paused', progress: 45 }
    render(<ReportPreview report={pausedReport} onBack={() => {}} />)
    await flush()
    expect(screen.getByText(/project\.report\.pausedStatus/)).toBeTruthy()
    // paused 分支短路，正文不渲染 markdown
    expect(screen.queryByText('Done body')).toBeNull()

    fireEvent.click(screen.getByTitle('project.report.resumeTip'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('reports:retryReport', { reportId: 'r1', mode: 'resume' })
    // resume 成功即乐观翻转，paused 面板已消失（详见下条用例）→ restart 全新挂载验证
    cleanup()
    render(<ReportPreview report={{ ...pausedReport }} onBack={() => {}} />)
    await flush()
    fireEvent.click(screen.getByText('project.report.restartReport'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('reports:retryReport', { reportId: 'r1', mode: 'restart' })
  })

  it('optimistically flips to generating on successful resume and resets on report switch', async () => {
    // 乐观本地态（复审建议）：retry 成功后不等心跳/快照同步，立即切生成中视图，
    // 消除窗口期内二次点击被后端 400 拒绝的误导错误
    const pausedReport: Report = { ...baseReport, status: 'paused', progress: 45 }
    const { rerender } = render(<ReportPreview report={pausedReport} onBack={() => {}} />)
    await flush()
    fireEvent.click(screen.getByTitle('project.report.resumeTip'))
    await flush()
    expect(screen.queryByText(/project\.report\.pausedStatus/)).toBeNull()
    expect(screen.getByText(/project\.report\.wizard\.generating/)).toBeTruthy()

    // 切换报告：override 重置，不跨报告泄漏
    const pausedB: Report = { ...baseReport, id: 'r2', title: 'B', status: 'paused', progress: 10 }
    rerender(<ReportPreview report={pausedB} onBack={() => {}} />)
    await flush()
    expect(screen.getByText(/project\.report\.pausedStatus/)).toBeTruthy()
  })
})
