/** tests/TaskMessageList.test.tsx — browser-fetch 登录墙横幅（2.8 §8.3）
 *  覆盖：历史消息（action+observation 顺序配对）中 needs_login observation
 *  渲染登录横幅（iter 卡片默认收起，展开后可见）；点击「去登录」先幂等登记
 *  （登记失败不阻塞）再导航内嵌 BrowserPanel（V2 §8.3）；
 *  正常 observation / 未完成步骤不渲染横幅。
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import type { TaskMessage, StreamStep } from '../src/types'

// loginRequired 查表返回带 {{domain}} 的真实文案，验证插值；其余返回 key。
// i18n.language 需存在：MessageBubble 用它格式化时间戳
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'zh-CN' },
    t: (key: string, opts?: Record<string, unknown>) => {
      let out = key === 'task.loginRequired' ? '抓取 {{domain}} 需要登录' : key
      for (const [k, v] of Object.entries(opts || {})) {
        if (typeof v === 'string' || typeof v === 'number') out = out.replaceAll(`{{${k}}}`, String(v))
      }
      return out
    },
  }),
}))

vi.mock('../src/features/task/TaskMessageList.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/components/Markdown', () => ({
  Markdown: () => null,
}))

// V2 §8.3：去登录 = 导航内嵌 BrowserPanel（不再走 weblogin:open 独立窗口）
const navSpy = vi.hoisted(() => vi.fn())
vi.mock('../src/features/browser/browserPanelStore', () => ({
  browserPanelStore: {
    navigate: navSpy,
    showCompact: vi.fn(),
    expand: vi.fn(),
    collapse: vi.fn(),
    hide: vi.fn(),
  },
}))

import { TaskMessageList, buildStreamGroups } from '../src/features/task/TaskMessageList'

const invokeMock = vi.fn()

// jsdom 无 scrollIntoView（主组件自动滚动 useEffect）
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

let seq = 0
const msg = (role: TaskMessage['role'], content: string, metadata?: Record<string, unknown>): TaskMessage => ({
  id: `m${seq++}`,
  task_id: 't1',
  role,
  content,
  metadata,
  created_at: '',
})

const loginOutput = JSON.stringify({
  title: 'Example Domain',
  url: 'https://example.com/',
  markdown_chars: 400, // 稀疏内容（< LOGIN_TEXT_SPARSE=500）
  needs_login: true,
  domain: 'example.com',
  reason: 'sparse_content',
})

const renderList = (messages: TaskMessage[], extra?: Partial<Parameters<typeof TaskMessageList>[0]>) =>
  render(
    <TaskMessageList
      messages={messages}
      streamContent=""
      streamSteps={[]}
      isGenerating={false}
      {...extra}
    />,
  )

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async () => ({}))
  navSpy.mockClear()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('TaskMessageList login-wall banner (2.8 §8.3)', () => {
  it('renders the banner with domain after expanding the iteration card', () => {
    const { container } = renderList([
      msg('user', '抓取 example.com'),
      msg('action', '', { tool_name: 'skill_runner', tool_input: { skill: 'browser-fetch' } }),
      msg('observation', '', { tool_output: loginOutput }),
    ])

    // iter 卡片默认收起，横幅不可见
    expect(container.querySelector('.loginBanner')).toBeNull()
    fireEvent.click(screen.getByText('Iter 1'))

    expect(container.querySelector('.loginBanner')).toBeTruthy()
    expect(screen.getByText('抓取 example.com 需要登录')).toBeTruthy()
    expect(screen.getByText('task.loginNow')).toBeTruthy()
  })

  it('registers the domain best-effort, then navigates the embedded panel', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'weblogin:add') throw new Error('backend down') // 登记失败不阻塞导航
      return {}
    })
    renderList([
      msg('user', '抓取 example.com'),
      msg('action', '', { tool_name: 'skill_runner', tool_input: { skill: 'browser-fetch' } }),
      msg('observation', '', { tool_output: loginOutput }),
    ])
    fireEvent.click(screen.getByText('Iter 1'))
    fireEvent.click(screen.getByText('task.loginNow'))

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('weblogin:add', { domain: 'example.com' })
      expect(navSpy).toHaveBeenCalledWith('https://example.com')
    })
    // V2：不再打开独立登录窗口
    expect(invokeMock).not.toHaveBeenCalledWith('weblogin:open', expect.anything())
  })

  it('renders no banner for a normal observation', () => {
    const { container } = renderList([
      msg('user', '抓取 example.com'),
      msg('action', '', { tool_name: 'skill_runner', tool_input: { skill: 'browser-fetch' } }),
      msg('observation', '', {
        tool_output: JSON.stringify({ title: 'Example', url: 'https://example.com/', markdown_chars: 3000 }),
      }),
    ])
    fireEvent.click(screen.getByText('Iter 1'))
    expect(container.querySelector('.loginBanner')).toBeNull()
  })

  it('renders no banner while the step is still running (no output yet)', () => {
    const { container } = render(
      <TaskMessageList
        messages={[]}
        streamContent=""
        streamSteps={[{ iteration: 1, tool: 'skill_runner', input: { skill: 'browser-fetch' } }]}
        streamIteration={1}
        isGenerating={true}
      />,
    )
    expect(container.querySelector('.loginBanner')).toBeNull()
  })
})

describe('TaskMessageList env-setup notes (2.8 §5.5 step_note)', () => {
  it('buildStreamGroups separates note steps from tool actions', () => {
    const steps: StreamStep[] = [
      { type: 'note', text: '⏳ 首次使用 browser-fetch，正在准备运行环境...', iteration: 0 },
      { type: 'action', tool: 'browser-fetch', input: { command: 'https://x' }, iteration: 0, output: '{"ok":true}' },
      { type: 'note', text: '✅ browser-fetch 运行环境就绪（38s）', iteration: 0 },
    ]
    const groups = buildStreamGroups(steps, '', '', 0, new Map(), new Map())
    expect(groups).toHaveLength(1)
    // notes 顺序保留（含 action 前后穿插），不混入 actions
    expect(groups[0].notes).toEqual([
      '⏳ 首次使用 browser-fetch，正在准备运行环境...',
      '✅ browser-fetch 运行环境就绪（38s）',
    ])
    expect(groups[0].actions).toHaveLength(1)
    // note 不驱动状态：唯一 action 已有 output；isCurrent 且有 actions → running（流式期）
    expect(groups[0].status).toBe('running')
  })

  it('note-only steps still produce an iteration card (first visible feedback)', () => {
    // 首条 note 到达、action 未出时即有可见卡片（环境准备中的第一反馈）
    const groups = buildStreamGroups(
      [{ type: 'note', text: '⏳ 准备中', iteration: 2 }],
      '', '', 2, new Map(), new Map(),
    )
    expect(groups).toHaveLength(1)
    expect(groups[0].iteration).toBe(2)
    expect(groups[0].notes).toEqual(['⏳ 准备中'])
    expect(groups[0].actions).toHaveLength(0)
  })

  it('renders note rows; pending note shows spinner, terminal note stops it', () => {
    const { container } = render(
      <TaskMessageList
        messages={[]}
        streamContent=""
        streamSteps={[
          { type: 'note', text: '⏳ 首次使用 browser-fetch，正在准备运行环境...', iteration: 0 },
          { type: 'note', text: '✅ browser-fetch 运行环境就绪（38s）', iteration: 0 },
          { type: 'action', tool: 'browser-fetch', input: { command: 'https://x' }, iteration: 0 },
        ]}
        streamIteration={0}
        isGenerating={true}
      />,
    )
    // isGenerating 时当前迭代挂载即自动展开（autoExpand），无需点击
    expect(screen.getByText(/运行环境就绪/)).toBeTruthy()
    // 最后一条 note 是终态 ✅ → noteRow 内无 spinner（header 的 running spinner 不在此断言范围）
    const noteRows = container.querySelectorAll('.noteRow')
    expect(noteRows).toHaveLength(2)
    expect(noteRows[0].querySelector('.spinner')).toBeNull()
    expect(noteRows[1].querySelector('.spinner')).toBeNull()
  })

  it('pending ⏳ as the last note renders a spinner in the note row', () => {
    const { container } = render(
      <TaskMessageList
        messages={[]}
        streamContent=""
        streamSteps={[
          { type: 'note', text: '⏳ 首次使用 browser-fetch，正在准备运行环境...', iteration: 0 },
        ]}
        streamIteration={0}
        isGenerating={true}
      />,
    )
    // 同上：当前迭代自动展开
    const noteRow = container.querySelector('.noteRow')
    expect(noteRow).toBeTruthy()
    expect(noteRow!.querySelector('.spinner')).toBeTruthy()
  })
})

describe('TaskMessageList 自动滚动（思考流跟随 + 上翻暂停）', () => {
  /** 覆盖 jsdom 布局度量（getter 可 defineProperty 覆写）*/
  const setMetrics = (el: HTMLElement, scrollHeight: number, scrollTop: number, clientHeight: number) => {
    Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
    Object.defineProperty(el, 'scrollTop', { value: scrollTop, configurable: true })
    Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
  }
  /** 惰性读取：beforeAll 才赋值 mock，describe 收集期直接读是 undefined */
  const scrollIntoViewMock = () => Element.prototype.scrollIntoView as unknown as ReturnType<typeof vi.fn>

  beforeEach(() => {
    scrollIntoViewMock().mockClear()
  })

  it('思考流式（streamReasoning 变化）触发贴底：根因一回归', () => {
    const { rerender } = render(
      <TaskMessageList
        messages={[]} streamContent="" streamSteps={[]}
        streamIteration={1} streamReasoning="思考中" isGenerating={true}
      />,
    )
    scrollIntoViewMock().mockClear()
    rerender(
      <TaskMessageList
        messages={[]} streamContent="" streamSteps={[]}
        streamIteration={1} streamReasoning="思考中...继续推理" isGenerating={true}
      />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalled()
  })

  it('生成中 behavior=auto（高频 chunk 下 smooth 动画追不上：根因二回归）', () => {
    const { rerender } = render(
      <TaskMessageList messages={[]} streamContent="" streamSteps={[]} isGenerating={true} />,
    )
    scrollIntoViewMock().mockClear()
    rerender(
      <TaskMessageList messages={[]} streamContent="chunk" streamSteps={[]} isGenerating={true} />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalledWith({ behavior: 'auto' })
  })

  it('非生成态新消息保留 smooth', () => {
    const msgs = [msg('user', 'hi')]
    const { rerender } = render(
      <TaskMessageList messages={msgs} streamContent="" streamSteps={[]} isGenerating={false} />,
    )
    scrollIntoViewMock().mockClear()
    rerender(
      <TaskMessageList messages={[...msgs, msg('assistant', '回复')]} streamContent="" streamSteps={[]} isGenerating={false} />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalledWith({ behavior: 'smooth' })
  })

  it('上翻离底暂停跟随；滚回底部恢复（距底 <30px 判定）', () => {
    const { container, rerender } = render(
      <TaskMessageList messages={[]} streamContent="" streamSteps={[]} isGenerating={true} />,
    )
    const list = container.firstElementChild as HTMLElement
    // 用户上翻：距底 500px → 暂停
    setMetrics(list, 2000, 500, 1000)
    fireEvent.scroll(list)
    scrollIntoViewMock().mockClear()
    rerender(
      <TaskMessageList messages={[]} streamContent="增量" streamSteps={[]} isGenerating={true} />,
    )
    expect(scrollIntoViewMock()).not.toHaveBeenCalled()
    // 滚回底部附近（距底 20px < 30）→ 恢复跟随
    setMetrics(list, 2000, 980, 1000)
    fireEvent.scroll(list)
    rerender(
      <TaskMessageList messages={[]} streamContent="增量2" streamSteps={[]} isGenerating={true} />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalled()
  })

  it('切换任务重置为跟随态（不沿用上一任务的暂停）', () => {
    const { container, rerender } = render(
      <TaskMessageList taskId="t1" messages={[]} streamContent="" streamSteps={[]} isGenerating={true} />,
    )
    const list = container.firstElementChild as HTMLElement
    setMetrics(list, 2000, 500, 1000)
    fireEvent.scroll(list) // 离底暂停
    scrollIntoViewMock().mockClear()
    rerender(
      <TaskMessageList taskId="t2" messages={[]} streamContent="新任务流" streamSteps={[]} isGenerating={true} />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalled()
  })

  it('上翻暂停期间用户发送（forceScrollSignal 递增）→ 强制贴底且后续流式恢复跟随', () => {
    const { container, rerender } = render(
      <TaskMessageList messages={[]} streamContent="" streamSteps={[]} isGenerating={true} forceScrollSignal={0} />,
    )
    const list = container.firstElementChild as HTMLElement
    setMetrics(list, 2000, 500, 1000)
    fireEvent.scroll(list) // 上翻暂停
    scrollIntoViewMock().mockClear()
    // 流式增量不拉回（暂停生效）
    rerender(
      <TaskMessageList messages={[]} streamContent="流式增量" streamSteps={[]} isGenerating={true} forceScrollSignal={0} />,
    )
    expect(scrollIntoViewMock()).not.toHaveBeenCalled()
    // 用户发送受理（信号递增）→ 强制贴底，且重置为跟随态
    rerender(
      <TaskMessageList
        messages={[msg('user', '补充一条')]}
        streamContent=""
        streamSteps={[]}
        isGenerating={true}
        forceScrollSignal={1}
      />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalledWith({ behavior: 'smooth' })
    // 后续流式不再被门控（跟随已恢复）
    rerender(
      <TaskMessageList
        messages={[msg('user', '补充一条')]}
        streamContent="流式继续"
        streamSteps={[]}
        isGenerating={true}
        forceScrollSignal={1}
      />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalledTimes(2)
  })

  it('planData 到达（计划卡长高）触发贴底：与 streamReasoning 同根因同修', () => {
    const { rerender } = render(
      <TaskMessageList messages={[]} streamContent="" streamSteps={[]} isGenerating={true} />,
    )
    scrollIntoViewMock().mockClear()
    rerender(
      <TaskMessageList
        messages={[]}
        streamContent=""
        streamSteps={[]}
        isGenerating={true}
        planData={{ text: '计划', steps: [{ id: 1, desc: '检索资料', files: [] }], total: 1 }}
      />,
    )
    expect(scrollIntoViewMock()).toHaveBeenCalledWith({ behavior: 'auto' })
  })
})
