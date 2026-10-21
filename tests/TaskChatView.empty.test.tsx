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
/** Electron 32+ File.path 回归：拖放路径经 preload getPathForFile 换取 */
const getPathMock = vi.fn((f: File) => `C:/drop/${f.name}`)

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
  // mockReset 清除上一用例残留实现（clearAllMocks 不清 implementation/once 队列），
  // 再重设默认：组件挂载时 slash 命令加载会先 invoke 一次，返回无 uploaded 字段
  invokeMock.mockReset()
  invokeMock.mockResolvedValue({ skills: [] })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock, getPathForFile: getPathMock }
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

  it('上传钮零任务态可用：点击 → 建任务 → pickAndUpload 挂新任务（0.5 §4.4 上传即建任务）', async () => {
    const ensure = vi.fn().mockResolvedValue('new-task')
    const onUploaded = vi.fn()
    // 按 channel 分流（挂载时 skills 加载会先 invoke，once 排队会被其消费）
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'tasks:pickAndUpload' ? { uploaded: [{ name: 'doc.pdf', size: 10 }] } : { skills: [] })
    render(<TaskChatView {...zeroBase} onEnsureTask={ensure} onFilesUploaded={onUploaded} />)
    const uploadBtn = screen.getByTitle('task.uploadDropHint') as HTMLButtonElement
    expect(uploadBtn.disabled).toBe(false)
    fireEvent.click(uploadBtn)
    await act(async () => { await Promise.resolve() })
    expect(ensure).toHaveBeenCalledTimes(1)
    expect(invokeMock).toHaveBeenCalledWith('tasks:pickAndUpload', { taskId: 'new-task' })
    // 回调显式传新任务 id（消费方不能依赖外部 task state，否则陈旧闭包漏刷）
    expect(onUploaded).toHaveBeenCalledWith('new-task')
  })

  it('上传钮零任务态建任务失败（句柄 null）：不弹文件选择', async () => {
    const ensure = vi.fn().mockResolvedValue(null)
    render(<TaskChatView {...zeroBase} onEnsureTask={ensure} />)
    fireEvent.click(screen.getByTitle('task.uploadDropHint'))
    await act(async () => { await Promise.resolve() })
    expect(invokeMock).not.toHaveBeenCalledWith('tasks:pickAndUpload', expect.anything())
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

describe('TaskChatView 拖放上传（Electron 32+ File.path 移除回归）', () => {
  const zeroBase = { ...baseProps, taskId: null, hasActiveTask: false }

  /** drop 事件绑在组件根 div（styles.window）：以根 div 为 target 派发；
   *  jsdom DragEvent.dataTransfer 为只读原型 getter，用 defineProperty 注入模拟对象 */
  const dropFiles = (container: HTMLElement, files: File[]) => {
    const event = new Event('drop', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'dataTransfer', { value: { files, types: ['Files'] } })
    fireEvent(container.firstElementChild as HTMLElement, event)
  }

  it('有任务态拖放：getPathForFile 换取真实路径 → tasks:uploadFiles → 回调带 taskId', async () => {
    const onUploaded = vi.fn()
    // 按 channel 分流（挂载时 skills 加载会先 invoke，once 排队会被其消费）
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'tasks:uploadFiles' ? { uploaded: [{ name: 'a.txt', size: 1 }] } : { skills: [] })
    const { container } = render(<TaskChatView {...baseProps} onFilesUploaded={onUploaded} />)
    dropFiles(container, [new File(['x'], 'a.txt')])
    await act(async () => { await Promise.resolve() })
    expect(invokeMock).toHaveBeenCalledWith('tasks:uploadFiles', { taskId: 't1', filePaths: ['C:/drop/a.txt'] })
    expect(onUploaded).toHaveBeenCalledWith('t1')
  })

  it('混合拖放（真实+虚拟文件）：上传真实文件并提示跳过数量', async () => {
    // 局部覆盖：仅 a.txt 有真实路径，web-image.png 为无路径虚拟文件
    ;(window as unknown as { electronAPI: unknown }).electronAPI = {
      invoke: invokeMock,
      getPathForFile: (f: File) => (f.name === 'a.txt' ? 'C:/drop/a.txt' : ''),
    }
    const { container } = render(<TaskChatView {...baseProps} />)
    dropFiles(container, [new File(['x'], 'a.txt'), new File(['y'], 'web-image.png')])
    await act(async () => { await Promise.resolve() })
    expect(invokeMock).toHaveBeenCalledWith('tasks:uploadFiles', { taskId: 't1', filePaths: ['C:/drop/a.txt'] })
    expect(toastMock).toHaveBeenCalledWith('task.dropPartialSkipped')
  })

  it('拖入虚拟文件（路径解析为空）：toast 提示且不触发上传', async () => {
    // 局部覆盖 electronAPI（beforeEach 逐用例重建，不污染共享 getPathMock 实现）
    ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock, getPathForFile: () => '' }
    const { container } = render(<TaskChatView {...baseProps} />)
    dropFiles(container, [new File(['x'], 'web-image.png')])
    await act(async () => { await Promise.resolve() })
    expect(toastMock).toHaveBeenCalledWith('task.dropUnsupported')
    expect(invokeMock).not.toHaveBeenCalledWith('tasks:uploadFiles', expect.anything())
  })

  it('零任务态拖放：先建任务 → 文件挂新任务上传', async () => {
    const ensure = vi.fn().mockResolvedValue('new-task')
    const { container } = render(<TaskChatView {...zeroBase} onEnsureTask={ensure} />)
    dropFiles(container, [new File(['x'], 'b.txt')])
    await act(async () => { await Promise.resolve() })
    expect(ensure).toHaveBeenCalledTimes(1)
    expect(invokeMock).toHaveBeenCalledWith('tasks:uploadFiles', { taskId: 'new-task', filePaths: ['C:/drop/b.txt'] })
  })

  it('零任务态建任务失败（句柄 null）：不上传', async () => {
    const ensure = vi.fn().mockResolvedValue(null)
    const { container } = render(<TaskChatView {...zeroBase} onEnsureTask={ensure} />)
    dropFiles(container, [new File(['x'], 'c.txt')])
    await act(async () => { await Promise.resolve() })
    expect(invokeMock).not.toHaveBeenCalledWith('tasks:uploadFiles', expect.anything())
  })
})

