/** tests/BootGate.test.tsx — 启动门（窗口先行 2026-10-24）
 *  覆盖：初始 boot-status 各阶段文案与计时 / boot-stage 事件推进并重置计时 /
 *  ready → connect 确认 → 放行 children / 主进程 error 与 python:process-exit
 *  → 错误屏（重试 invoke app:relaunch、退出 invoke window:close）/
 *  确认 invoke 拒绝 → 错误屏。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && 'seconds' in opts ? `${key}:${opts.seconds}` : key,
  }),
}))

import BootGate from '../src/components/BootGate'

const invokeMock = vi.fn()
let stageHandlers: Array<(p: unknown) => void> = []
let exitHandlers: Array<(p: unknown) => void> = []

const onMock = vi.fn((channel: string, cb: (...args: unknown[]) => void) => {
  if (channel === 'app:boot-stage') stageHandlers.push(cb)
  if (channel === 'python:process-exit') exitHandlers.push(cb)
  return () => {
    stageHandlers = stageHandlers.filter((h) => h !== cb)
    exitHandlers = exitHandlers.filter((h) => h !== cb)
  }
})

const bootStatus = (stage: string, elapsedMs = 0, message: string | null = null) =>
  ({ stage, message, elapsedMs })

beforeEach(() => {
  invokeMock.mockReset()
  onMock.mockClear()
  stageHandlers = []
  exitHandlers = []
  ;(window as unknown as { electronAPI: unknown }).electronAPI = {
    invoke: invokeMock,
    on: onMock,
    send: vi.fn(),
    getPathForFile: vi.fn(),
  }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('BootGate boot gate (窗口先行 2026-10-24)', () => {
  it('renders the initial stage from boot-status with elapsed seconds and blocks children', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'app:boot-status' ? bootStatus('python', 12500) : {})
    render(<BootGate><div>app-content</div></BootGate>)

    await waitFor(() => {
      expect(screen.getByText('boot.stage.python')).toBeTruthy()
    })
    expect(screen.getByText('boot.elapsed:12')).toBeTruthy()
    expect(screen.queryByText('app-content')).toBeNull()
  })

  it('advances the stage on app:boot-stage events and resets the timer', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'app:boot-status' ? bootStatus('tools', 800) : {})
    render(<BootGate><div>app-content</div></BootGate>)

    await waitFor(() => {
      expect(screen.getByText('boot.stage.tools')).toBeTruthy()
    })
    await act(async () => {
      stageHandlers.forEach((h) => h({ stage: 'python', message: null, elapsedMs: 99999 }))
    })
    await waitFor(() => {
      expect(screen.getByText('boot.stage.python')).toBeTruthy()
    })
    // 新阶段从 0 本地计时（事件里的 elapsedMs 是上一阶段耗时，不适用）
    expect(screen.getByText('boot.elapsed:0')).toBeTruthy()
  })

  it('passes children through when boot-status reports ready at mount', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'app:boot-status' ? bootStatus('ready') : {})
    render(<BootGate><div>app-content</div></BootGate>)

    await waitFor(() => {
      expect(screen.getByText('app-content')).toBeTruthy()
    })
  })

  it('passes children through after a ready event followed by the connect confirm', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'app:boot-status' ? bootStatus('python', 100) : {})
    render(<BootGate><div>app-content</div></BootGate>)

    await waitFor(() => {
      expect(screen.getByText('boot.stage.python')).toBeTruthy()
    })
    await act(async () => {
      stageHandlers.forEach((h) => h({ stage: 'ready', message: null, elapsedMs: 0 }))
    })
    await waitFor(() => {
      expect(screen.getByText('app-content')).toBeTruthy()
    })
  })

  it('shows the error screen with the message on a main-process error event', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'app:boot-status' ? bootStatus('tools', 100) : {})
    render(<BootGate><div>app-content</div></BootGate>)

    await waitFor(() => {
      expect(screen.getByText('boot.stage.tools')).toBeTruthy()
    })
    await act(async () => {
      stageHandlers.forEach((h) => h({ stage: 'error', message: 'backend boom', elapsedMs: 0 }))
    })
    await waitFor(() => {
      expect(screen.getByText('boot.errorTitle')).toBeTruthy()
    })
    expect(screen.getByText('backend boom')).toBeTruthy()
    fireEvent.click(screen.getByText('boot.retry'))
    expect(invokeMock).toHaveBeenCalledWith('app:relaunch')
    fireEvent.click(screen.getByText('boot.quit'))
    expect(invokeMock).toHaveBeenCalledWith('window:close')
  })

  it('shows the error screen when the python process exits during boot', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'app:boot-status' ? bootStatus('python', 100) : {})
    render(<BootGate><div>app-content</div></BootGate>)

    await waitFor(() => {
      expect(screen.getByText('boot.stage.python')).toBeTruthy()
    })
    await act(async () => {
      exitHandlers.forEach((h) => h({ code: 1, signal: null }))
    })
    await waitFor(() => {
      expect(screen.getByText('boot.errorTitle')).toBeTruthy()
    })
    expect(screen.getByText('boot.errorHint')).toBeTruthy()
    expect(screen.queryByText('app-content')).toBeNull()
  })

  it('shows the error screen when the connect-confirm invoke rejects', async () => {
    let calls = 0
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'app:boot-status') {
        calls += 1
        if (calls === 1) return bootStatus('python', 0)
        throw new Error('ipc gone')
      }
      return {}
    })
    render(<BootGate><div>app-content</div></BootGate>)

    await waitFor(() => {
      expect(screen.getByText('boot.stage.python')).toBeTruthy()
    })
    await act(async () => {
      stageHandlers.forEach((h) => h({ stage: 'ready', message: null, elapsedMs: 0 }))
    })
    await waitFor(() => {
      expect(screen.getByText('boot.errorTitle')).toBeTruthy()
    })
    expect(screen.getByText('ipc gone')).toBeTruthy()
  })
})
