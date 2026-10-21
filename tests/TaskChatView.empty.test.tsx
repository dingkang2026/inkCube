import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { TaskChatView } from '../src/features/task/TaskChatView'

/** prompt 键返回带占位符文本（其余返回键名）；companyBasic 用两处同名占位符覆盖
 *  「填一次全量替换」形态，industryEvents 用多行 mock 覆盖 skill 版模板形态 */
const tMock = (k: string) => {
  if (!k.endsWith('.prompt')) return k
  if (k === 'task.tpl.companyBasic.prompt') return '摸清「{企业名}」底数，报告「{企业名}」'
  return k === 'task.tpl.industryEvents.prompt' ? '多行模板\n第二行{产业}' : `P[${k}]{企业名}`
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}))

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

/** 入口 guard 直接放行（聚焦发送占位符守卫本身） */
vi.mock('../src/features/readiness/useReadiness', () => ({
  useFeatureGuard: () => async (_feature: string, run: () => void) => { run() },
}))

vi.mock('../src/features/task/TaskMessageList', () => ({
  TaskMessageList: () => null,
}))

const invokeMock = vi.fn().mockResolvedValue({ skills: [] })

const baseProps = {
  taskId: 't1',
  hasActiveTask: true,
  messages: [] as unknown[],
  isGenerating: false,
  streamContent: '',
  streamSteps: [],
  onSendMessage: vi.fn(),
  onStopGenerate: vi.fn(),
}

beforeEach(() => {
  vi.clearAllMocks()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('TaskChatView 空态模板化 (0.5 E1 T7)', () => {
  it('greeting 含时段问候与「我是墨方」，hint 行存在', () => {
    render(<TaskChatView {...baseProps} />)
    const greet = screen.getByText(/task\.greet(Morning|Afternoon|Evening)/)
    expect(greet.textContent).toContain('task.greetLead')
    expect(screen.getByText('task.hint')).toBeTruthy()
  })

  it('无 task.example 残留渲染', () => {
    render(<TaskChatView {...baseProps} />)
    expect(screen.queryByText(/task\.example/)).toBeNull()
  })

  it('空态渲染模板面板（10 卡）；有消息后不渲染（0.5 §7.2）', () => {
    const { rerender } = render(<TaskChatView {...baseProps} />)
    expect(screen.getAllByText(/task\.tpl\..+\.title/)).toHaveLength(10)

    rerender(
      <TaskChatView
        {...baseProps}
        messages={[{ role: 'user', content: 'hi' } as unknown as never]}
      />,
    )
    expect(screen.queryByText('task.cat.all')).toBeNull()
  })

  it('点模板卡：输入框预填 prompt，占位符表单出现（变量按名去重）', () => {
    render(<TaskChatView {...baseProps} />)
    fireEvent.click(screen.getByText('task.tpl.companyBasic.title'))
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    expect(textarea.value).toBe(tMock('task.tpl.companyBasic.prompt'))
    // {企业名} 出现 2 次但表单只有一个字段（aria-label 唯一）
    expect(screen.getAllByLabelText('企业名')).toHaveLength(1)
  })

  it('填表单一次：同名占位符全文替换；填齐后发送不再拦截', () => {
    render(<TaskChatView {...baseProps} />)
    fireEvent.click(screen.getByText('task.tpl.companyBasic.title'))
    const field = screen.getByLabelText('企业名') as HTMLInputElement
    fireEvent.change(field, { target: { value: '华润' } })
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    expect(textarea.value).toBe('摸清「华润」底数，报告「华润」')
    expect(textarea.value).not.toContain('{企业名}')

    // 已填齐：占位符守卫不触发，直接发送
    fireEvent.click(screen.getByTitle('task.send'))
    expect(toastMock).not.toHaveBeenCalledWith('task.placeholderWarn')
    expect(baseProps.onSendMessage).toHaveBeenCalledWith('摸清「华润」底数，报告「华润」')
  })

  it('手动编辑文本框 → 接管，表单隐藏；未填占位符由发送守卫兜底', () => {
    render(<TaskChatView {...baseProps} />)
    fireEvent.click(screen.getByText('task.tpl.companyBasic.title'))
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    // 手动改写且仍含未填占位符 → 接管（表单隐藏），发送守卫照常拦截
    fireEvent.change(textarea, { target: { value: '我改过的「{企业名}」内容' } })
    expect(screen.queryByLabelText('企业名')).toBeNull()

    fireEvent.click(screen.getByTitle('task.send'))
    expect(toastMock).toHaveBeenCalledWith('task.placeholderWarn')
    expect(baseProps.onSendMessage).not.toHaveBeenCalled()
  })

  it('发送守卫：含未填占位符首次发送 → toast 警示不发送；再次点击放行', () => {
    render(<TaskChatView {...baseProps} />)
    fireEvent.click(screen.getByText('task.tpl.companyBasic.title'))
    const sendBtn = screen.getByTitle('task.send')

    fireEvent.click(sendBtn)
    expect(toastMock).toHaveBeenCalledWith('task.placeholderWarn')
    expect(baseProps.onSendMessage).not.toHaveBeenCalled()

    fireEvent.click(sendBtn)
    expect(baseProps.onSendMessage).toHaveBeenCalledTimes(1)
    expect(baseProps.onSendMessage).toHaveBeenCalledWith(tMock('task.tpl.companyBasic.prompt'))
  })

  it('重新点模板卡重置守卫：再次预填后首次发送仍被拦截', () => {
    render(<TaskChatView {...baseProps} />)
    const sendBtn = screen.getByTitle('task.send')

    fireEvent.click(screen.getByText('task.tpl.companyBasic.title'))
    fireEvent.click(sendBtn)
    expect(baseProps.onSendMessage).not.toHaveBeenCalled()

    // 重选模板：warnedRef 重置 → 首次发送再次拦截
    fireEvent.click(screen.getByText('task.tpl.companyBasic.title'))
    fireEvent.click(sendBtn)
    expect(baseProps.onSendMessage).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledTimes(2)
  })
})

describe('TaskChatView 多行输入发送语义（skill 版模板 0.5 §7.3）', () => {
  it('单行裸 Enter 发送（现状习惯回归）', () => {
    render(<TaskChatView {...baseProps} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '单行任务' } })
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(baseProps.onSendMessage).toHaveBeenCalledWith('单行任务')
  })

  it('多行裸 Enter 不发送（换行走 textarea 默认行为）', () => {
    render(<TaskChatView {...baseProps} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '第一行\n第二行' } })
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(baseProps.onSendMessage).not.toHaveBeenCalled()
  })

  it('多行 Ctrl+Enter 发送', () => {
    render(<TaskChatView {...baseProps} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '第一行\n第二行' } })
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true })
    expect(baseProps.onSendMessage).toHaveBeenCalledTimes(1)
  })

  it('预填多行模板 → toast 提示发送方式；占位符守卫不受影响', () => {
    render(<TaskChatView {...baseProps} />)
    fireEvent.click(screen.getByText('task.tpl.industryEvents.title'))
    expect(toastMock).toHaveBeenCalledWith('task.templateMultilineHint')
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    expect(textarea.value).toContain('\n')

    // 多行模板含未填占位符：首次发送仍被占位符守卫拦截
    fireEvent.click(screen.getByTitle('task.send'))
    expect(toastMock).toHaveBeenCalledWith('task.placeholderWarn')
    expect(baseProps.onSendMessage).not.toHaveBeenCalled()
  })
})