describe('TaskChatView 粘贴截图上传（剪贴板字节直传）', () => {
  const zeroBase = { ...baseProps, taskId: null, hasActiveTask: false }

  /** jsdom 无 ClipboardEvent 构造器：用 Event('paste') + defineProperty 注入
   *  clipboardData（与拖放用例 dropFiles 同一模式）；item 模拟 DataTransferItem
   *  接口（type + getAsFile()）；paste 事件绑在 textarea 上 */
  const pasteClipboard = (textarea: HTMLElement, items: Array<{ type: string; file: File | null }>) => {
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', {
      value: { items: items.map((it) => ({ type: it.type, getAsFile: () => it.file })) },
    })
    fireEvent(textarea, event)
    return event
  }

  /** FileReader load 是宏任务，await Promise.resolve() 只 flush 微任务不够等；
   *  不等完会让 invoke 泄漏到下一用例窗口污染共享 mock。轮询到 invoke 被调为止 */
  const drainMacrotasks = () =>
    act(async () => { await new Promise((r) => setTimeout(r, 10)) })

  it('有任务态粘贴图片：拦截默认行为 → base64 → tasks:uploadClipboardImage → 回调带 taskId', async () => {
    const onUploaded = vi.fn()
    // 按 channel 分流（挂载时 skills 加载会先 invoke，once 排队会被其消费）
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'tasks:uploadClipboardImage'
        ? { uploaded: [{ name: 'clipboard-x.png', size: 4 }] }
        : { skills: [] })
    render(<TaskChatView {...baseProps} onFilesUploaded={onUploaded} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder')
    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', { type: 'image/png' })
    const evt = pasteClipboard(textarea, [{ type: 'image/png', file: png }])
    expect(evt.defaultPrevented).toBe(true)
    await vi.waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('tasks:uploadClipboardImage', {
        taskId: 't1',
        dataBase64: expect.any(String),
      })
    })
    expect(onUploaded).toHaveBeenCalledWith('t1')
  })

  it('纯文本粘贴：不拦截、不触发上传通道', async () => {
    render(<TaskChatView {...baseProps} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder')
    const evt = pasteClipboard(textarea, [{ type: 'text/plain', file: null }])
    await drainMacrotasks()
    expect(evt.defaultPrevented).toBe(false)
    expect(invokeMock).not.toHaveBeenCalledWith('tasks:uploadClipboardImage', expect.anything())
  })

  it('零任务态粘贴：先建任务 → 截图挂新任务（上传即建任务）', async () => {
    const ensure = vi.fn().mockResolvedValue('new-task')
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'tasks:uploadClipboardImage'
        ? { uploaded: [{ name: 'clipboard-x.png', size: 4 }] }
        : { skills: [] })
    render(<TaskChatView {...zeroBase} onEnsureTask={ensure} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder')
    pasteClipboard(textarea, [{ type: 'image/png', file: new File(['x'], 's.png', { type: 'image/png' }) }])
    await vi.waitFor(() => {
      expect(ensure).toHaveBeenCalledTimes(1)
      expect(invokeMock).toHaveBeenCalledWith('tasks:uploadClipboardImage', {
        taskId: 'new-task',
        dataBase64: expect.any(String),
      })
    })
  })

  it('零任务态建任务失败（句柄 null）：不上传', async () => {
    const ensure = vi.fn().mockResolvedValue(null)
    render(<TaskChatView {...zeroBase} onEnsureTask={ensure} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder')
    pasteClipboard(textarea, [{ type: 'image/png', file: new File(['x'], 's.png', { type: 'image/png' }) }])
    await drainMacrotasks()
    expect(ensure).toHaveBeenCalledTimes(1)
    expect(invokeMock).not.toHaveBeenCalledWith('tasks:uploadClipboardImage', expect.anything())
  })

  it('上传失败：toast 提示且文案带错误详情', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'tasks:uploadClipboardImage') throw new Error('python down')
      return { skills: [] }
    })
    render(<TaskChatView {...baseProps} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder')
    pasteClipboard(textarea, [{ type: 'image/png', file: new File(['x'], 's.png', { type: 'image/png' }) }])
    await vi.waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith('task.pasteUploadFailed: python down')
    })
  })

  it('拒绝 resolved 体（{error}）：同样转 toast 不静默（护栏/端点 200-错误体路径）', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'tasks:uploadClipboardImage'
        ? { error: 'clipboard image too large (base64 cap 40MB)', code: 'FILE_TOO_LARGE' }
        : { skills: [] })
    render(<TaskChatView {...baseProps} />)
    const textarea = screen.getByPlaceholderText('task.inputPlaceholder')
    pasteClipboard(textarea, [{ type: 'image/png', file: new File(['x'], 's.png', { type: 'image/png' }) }])
    await vi.waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        'task.pasteUploadFailed: clipboard image too large (base64 cap 40MB)',
      )
    })
  })
})
