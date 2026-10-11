/** tests/AntivirusBanner.test.tsx — 一键加白名单横幅（2.8 §12 F3）
 *  覆盖：blocked=false 不渲染；blocked=true 渲染提示 + 一键优化；点击后
 *  added=true 横幅消失；added=false / invoke 抛错降级 fallback 引导文案，
 *  「知道了」关闭；prompt 态可手动关闭（不触发提权）；信号归零后 prompt
 *  回退隐藏。轮询用真 usePolling（挂载即首查，30s 间隔内测试只依赖首查）。
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
    if (channel === 'system:getUvCacheBlocked') return { blocked: false, tmp_count: 0 }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

function mockBlocked(blocked: boolean) {
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'system:getUvCacheBlocked') return { blocked, tmp_count: blocked ? 3 : 0 }
    return {}
  })
}

describe('AntivirusBanner (2.8 §12 F3)', () => {
  it('renders nothing when not blocked', async () => {
    const { container } = render(<AntivirusBanner />)
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:getUvCacheBlocked')
    })
    expect(container.querySelector('.banner')).toBeNull()
  })

  it('shows prompt with optimize button when blocked', async () => {
    mockBlocked(true)
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeTruthy()
    })
    expect(screen.getByText('检测到杀毒软件实时扫描拖慢依赖安装，建议将依赖缓存加入白名单')).toBeTruthy()
    expect(screen.getByText('一键优化')).toBeTruthy()
  })

  it('closes from prompt via dismiss without invoking elevation', async () => {
    mockBlocked(true)
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('一键优化')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('知道了'))

    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeNull()
    })
    expect(invokeMock).not.toHaveBeenCalledWith('system:addUvCacheExclusion')
  })

  it('hides again when the blocked signal clears', async () => {
    vi.useFakeTimers()
    let blocked = true
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvCacheBlocked') {
        return { blocked, tmp_count: blocked ? 3 : 0 }
      }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await act(async () => {}) // 首轮 tick（挂载即查）
    expect(container.querySelector('.banner')).toBeTruthy()

    blocked = false
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000) // 下一轮轮询
    })
    expect(container.querySelector('.banner')).toBeNull()
  })

  it('hides the banner after successful exclusion', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvCacheBlocked') return { blocked: true, tmp_count: 3 }
      if (channel === 'system:addUvCacheExclusion') return { added: true, fallback: false }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('一键优化')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('一键优化'))

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:addUvCacheExclusion')
    })
    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeNull()
    })
  })

  it('falls back to manual guidance when exclusion fails', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvCacheBlocked') return { blocked: true, tmp_count: 3 }
      if (channel === 'system:addUvCacheExclusion') return { added: false, fallback: true }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('一键优化')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('一键优化'))

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
      if (channel === 'system:getUvCacheBlocked') return { blocked: true, tmp_count: 3 }
      if (channel === 'system:addUvCacheExclusion') throw new Error('electron down')
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('一键优化')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('一键优化'))

    await waitFor(() => {
      expect(screen.getByText(/未能自动写入排除项/)).toBeTruthy()
    })
    expect(container.querySelector('.banner')).toBeTruthy()
  })
})
