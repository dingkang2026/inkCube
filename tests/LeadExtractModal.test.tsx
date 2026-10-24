/**
 * LeadExtractModal 单元测试（0.6 §3.3/§6.3-10 V2-E2）：阅读页「提取线索」
 * LLM 候选勾选入池模态。
 * 覆盖：并行拉取契约（leads:extract runId + leads:list limit=0 已在池判定）、
 * 候选默认全选、取消勾选只入池勾选项（note=信号摘要）、本期已入池条目禁选
 * 标「已在池」（零重复入池）、跨期同名软提示不硬拦、入池成功 toast + 可跳
 * 线索池、extract 失败错误态 + 重试、入池中途失败剔除已成功项模态保留
 * （不重跑 extract 避免重复 LLM 调用）、空候选占位、关闭。
 * 隔离：mock electronAPI.invoke / Toast；i18n mock 透传 key。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { LeadExtractModal } from '../src/features/intel/LeadExtractModal'

vi.mock('react-i18next', () => ({
  // 带参数时追加大括号值（confirmAddN 计数断言依赖：key 本身不含 {{count}} 占位，
  // 透传式 mock 无法插值，故以「key(值)」形态保留参数）
  useTranslation: () => ({
    t: (k: string, opts?: Record<string, unknown>) =>
      opts ? `${k}(${Object.values(opts).join('|')})` : k,
  }),
}))

const toastMock = vi.fn()
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

const invokeMock = vi.fn()

const CANDIDATES = [
  { name: '华润置地', signal: '启动新园区谈判', evidence: '本期报告首段' },
  { name: '中金公司', signal: '设立产业基金', evidence: '本期报告第二段' },
  { name: '张江高科', signal: '出新招商政策', evidence: '政策节' },
]

beforeEach(() => {
  toastMock.mockReset()
  ;(window as unknown as { electronAPI: { invoke: typeof invokeMock } }).electronAPI = {
    invoke: invokeMock,
  }
  invokeMock.mockReset()
  invokeMock.mockImplementation((channel: string) => {
    if (channel === 'leads:extract') {
      return Promise.resolve({ candidates: CANDIDATES })
    }
    if (channel === 'leads:list') {
      return Promise.resolve({ leads: [], has_more: false, total: 0 })
    }
    if (channel === 'leads:create') {
      return Promise.resolve({ lead: { id: 'lead_new' } })
    }
    return Promise.resolve({})
  })
})

const renderModal = (onClose = vi.fn()) => {
  render(<LeadExtractModal runId="run_1" scheduleName="产业周报" onClose={onClose} />)
  return onClose
}

describe('LeadExtractModal', () => {
  it('并行拉取：leads:extract(runId) + leads:list(limit=0)；候选默认全选', async () => {
    renderModal()
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:extract', 'run_1')
      expect(invokeMock).toHaveBeenCalledWith('leads:list', { limit: 0 })
    })
    // 候选名 + 信号摘要 + 原文依据渲染
    expect(await screen.findByText('华润置地')).toBeTruthy()
    expect(screen.getByText('启动新园区谈判')).toBeTruthy()
    expect(screen.getByText('本期报告首段')).toBeTruthy()
    // 默认全选：3 个候选 checkbox 全 checked
    const boxes = screen.getAllByTestId('lead-candidate-check') as HTMLInputElement[]
    expect(boxes).toHaveLength(3)
    expect(boxes.every((b) => b.checked)).toBe(true)
    // 确认按钮计数 = 3
    expect(screen.getByTestId('lead-extract-confirm').textContent).toContain('3')
  })

  it('取消勾选后入池：只 create 勾选项（note=信号摘要，run 来源快照）', async () => {
    renderModal()
    const boxes = await screen.findAllByTestId('lead-candidate-check') as HTMLInputElement[]
    fireEvent.click(boxes[0])  // 取消勾选华润置地
    fireEvent.click(screen.getByTestId('lead-extract-confirm'))
    await waitFor(() => {
      const creates = invokeMock.mock.calls.filter((c) => c[0] === 'leads:create')
      expect(creates).toHaveLength(2)
      expect(creates[0][1]).toEqual({
        name: '中金公司', sourceType: 'run', sourceRunId: 'run_1',
        sourceScheduleName: '产业周报', note: '设立产业基金',
      })
    })
    expect(toastMock).toHaveBeenCalledWith('intel.lead.extracted(2)', 'success', expect.objectContaining({
      action: expect.objectContaining({ label: 'intel.lead.goLeads' }),
    }))
  })

  it('本期已入池候选：禁选 + 「已在池」标记，确认不重复 create', async () => {
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:extract') return Promise.resolve({ candidates: CANDIDATES })
      if (channel === 'leads:list') {
        // 华润置地本期已入池
        return Promise.resolve({
          leads: [{ id: 'lead_1', name: '华润置地', source_run_id: 'run_1', source_schedule_name: '产业周报' }],
          has_more: false, total: 1,
        })
      }
      return Promise.resolve({})
    })
    renderModal()
    const boxes = await screen.findAllByTestId('lead-candidate-check') as HTMLInputElement[]
    expect(boxes[0].disabled).toBe(true)
    expect(screen.getByText('intel.lead.inPool')).toBeTruthy()
    // 禁选项不计入确认计数（默认全选只含未入池 2 条）
    expect(screen.getByTestId('lead-extract-confirm').textContent).toContain('2')
    fireEvent.click(screen.getByTestId('lead-extract-confirm'))
    await waitFor(() => {
      const creates = invokeMock.mock.calls.filter((c) => c[0] === 'leads:create')
      expect(creates).toHaveLength(2)
      expect(creates.every((c) => (c[1] as { name: string }).name !== '华润置地')).toBe(true)
    })
  })

  it('跨期同名软提示：文案出现但不禁选', async () => {
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:extract') return Promise.resolve({ candidates: CANDIDATES })
      if (channel === 'leads:list') {
        return Promise.resolve({
          leads: [{ id: 'lead_9', name: '中金公司', source_run_id: 'run_old', source_schedule_name: '旧订阅' }],
          has_more: false, total: 1,
        })
      }
      return Promise.resolve({})
    })
    renderModal()
    expect(await screen.findByText('intel.lead.dupSoft')).toBeTruthy()
    const boxes = await screen.findAllByTestId('lead-candidate-check') as HTMLInputElement[]
    expect(boxes[1].disabled).toBe(false)
    expect(boxes[1].checked).toBe(true)
  })

  it('extract 失败 → 错误态 + 重试；重试成功恢复候选', async () => {
    let fail = true
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:extract') {
        return fail ? Promise.reject(new Error('llm down')) : Promise.resolve({ candidates: CANDIDATES })
      }
      if (channel === 'leads:list') return Promise.resolve({ leads: [], has_more: false, total: 0 })
      return Promise.resolve({})
    })
    renderModal()
    expect(await screen.findByText('intel.lead.extractFailed')).toBeTruthy()
    // 先翻转再点击：click 同步触发 load()，Promise.all 参数求值时 fail 必须已为 false
    fail = false
    fireEvent.click(screen.getByTestId('lead-extract-retry'))
    expect(await screen.findByText('华润置地')).toBeTruthy()
    expect(screen.queryByText('intel.lead.extractFailed')).toBeNull()
  })

  it('入池中途失败：已成功项剔除、模态保留，重试只补剩余（不重跑 extract）', async () => {
    let createCount = 0
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:extract') return Promise.resolve({ candidates: CANDIDATES })
      if (channel === 'leads:list') {
        // 重拉池返回已成功的（按已收到的 create 调用判定）
        const done: string[] = []
        for (const c of invokeMock.mock.calls) {
          if (c[0] === 'leads:create' && (createCount >= 0)) done.push((c[1] as { name: string }).name)
        }
        // 仅第 1 条（华润置地）成功
        const ok = done.filter((n) => n === '华润置地')
        return Promise.resolve({
          leads: ok.map((n, i) => ({ id: `lead_${i}`, name: n, source_run_id: 'run_1', source_schedule_name: '产业周报' })),
          has_more: false, total: ok.length,
        })
      }
      if (channel === 'leads:create') {
        createCount++
        if (createCount === 2) return Promise.reject(new Error('db locked'))  // 第 2 条失败
        return Promise.resolve({ lead: { id: 'lead_new' } })
      }
      return Promise.resolve({})
    })
    renderModal()
    await screen.findByText('华润置地')
    fireEvent.click(screen.getByTestId('lead-extract-confirm'))

    // 第 2 条失败 → toast 报错、模态保留
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith('intel.lead.opFailed', 'error')
    })
    expect(screen.getByTestId('lead-extract-modal')).toBeTruthy()
    // extract 只调了 1 次（重试补池走 leads:list，不重复 LLM 调用）
    const extractCalls = invokeMock.mock.calls.filter((c) => c[0] === 'leads:extract')
    expect(extractCalls).toHaveLength(1)
    // 确认按钮剔除已成功项后剩 2 条
    await waitFor(() => {
      expect(screen.getByTestId('lead-extract-confirm').textContent).toContain('2')
    })

    // 重试：选中集剔除已成功的华润置地后剩 2 条（中金公司首轮失败未入池仍在集
    // 中 → 重试；张江高科），华润置地不再 create → 总 2+2=4 次
    fireEvent.click(screen.getByTestId('lead-extract-confirm'))
    await waitFor(() => {
      const creates = invokeMock.mock.calls.filter((c) => c[0] === 'leads:create')
      expect(creates).toHaveLength(4)
      expect(creates[3][1]).toMatchObject({ name: '张江高科' })
      expect(creates.slice(1).every((c) => (c[1] as { name: string }).name !== '华润置地')).toBe(true)
    })
    expect(toastMock).toHaveBeenCalledWith('intel.lead.extracted(2)', 'success', expect.anything())
  })

  it('空候选 → 占位文案，确认按钮不可用', async () => {
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:extract') return Promise.resolve({ candidates: [] })
      if (channel === 'leads:list') return Promise.resolve({ leads: [], has_more: false, total: 0 })
      return Promise.resolve({})
    })
    renderModal()
    expect(await screen.findByText('intel.lead.noCandidates')).toBeTruthy()
    expect((screen.getByTestId('lead-extract-confirm') as HTMLButtonElement).disabled).toBe(true)
  })

  it('关闭按钮 → onClose', async () => {
    const onClose = renderModal()
    await screen.findByText('华润置地')
    fireEvent.click(screen.getByTestId('lead-extract-close'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
