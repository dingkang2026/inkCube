/** tests/WebLogins.test.tsx — 设置页「网页登录态」区块（2.8 §8.1）
 *  覆盖：列表加载渲染 / 手动添加并刷新 / 后端业务错误（HTTP 200 + {error}）
 *  就地展示且不刷新列表 / 移除链路（先清 cookie 再删行，§9.2 方向）。
 *  ConfirmDialog 打桩为恒确认；IPC 全部经 invokeMock 捕获。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      let out = key
      for (const [k, v] of Object.entries(opts || {})) {
        if (typeof v === 'string' || typeof v === 'number') out = out.replaceAll(`{{${k}}}`, String(v))
      }
      return out
    },
  }),
}))

vi.mock('../src/features/settings/WebLogins.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

// 删除路径需要确认框；恒确认，捕获入参断言文案通道
const confirmMock = vi.hoisted(() => vi.fn())
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))

// 稳定引用（返回新 vi.fn() 会因 useCallback 依赖失效触发无限重渲染）
const handleErrorMock = vi.hoisted(() => vi.fn())
vi.mock('../src/hooks/useErrorHandler', () => ({
  useErrorHandler: () => handleErrorMock,
}))

import { WebLogins } from '../src/features/settings/WebLogins'

const invokeMock = vi.fn()
const sample = { domain: 'example.com', note: '主站', created_at: '2026-01-01T00:00:00' }

const listCalls = () => invokeMock.mock.calls.filter(([c]) => c === 'weblogin:list').length

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'weblogin:list') return { logins: [sample] }
    return {}
  })
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(true)
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('WebLogins settings block (2.8 §8.1)', () => {
  it('renders registered logins with note and actions', async () => {
    render(<WebLogins />)
    expect(await screen.findByText('example.com')).toBeTruthy()
    expect(screen.getByText('主站')).toBeTruthy()
    expect(screen.getByText('webLogins.relogin')).toBeTruthy()
    expect(screen.getByTitle('webLogins.remove')).toBeTruthy()
  })

  it('adds a login (raw input, backend normalizes) and refreshes the list', async () => {
    let calls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'weblogin:list') {
        calls++
        return calls === 1 ? { logins: [] } : { logins: [sample] }
      }
      return { login: {} }
    })
    render(<WebLogins />)
    await screen.findByText('webLogins.empty')

    fireEvent.change(screen.getByPlaceholderText('webLogins.domainPlaceholder'), {
      target: { value: 'Example.com/Profile ' },
    })
    fireEvent.change(screen.getByPlaceholderText('webLogins.notePlaceholder'), {
      target: { value: '备注' },
    })
    fireEvent.click(screen.getByText('webLogins.add'))

    // 前端透传原始输入（trim 后），域名归一化在后端（§3.2）
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('weblogin:add', { domain: 'Example.com/Profile', note: '备注' })
    })
    await waitFor(() => expect(listCalls()).toBe(2))
    expect(await screen.findByText('example.com')).toBeTruthy()
  })

  it('shows backend {error} inline and skips the list refresh', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'weblogin:list') return { logins: [] }
      return { error: '无效的域名' }
    })
    render(<WebLogins />)
    await screen.findByText('webLogins.empty')

    fireEvent.change(screen.getByPlaceholderText('webLogins.domainPlaceholder'), {
      target: { value: '192.168.1.1' },
    })
    fireEvent.click(screen.getByText('webLogins.add'))

    expect(await screen.findByText('无效的域名')).toBeTruthy()
    expect(listCalls()).toBe(1) // 失败不触发刷新
  })

  it('removes via cookie-clear first, then row delete, then refreshes (§9.2)', async () => {
    render(<WebLogins />)
    await screen.findByText('example.com')
    fireEvent.click(screen.getByTitle('webLogins.remove'))

    // 确认框走 webLogins.remove 通道
    expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'webLogins.remove' }))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('weblogin:remove-cookies', { domain: 'example.com' })
      expect(invokeMock).toHaveBeenCalledWith('weblogin:remove', { domain: 'example.com' })
    })
    const channels = invokeMock.mock.calls.map(([c]) => c)
    expect(channels.indexOf('weblogin:remove-cookies')).toBeLessThan(channels.indexOf('weblogin:remove'))
    await waitFor(() => expect(listCalls()).toBe(2))
  })
})
