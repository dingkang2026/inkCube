/**
 * LeadPool 单元测试（0.6 §6.1/§6.2/§6.3 V2-E2）：线索池子 Tab。
 * 覆盖：全量拉取契约（limit=0 分组豁免）、四状态分组 + 已立项置灰区、
 * 零线索空态引导（去情报流 + 手动录入）、状态推进（leads:update）、
 * 备注 inline 保存、立项（成功回填 converted / 建项目失败不推进不关模态 /
 * 回填失败提示不重复建项目；五参签名对位 ProjectList.onCreate 2026-10-25）、
 * convertExtract AI 预填三态（有来源即调覆盖字段 / 无来源不发请求 /
 * 失败静默留手填）、升级订阅回调、删除、手动录入、已立项跳项目。
 * 隔离：mock electronAPI.invoke（内存态联装）/ Toast；i18n mock 透传 key。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { LeadPool, type LeadRow } from '../src/features/intel/LeadPool'

vi.mock('react-i18next', () => ({
  // 立项勾选创建订阅用例依赖真实形态的 competitor prompt（含 {企业名} 占位符）：
  // 占位符提取 → applyValues 固化断言都建立在真实模板结构上
  useTranslation: () => ({
    t: (k: string) =>
      k === 'task.tpl.competitor.prompt' ? '跟踪「{企业名}」近一个月的动态' : k,
  }),
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
// leads:convertExtract 预填响应（用例可改写为带 fields 的对象或 rejected
// promise）；默认空对象 = fields 缺省不覆盖字段，等价提取失败静默
let convertExtractRes: Promise<unknown>

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
  convertExtractRes = Promise.resolve({})
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
      case 'leads:convertExtract':
        return convertExtractRes
      default:
        return Promise.resolve({})
    }
  })
})

const baseProps = {
  onConvertProject: vi.fn(),
  onOpenProject: vi.fn(),
  onUpgrade: vi.fn(),
  onGoAdmin: vi.fn(),
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

  it('零线索空态：引导文案 + 去订阅管理回调 + 手动录入开模态', async () => {
    const onGoAdmin = vi.fn()
    render(<LeadPool {...baseProps} onGoAdmin={onGoAdmin} />)
    await waitFor(() => expect(screen.getByTestId('lead-empty')).toBeTruthy())

    fireEvent.click(screen.getByText('intel.lead.emptyGoAdmin'))
    expect(onGoAdmin).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTestId('lead-add-manual'))
    expect(screen.getByTestId('lead-manual-dialog')).toBeTruthy()
  })

  it('run 来源卡显示订阅名；订阅名缺失回落「订阅期次」（intel.tab.feed 退役后收口）', async () => {
    rows = [
      lead({ id: 'lead_1' }),                                                  // source_schedule_name='产业周报'
      lead({ id: 'lead_2', name: '无名来源', source_schedule_name: '' }),
    ]
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(2))
    expect(screen.getByText('产业周报')).toBeTruthy()
    expect(screen.getByText('intel.lead.sourceRun')).toBeTruthy()
  })

  it('首拉失败 → lead-error 错误+重试，不落到空列伪装零线索（静默 catch 排查）', async () => {
    invokeMock.mockImplementation((channel: string) =>
      channel === 'leads:list' ? Promise.reject(new Error('boom')) : Promise.resolve({}))
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getByTestId('lead-error')).toBeTruthy())
    expect(screen.getByText('intel.lead.loadFailed')).toBeTruthy()
    // 不得伪装零线索空态（引导态仅在 loaded 后可达）
    expect(screen.queryByTestId('lead-empty')).toBeNull()
    // 重试恢复：错误态消失、回到真实空态引导
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:list') return Promise.resolve({ leads: [], has_more: false, total: 0 })
      return Promise.resolve({})
    })
    fireEvent.click(screen.getByText('knowledge.retry'))
    await waitFor(() => expect(screen.queryByTestId('lead-error')).toBeNull())
    expect(screen.getByTestId('lead-empty')).toBeTruthy()
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
      expect(onConvertProject).toHaveBeenCalledWith('华润置地', '', 'general', undefined, undefined)
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
      expect(onConvertProject).toHaveBeenCalledWith('华润置地', '', 'investment', undefined, undefined)
    })
  })

  it('有来源报告的线索打开立项模态：即调 leads:convertExtract，AI 预填五字段', async () => {
    rows = [lead({ id: 'lead_1', name: '华润置地' })]
    convertExtractRes = Promise.resolve({
      fields: {
        name: '华润置地有限公司',
        description: '商业地产开发与运营',
        companyName: '华润置地有限公司',
        creditCode: '91440300192307052G',
        suggestedType: 'investment',
      },
    })
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-convert'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:convertExtract', 'lead_1')
    })
    // AI 字段覆盖线索名预填；suggestedType 与前端 ProjectType 值域对位直填
    await waitFor(() => {
      expect((screen.getByTestId('lead-convert-name') as HTMLInputElement).value).toBe('华润置地有限公司')
    })
    expect((screen.getByTestId('lead-convert-desc') as HTMLTextAreaElement).value).toBe('商业地产开发与运营')
    expect((screen.getByTestId('lead-convert-company') as HTMLInputElement).value).toBe('华润置地有限公司')
    expect((screen.getByTestId('lead-convert-credit') as HTMLInputElement).value).toBe('91440300192307052G')
    expect(screen.getByText('intel.lead.convert.typeInvest').getAttribute('aria-checked')).toBe('true')
    // 提取完成后提示行消失（extracting 为进行中专用）
    await waitFor(() => {
      expect(screen.queryByTestId('lead-convert-extracting')).toBeNull()
    })
  })

  it('无来源报告的线索（手动录入）打开模态：不发提取请求，字段保持线索名预填', async () => {
    rows = [lead({
      id: 'lead_m', name: '展会认识企业',
      source_type: 'manual', source_run_id: null, source_schedule_name: '',
    })]
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-convert'))
    expect(screen.getByTestId('lead-convert-dialog')).toBeTruthy()
    // 排空微任务后再断言（有来源才会发出的通道不得被调用）
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(invokeMock).not.toHaveBeenCalledWith('leads:convertExtract', 'lead_m')
    expect(screen.queryByTestId('lead-convert-extracting')).toBeNull()
    expect((screen.getByTestId('lead-convert-name') as HTMLInputElement).value).toBe('展会认识企业')
  })

  it('convertExtract 失败：静默不阻断，模态保留、字段保持线索名预填可手填', async () => {
    rows = [lead({ id: 'lead_1', name: '华润置地' })]
    render(<LeadPool {...baseProps} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    // 用例级 override：convertExtract 拒绝——reject 于 invoke 调用时同步创建，
    // 组件消费链紧随附加，无 unhandled 窗口（模块级预建 rejected promise
    // 会在 await 让出窗口被 Node 判无 handler，实测）
    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'leads:convertExtract') return Promise.reject(new Error('llm timeout'))
      if (channel === 'leads:list') return Promise.resolve({ leads: rows, has_more: false, total: rows.length })
      return Promise.resolve({})
    })

    fireEvent.click(screen.getByTestId('lead-convert'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('leads:convertExtract', 'lead_1')
    })
    // 组件内 .catch 吞掉提取失败；字段留线索名预填，人手动补（智能体优先语义）
    await waitFor(() => {
      expect(screen.queryByTestId('lead-convert-extracting')).toBeNull()
    })
    expect(screen.getByTestId('lead-convert-dialog')).toBeTruthy()
    expect((screen.getByTestId('lead-convert-name') as HTMLInputElement).value).toBe('华润置地')
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

  it('立项勾选创建盯梢订阅：企业名空禁用+hint 解释；勾选后 schedules:create 静默创建（模板固化 prompt + 绑项目去向）', async () => {
    rows = [lead({ id: 'lead_1' })]
    const onConvertProject = vi.fn().mockResolvedValue({ id: 'proj_new' })
    render(<LeadPool {...baseProps} onConvertProject={onConvertProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-convert'))
    // 企业名空 → 勾选禁用，hint 解释禁用原因（免培训 §3.9-②）
    expect((screen.getByTestId('lead-convert-sub') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByTestId('lead-convert-sub-hint').textContent).toBe('schedule.createSub.needCompany')
    fireEvent.change(screen.getByTestId('lead-convert-company'), { target: { value: '华润置地有限公司' } })
    // 企业名非空 → 可勾选，hint 切换为勾选后果说明
    expect((screen.getByTestId('lead-convert-sub') as HTMLInputElement).disabled).toBe(false)
    expect(screen.getByTestId('lead-convert-sub-hint').textContent).toBe('schedule.createSub.hint')
    fireEvent.click(screen.getByTestId('lead-convert-sub'))
    fireEvent.click(screen.getByTestId('lead-convert-confirm'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('schedules:create', {
        // t mock 透传 key：插值形态不展开（真实 i18n 由渲染层消费）
        name: 'schedule.createSub.name',
        // prompt = 模板 applyValues 固化快照（{企业名}=企业全称）
        prompt: '跟踪「华润置地有限公司」近一个月的动态',
        category: 'corporate',   // mon→corporate 静态映射
        intervalType: 'daily',
        intervalValue: '09:00',
        targetType: 'project',   // 去向绑新项目，期次产出自动沉淀
        targetId: 'proj_new',
        scope: null,
        digestScheduleIds: null,
      })
    })
  })

  it('立项不勾选订阅：零 schedules:create（默认不勾 = 持续消耗不可静默代做）', async () => {
    rows = [lead({ id: 'lead_1' })]
    const onConvertProject = vi.fn().mockResolvedValue({ id: 'proj_new' })
    render(<LeadPool {...baseProps} onConvertProject={onConvertProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    fireEvent.click(screen.getByTestId('lead-convert'))
    fireEvent.change(screen.getByTestId('lead-convert-company'), { target: { value: '华润置地有限公司' } })
    fireEvent.click(screen.getByTestId('lead-convert-confirm'))
    await waitFor(() => {
      expect(onConvertProject).toHaveBeenCalledTimes(1)
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(invokeMock).not.toHaveBeenCalledWith('schedules:create', expect.anything())
  })

  it('订阅创建失败：立项主链不受影响（回填+success 照常），仅提示手动补建（部分失败语义）', async () => {
    rows = [lead({ id: 'lead_1' })]
    const onConvertProject = vi.fn().mockResolvedValue({ id: 'proj_new' })
    render(<LeadPool {...baseProps} onConvertProject={onConvertProject} />)
    await waitFor(() => expect(screen.getAllByTestId('lead-card')).toHaveLength(1))

    invokeMock.mockImplementation((channel: string) => {
      if (channel === 'schedules:create') return Promise.reject(new Error('boom'))
      if (channel === 'leads:list') return Promise.resolve({ leads: rows, has_more: false, total: rows.length })
      return Promise.resolve({})
    })

    fireEvent.click(screen.getByTestId('lead-convert'))
    fireEvent.change(screen.getByTestId('lead-convert-company'), { target: { value: '华润置地有限公司' } })
    fireEvent.click(screen.getByTestId('lead-convert-sub'))
    fireEvent.click(screen.getByTestId('lead-convert-confirm'))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith('schedule.createSub.failed', 'error')
    })
    // 主链路不受影响：回填照常 + success toast + 模态正常关闭
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith('intel.lead.convert.success', 'success')
    })
    expect(invokeMock).toHaveBeenCalledWith('leads:update', {
      id: 'lead_1', projectId: 'proj_new', status: 'converted',
    })
    expect(screen.queryByTestId('lead-convert-dialog')).toBeNull()
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
