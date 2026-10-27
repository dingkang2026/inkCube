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

describe('ReportPreview panel defaults (§1.3)', () => {
  it('expands logs by default while generating and collapses on terminal edge', async () => {
    // 生成中打开：日志面板默认展开（空日志渲染占位），进度条就位
    activityState.items = [{ entity_id: 'r1' }]
    const { rerender } = render(<ReportPreview report={genReport} onBack={() => {}} />)
    await flush()
    expect(screen.getByRole('progressbar')).toBeTruthy()
    expect(screen.getByText('project.report.preview.noLogs')).toBeTruthy()

    // 终态边沿：日志自动收起 + 正文渲染（「生成完内容预览出来，其它收起来」）
    activityState.items = []
    rerender(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    expect(screen.queryByText('project.report.preview.noLogs')).toBeNull()
    expect(screen.getByText('Done body')).toBeTruthy()
  })

  it('keeps logs collapsed for a report opened after completion', async () => {
    render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    expect(screen.queryByText('project.report.preview.noLogs')).toBeNull()
  })

  it('renders sources toggle collapsed by default when registry has entries', async () => {
    contentSnapshot = {
      content: '# Done body',
      registry: { entries: [{ ref_number: 1, source_title: '某研报', content: 'x' }] },
      report: { id: 'r1', title: 'T', status: 'done' },
    }
    render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    // toggle 可见（计数上屏），但来源列表默认收起
    expect(screen.getByText(/project\.report\.preview\.sources/)).toBeTruthy()
    expect(screen.queryByText('某研报')).toBeNull()
  })

  it('renders logs in backend order (newest-first, no auto-scroll)', async () => {
    // 后端已按最新在上返回（新上旧下，2026-10-27 用户裁决，自动滚底已撤销）；
    // UI 层透传渲染不得反转，最新进展首屏可见
    activityState.items = [{ entity_id: 'r1' }]
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'reports:getReportContent') return contentSnapshot
      if (channel === 'reports:getReportLogs') {
        return { logs: [
          { id: 'l1', message: '第一条：最新进展', level: 'info', created_at: '2026-01-01T00:00:02' },
          { id: 'l2', message: '第二条：开始生成', level: 'info', created_at: '2026-01-01T00:00:01' },
        ] }
      }
      return {}
    })
    render(<ReportPreview report={genReport} onBack={() => {}} />)
    await flush()
    const first = screen.getByText('第一条：最新进展')
    const second = screen.getByText('第二条：开始生成')
    expect(
      first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
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

describe('ReportPreview error recovery (2.5 §3.8.3)', () => {
  it('renders error state with resume (retry resume) and restart (retry restart) exits', async () => {
    // 崩溃恢复：error 保留 checkpoint，详情页与 paused 同构提供恢复/重新生成，
    // 兑现中断日志「可重新生成续跑」承诺（此前详情页无任何按钮，须退回列表）
    const errorReport: Report = {
      ...baseReport,
      status: 'error',
      error_message: '程序异常中断，请重新生成',
    }
    render(<ReportPreview report={errorReport} onBack={() => {}} />)
    await flush()
    expect(screen.getByText('程序异常中断，请重新生成')).toBeTruthy()
    // error 分支短路，正文不渲染 markdown
    expect(screen.queryByText('Done body')).toBeNull()

    fireEvent.click(screen.getByTitle('project.report.resumeTip'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('reports:retryReport', { reportId: 'r1', mode: 'resume' })

    // resume 成功即乐观翻转 → restart 全新挂载验证
    cleanup()
    render(<ReportPreview report={{ ...errorReport }} onBack={() => {}} />)
    await flush()
    fireEvent.click(screen.getByText('project.report.restartReport'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('reports:retryReport', { reportId: 'r1', mode: 'restart' })
  })
})

describe('ReportPreview find-in-report（块级定位，useInFileFind + FindBar 复用）', () => {
  it('counts leaf-block hits and wraps around via Enter', async () => {
    // h1 与两个 p 均为叶子块，其中两块含 alpha → 计数 2；末尾 Enter 回绕到首个命中
    contentSnapshot = {
      content: '# Alpha 标题\n\n第一段提到 alpha。\n\n无命中段落。',
      registry: {},
      report: { id: 'r1', title: 'T', status: 'done' },
    }
    render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    fireEvent.keyDown(window, { key: 'f', ctrlKey: true })
    const input = screen.getByPlaceholderText('find.placeholder')
    fireEvent.change(input, { target: { value: 'alpha' } })
    expect(screen.getByText('1/2')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('2/2')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('1/2')).toBeTruthy()
  })

  it('hides the find entry while generating (canFind gate)', async () => {
    // 生成中无正文可定位：toolbar 无入口 + window 级 Ctrl+F 不接管
    activityState.items = [{ entity_id: 'r1' }]
    render(<ReportPreview report={genReport} onBack={() => {}} />)
    await flush()
    expect(screen.queryByTitle('project.report.preview.findInReport')).toBeNull()
    fireEvent.keyDown(window, { key: 'f', ctrlKey: true })
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
  })

  it('opens from the toolbar button and closes with Escape on a done report', async () => {
    render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    fireEvent.click(screen.getByTitle('project.report.preview.findInReport'))
    expect(screen.getByPlaceholderText('find.placeholder')).toBeTruthy()
    fireEvent.keyDown(screen.getByPlaceholderText('find.placeholder'), { key: 'Escape' })
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
  })
})

describe('ReportPreview citation superscript（视觉尾注，2026-10-27）', () => {
  it('renders inline [n] citations as sup while keeping links, nesting and sources list plain', async () => {
    // 转换契约（与导出端 renderer.apply_endnote_superscript 同规则）：
    // 正文 [25] → <sup>[25]</sup>；[1](url) 链接、[[9]] 嵌套、行首 [n]（
    // 参考来源列表条目自身）均排除
    contentSnapshot = {
      content: '增速领先的预测[25]，另见[1](https://example.com)与[[9]]页。\n\n[25] 《来源A》: 摘要',
      registry: {},
      report: { id: 'r1', title: 'T', status: 'done' },
    }
    const { container } = render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    const sups = container.querySelectorAll('sup')
    expect(sups.length).toBe(1)
    expect(sups[0].textContent).toBe('[25]')
    // 链接形态保持原生链接渲染，未被上标转换吞掉
    expect(container.querySelector('a[href="https://example.com"]')).toBeTruthy()
    // 参考来源列表条目保持行首纯文本（不上标）
    expect(screen.getByText(/\[25\] 《来源A》/)).toBeTruthy()
  })

  it('keeps citation paragraphs as separate <p> leaf blocks', async () => {
    // review 修复回归（2026-10-27）：含 sup 的段落行经 InlineHtmlLine 渲染后
    // 必须各自为 p 叶子块（FIND_BLOCK_SELECTOR 含 p）——此前裸 fragment 使
    // 引用段落粘连成段且对 Ctrl+F 完全不可见
    contentSnapshot = {
      content: '第一段引用[5]。\n\n第二段引用[6]。',
      registry: {},
      report: { id: 'r1', title: 'T', status: 'done' },
    }
    const { container } = render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    const ps = container.querySelectorAll('.markdown p')
    expect(ps.length).toBe(2)
    expect(ps[0].querySelector('sup')?.textContent).toBe('[5]')
    expect(ps[1].querySelector('sup')?.textContent).toBe('[6]')
  })

  it('matches citation paragraph text via in-file find (leaf block hit)', async () => {
    // 端到端：引用段落文本可被文档内查找命中（p 叶子块）
    contentSnapshot = {
      content: '增速领先的预测[25]，口径二[26]。',
      registry: {},
      report: { id: 'r1', title: 'T', status: 'done' },
    }
    render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    fireEvent.keyDown(window, { key: 'f', ctrlKey: true })
    const input = screen.getByPlaceholderText('find.placeholder')
    fireEvent.change(input, { target: { value: '增速领先' } })
    expect(screen.getByText('1/1')).toBeTruthy()
  })

  it('keeps bold pairing across an embedded citation (no literal asterisks)', async () => {
    // Minor 2 回归：**核心[5]观点** 转换后 sup 不得切开加粗配对（占位符协议）
    contentSnapshot = {
      content: '**核心[5]观点**陈述。',
      registry: {},
      report: { id: 'r1', title: 'T', status: 'done' },
    }
    const { container } = render(<ReportPreview report={doneReport} onBack={() => {}} />)
    await flush()
    const strong = container.querySelector('.markdown strong')
    expect(strong).toBeTruthy()
    expect(strong!.textContent).toBe('核心[5]观点')
    expect(strong!.querySelector('sup')).toBeTruthy()
    expect(container.textContent).not.toContain('**')
  })
})
