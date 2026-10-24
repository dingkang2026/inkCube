/**
 * LeadPool 单元测试（0.6 §6.1/§6.2/§6.3 V2-E2）：线索池子 Tab。
 * 覆盖：全量拉取契约（limit=0 分组豁免）、四状态分组 + 已立项置灰区、
 * 零线索空态引导（去情报流 + 手动录入）、状态推进（leads:update）、
 * 备注 inline 保存、立项（成功回填 converted / 建项目失败不推进不关模态 /
 * 回填失败提示不重复建项目）、升级订阅回调、删除、手动录入、已立项跳项目。
 * 隔离：mock electronAPI.invoke（内存态联装）/ Toast；i18n mock 透传 key。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { LeadPool, type LeadRow } from '../src/features/intel/LeadPool'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

const toastMock = vi.fn()
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

// 删除二次确认层（CodeReview 建议 2026-10-24 采纳）：默认确认，取消用例内临时改写
const confirmMock = vi.fn(() => Promise.resolve(true))
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))

const invokeMock = vi.fn()

function lead(overrides: Partial<LeadRow> = {}): LeadRow {
  return {
    id: 'lead_1',
    name: '华润置地',
    source_type: 'run',
    source_run_id: 'run_1',
    source_schedule_name: '产业周报',
    status: 'new',
    note: '',
    project_id: null,
    created_at: '2026-10-24T08:00:00Z',
    updated_at: '2026-10-24T08:00:00Z',
    ...overrides,
  }
}

// 内存态联装：写操作真实变更 rows，重拉即见新状态（等价后端行为）
let rows: LeadRow[] = []
beforeEach(async () => {
  rows = []
  toastMock.mockReset()
  // mockClear 保留默认 resolve(true) 实现（mockReset 会清掉导致后续用例取消删除）
  confirmMock.mockClear()
  ;(window as unknown as { electronAPI: { invoke: typeof invokeMock } }).electronAPI = {
    invoke: invokeMock,
  }
  // 排空前序用例未完成的 async 尾巴（load/saveNote 的微任务链会漂移到
  // 下一用例窗口并污染 mock.calls 计数，合跑实测）
  await new Promise((resolve) => setTimeout(resolve, 0))
  invokeMock.mockReset()
  invokeMock.mockImplementation((channel: string, payload?: unknown) => {
    switch (channel) {
      case 'leads:list':
        return Promise.resolve({ leads: rows, has_more: false, total: rows.length })
      case 'leads:create': {
        const p = payload as { name: string; sourceType: string; note: string }
        const row = lead({
          id: `lead_${rows.length + 1}`, name: p.name,
          source_type: p.sourceType === 'manual' ? 'manual' : 'run',
          source_run_id: null, source_schedule_name: '', note: p.note ?? '',
        })
        rows = [...rows, row]
        return Promise.resolve({ lead: row })
      }
      case 'leads:update': {
        const p = payload as { id: string; status?: string; note?: string; projectId?: string }
        rows = rows.map((r) => (r.id === p.id
          ? { ...r, status: (p.status ?? r.status) as LeadRow['status'], note: p.note ?? r.note, project_id: p.projectId ?? r.project_id }
          : r))
        return Promise.resolve({ lead: rows.find((r) => r.id === p.id) })
      }
      case 'leads:delete':
        rows = rows.filter((r) => r.id !== payload)
        return Promise.resolve({ ok: true })
      default:
        return Promise.resolve({})
    }
  })
})

const baseProps = {
  onConvertProject: vi.fn(),
  onOpenProject: vi.fn(),
  onUpgrade: vi.fn(),
  onGoFeed: vi.fn(),
}

describe('LeadPool', () => {
  it('挂载以 limit=0 全量拉取，按四状态分组渲染，已立项卡进置灰区', async () => {
    rows = [
      lead({ id: 'lead_1', status: 'new' }),
      lead({ id: 'lead_2', name: '中金', status: 'contacting' }),
      lead({ id: 'lead_3', name: '张江园区', status: 'evaluating' }),
      lead({ id: 'lead_4', name: '合肥产投', status: 'hold' }),
      lead({ id: 'lead_5', name: '已立项线索', status: 'converted', project_id: 'proj_9' }),
    ]
    render(<LeadPool {...baseProps} />)

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:list', { limit: 0 })
    })
    await waitFor(() => {
      expect(screen.getAllByTestId('lead-card')).toHaveLength(4)
    })
    expect(screen.getAllByTestId('lead-card-converted')).toHaveLength(1)
    for (const st of ['new', 'contacting', 'evaluating', 'hold']) {
      expect(screen.getByTestId(`lead-col-${st}`)).toBeTruthy()
    }
  })

  it('零线索空态：引导文案 + 去情报流回调 + 手动录入开模态', async () => {
    const onGoFeed = vi.fn()
    render(<LeadPool {...baseProps} onGoFeed={onGoFeed} />)
    await waitFor(() => expect(screen.getByTestId('lead-empty')).toBeTruthy())

    fireEvent.click(screen.getByText('intel.lead.emptyGoFeed'))
    expect(onGoFeed).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTestId('lead-add-manual'))
    expect(screen.getByTestId('lead-manual-dialog')).toBeTruthy()
  })

  it('点击状态 pill 以 leads:update 推进并重拉（卡移入目标分组）', async () => {
    rows = [lead({ id: 'lead_1', status: 'new' })]
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    // 列标题与卡 pill 同文案，须限定卡内 pill（lead-pill）查询
    const pill = screen
      .getAllByTestId('lead-pill')
      .find((p) => p.textContent === 'intel.lead.status.contacting')
    expect(pill).toBeTruthy()
    fireEvent.click(pill!)
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:update', { id: 'lead_1', status: 'contacting' })
    })
    // 重拉后 new 列空、contacting 列有卡
    await waitFor(() => {
      expect(screen.getByTestId('lead-col-contacting').querySelectorAll('[data-testid="lead-card"]').length).toBe(1)
      expect(screen.getByTestId('lead-col-new').querySelectorAll('[data-testid="lead-card"]').length).toBe(0)
    })
  })

  it('备注 inline 编辑：blur 时值有变才 PATCH leads:update note', async () => {
    rows = [lead({ id: 'lead_1', note: '' })]
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-note'))
    const input = screen.getByTestId('lead-note-input')
    fireEvent.change(input, { target: { value: '已电话，约下周面谈' } })
    fireEvent.blur(input)
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:update', { id: 'lead_1', note: '已电话，约下周面谈' })
    })
    // 重拉后展示新备注
    await waitFor(() => {
      expect(screen.getByTestId('lead-note').textContent).toContain('已电话，约下周面谈')
    })
  })

  it('立项成功：onConvertProject → 回填 projectId+converted → toast → 卡进已立项区', async () => {
    rows = [lead({ id: 'lead_1', name: '华润置地' })]
    const onConvertProject = vi.fn().mockResolvedValue({ id: 'proj_new' })
    render(<LeadPool {...baseProps} onConvertProject={onConvertProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-convert'))
    expect(screen.getByTestId('lead-convert-dialog')).toBeTruthy()
    // 模态名称预填线索名
    expect((screen.getByTestId('lead-convert-name') as HTMLInputElement).value).toBe('华润置地')
    fireEvent.click(screen.getByTestId('lead-convert-confirm'))
    // onConvertProject 为异步 mock：排空微任务链再断言（fireEvent 不等待 async handler）
    await waitFor(() => {
      expect(onConvertProject).toHaveBeenCalledWith('华润置地', 'general')
    })
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:update', {
        id: 'lead_1', projectId: 'proj_new', status: 'converted',
      })
    })
    expect(toastMock).toHaveBeenCalledWith('intel.lead.convert.success', 'success')
    await waitFor(() => {
      expect(screen.getAllByTestId('lead-card-converted')).toHaveLength(1)
    })
  })

  it('立项选类型：点击投资项目 pill 后 onConvertProject 收到 recruitment/investment 枚举', async () => {
    rows = [lead({ id: 'lead_1', name: '华润置地' })]
    const onConvertProject = vi.fn().mockResolvedValue({ id: 'proj_new' })
    render(<LeadPool {...baseProps} onConvertProject={onConvertProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-convert'))
    fireEvent.click(screen.getByText('intel.lead.convert.typeInvest'))
    fireEvent.click(screen.getByTestId('lead-convert-confirm'))
    await waitFor(() => {
      expect(onConvertProject).toHaveBeenCalledWith('华润置地', 'investment')
    })
  })

  it('建项目失败（返回 undefined）：状态不推进、模态保留可重试、不调 leads:update', async () => {
    rows = [lead({ id: 'lead_1' })]
    const onConvertProject = vi.fn().mockResolvedValue(undefined)
    render(<LeadPool {...baseProps} onConvertProject={onConvertProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-convert'))
    fireEvent.click(screen.getByTestId('lead-convert-confirm'))
    await waitFor(() => {
      expect(onConvertProject).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(screen.getByTestId('lead-convert-dialog')).toBeTruthy()
    })
    expect(invokeMock).not.toHaveBeenCalledWith('leads:update', expect.objectContaining({ id: 'lead_1' }))
  })

  it('回填失败（leads:update 拒绝）：提示且不重复建项目，模态关闭', async () => {
    rows = [lead({ id: 'lead_1' })]
    const onConvertProject = vi.fn().mockResolvedValue({ id: 'proj_new' })
    render(<LeadPool {...baseProps} onConvertProject={onConvertProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    // 模拟本地回填失败：改写 update 分支
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:update') return Promise.reject(new Error('db locked'))
      if (channel === 'leads:list') return Promise.resolve({ leads: rows, has_more: false, total: rows.length })
      return Promise.resolve({})
    })

    fireEvent.click(screen.getByTestId('lead-convert'))
    fireEvent.click(screen.getByTestId('lead-convert-confirm'))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith('intel.lead.convert.patchFailed', 'error')
    })
    // 模态已关（防用户重试再建一个重复项目）
    expect(screen.queryByTestId('lead-convert-dialog')).toBeNull()
    expect(onConvertProject).toHaveBeenCalledTimes(1)
  })

  it('升级订阅按钮 → onUpgrade(lead) 回调', async () => {
    rows = [lead({ id: 'lead_1' })]
    const onUpgrade = vi.fn()
    render(<LeadPool {...baseProps} onUpgrade={onUpgrade} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-upgrade'))
    expect(onUpgrade).toHaveBeenCalledWith(rows[0])
  })

  it('删除线索：确认层通过后 leads:delete（裸 id 参数）+ 重拉', async () => {
    rows = [lead({ id: 'lead_1' }), lead({ id: 'lead_2', name: '中金' })]
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(2))

    const delBtns = screen.getAllByTestId('lead-delete')
    fireEvent.click(delBtns[0])
    // 确认层带线索名（本文件 t mock 透传 key，参数忽略）
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
        title: 'intel.lead.deleteLabel',
        message: 'intel.lead.confirmDeleteLead',
      }))
    })
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:delete', 'lead_1')
    })
    await waitFor(() => {
      expect(screen.getAllByTestId('lead-card')).toHaveLength(1)
    })
  })

  it('删除线索取消：不发 leads:delete，卡保留', async () => {
    rows = [lead({ id: 'lead_1' })]
    confirmMock.mockResolvedValueOnce(false)
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getAllByTestId('lead-delete')[0])
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledTimes(1)
    })
    // 排空异步微任务后断言未发删除请求
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(invokeMock).not.toHaveBeenCalledWith('leads:delete', 'lead_1')
    expect(screen.getAllByTestId('lead-card')).toHaveLength(1)
  })

  it('手动录入：空名禁用确认；确认后 leads:create（manual 来源）+ 重拉 + 关模态', async () => {
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('lead-empty')).toBeTruthy())

    fireEvent.click(screen.getByTestId('lead-add-manual'))
    const nameInput = screen.getByTestId('lead-manual-name') as HTMLInputElement
    const confirmBtn = screen.getByTestId('lead-manual-confirm') as HTMLButtonElement
    expect(nameInput.value).toBe('')
    expect(confirmBtn.disabled).toBe(true)

    fireEvent.change(nameInput, { target: { value: '合肥产投' } })
    fireEvent.change(screen.getByTestId('lead-manual-note'), { target: { value: '展会认识的园区方' } })
    expect(confirmBtn.disabled).toBe(false)
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:create', {
        name: '合肥产投', sourceType: 'manual', note: '展会认识的园区方',
      })
    })
    expect(toastMock).toHaveBeenCalledWith('intel.lead.manualAdded', 'success')
    await waitFor(() => {
      expect(screen.queryByTestId('lead-manual-dialog')).toBeNull()
    })
    // 重拉后卡出现在新线索列
    await waitFor(() => {
      expect(screen.getByTestId('lead-col-new').textContent).toContain('合肥产投')
    })
  })

  it('已立项卡「查看项目」→ onOpenProject(project_id)', async () => {
    rows = [lead({ id: 'lead_5', status: 'converted', project_id: 'proj_9' })]
    const onOpenProject = vi.fn()
    render(<LeadPool {...baseProps} onOpenProject={onOpenProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card-converted')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-open-project'))
    expect(onOpenProject).toHaveBeenCalledWith('proj_9')
  })
})
