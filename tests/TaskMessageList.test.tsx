/** tests/TaskMessageList.test.tsx — browser-fetch 登录墙横幅（2.8 §8.3）
 *  覆盖：历史消息（action+observation 顺序配对）中 needs_login observation
 *  渲染登录横幅（iter 卡片默认收起，展开后可见）；点击「去登录」先幂等登记
 *  （登记失败不阻塞）再开登录窗口；正常 observation / 未完成步骤不渲染横幅。
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import type { TaskMessage } from '../src/types'

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

import { TaskMessageList } from '../src/features/task/TaskMessageList'

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

  it('registers the domain best-effort, then opens the login window', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'weblogin:add') throw new Error('backend down') // 登记失败不阻塞开窗
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
      expect(invokeMock).toHaveBeenCalledWith('weblogin:open', { domain: 'example.com' })
    })
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