describe('TaskChatView 零任务态（0.5 §4.4 发送即建任务）', () => {
  const zeroBase = { ...baseProps, taskId: null, hasActiveTask: false }

  it('输入框不禁用：可输入、发送受理后清空', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined)
    render(<TaskChatView {...zeroBase} onSendMessage={onSend} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    expect(textarea.disabled).toBe(false)
    fireEvent.change(textarea, { target: { value: '帮我收集企业信息' } })
    fireEvent.click(screen.getByTitle('task.send'))
    await act(async () => { await Promise.resolve() })
    expect(onSend).toHaveBeenCalledWith('帮我收集企业信息')
    expect(textarea.value).toBe('')
  })

  it('上传钮仍禁用（文件必须挂任务工作区）', () => {
    render(<TaskChatView {...zeroBase} />)
    const uploadBtn = screen.getByTitle('task.uploadFiles (或拖放文件到对话区)') as HTMLButtonElement
    expect(uploadBtn.disabled).toBe(true)
  })

  it('发送链路失败（建任务失败 throw）：输入保留、发送锁复位可直接重试', async () => {
    const onSend = vi.fn()
      .mockRejectedValueOnce(new Error('create failed'))
      .mockResolvedValueOnce(undefined)
    render(<TaskChatView {...zeroBase} onSendMessage={onSend} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '我的任务' } })
    const sendBtn = screen.getByTitle('task.send')

    fireEvent.click(sendBtn)
    await act(async () => { await Promise.resolve() })
    expect(onSend).toHaveBeenCalledTimes(1)
    expect(textarea.value).toBe('我的任务')  // 失败不清输入

    fireEvent.click(sendBtn)
    await act(async () => { await Promise.resolve() })
    expect(onSend).toHaveBeenCalledTimes(2)
    expect(textarea.value).toBe('')  // 重试成功后清空
  })
})
