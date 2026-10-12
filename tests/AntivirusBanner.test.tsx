/** tests/AntivirusBanner.test.tsx — 白名单授权横幅（2.8 §12 F4，前置阻断）
 *  覆盖：verified=true 不渲染；verified=false 渲染提示 + 立即授权；点击后
 *  added=true 横幅消失；added=false / invoke 抛错降级 fallback 引导文案，
 *  fallback 以「知道了」关闭；prompt 态以「暂不」手动关闭（不触发提权）；verified 翻 true 后
 *  prompt 回退隐藏。轮询用真 usePolling（挂载即首查，30s 间隔内测试只依赖首查）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) =>
      opts?.defaultValue ?? key,
  }),
}))

vi.mock('../src/features/task/AntivirusBanner.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/hooks/usePolling', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/hooks/usePolling')>()
  // 保留实现（挂载即首查），测试只依赖首轮 30s 间隔内的行为
  return actual
})

import { AntivirusBanner } from '../src/features/task/AntivirusBanner'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'system:getUvExclusionStatus') return { verified: true }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

function mockVerified(verified: boolean) {
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'system:getUvExclusionStatus') return { verified }
    return {}
  })
}

describe('AntivirusBanner (2.8 §12 F4)', () => {
  it('renders nothing when already verified', async () => {
    const { container } = render(<AntivirusBanner />)
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:getUvExclusionStatus')
    })
    expect(container.querySelector('.banner')).toBeNull()
  })

  it('shows prompt with authorize button when not verified', async () => {
    mockVerified(false)
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeTruthy()
    })
    expect(screen.getByText('代码执行与网页抓取环境需要一次系统授权（加入杀毒软件白名单），授权完成前依赖安装无法进行')).toBeTruthy()
    expect(screen.getByText('立即授权')).toBeTruthy()
  })

  it('closes from prompt via dismiss without invoking elevation', async () => {
    mockVerified(false)
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('立即授权')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('暂不'))

    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeNull()
    })
    expect(invokeMock).not.toHaveBeenCalledWith('system:addUvCacheExclusion')
  })

  it('hides again once verification turns true', async () => {
    vi.useFakeTimers()
    let verified = false
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await act(async () => {}) // 首轮 tick（挂载即查）
    expect(container.querySelector('.banner')).toBeTruthy()

    verified = true
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000) // 下一轮轮询
    })
    expect(container.querySelector('.banner')).toBeNull()
  })

  it('hides the banner after successful exclusion', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified: false }
      if (channel === 'system:addUvCacheExclusion') return { added: true, fallback: false }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('立即授权')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('立即授权'))

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:addUvCacheExclusion')
    })
    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeNull()
    })
  })

  it('falls back to manual guidance when exclusion fails', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified: false }
      if (channel === 'system:addUvCacheExclusion') return { added: false, fallback: true }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('立即授权')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('立即授权'))

    await waitFor(() => {
      expect(screen.getByText(/未能自动写入排除项/)).toBeTruthy()
    })
    expect(container.querySelector('.banner')).toBeTruthy()

    fireEvent.click(screen.getByText('知道了'))
    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeNull()
    })
  })

  it('falls back to manual guidance when the IPC call throws', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified: false }
      if (channel === 'system:addUvCacheExclusion') throw new Error('electron down')
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('立即授权')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('立即授权'))

    await waitFor(() => {
      expect(screen.getByText(/未能自动写入排除项/)).toBeTruthy()
    })
    expect(container.querySelector('.banner')).toBeTruthy()
  })
})
