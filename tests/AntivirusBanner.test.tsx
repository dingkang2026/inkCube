/** tests/AntivirusBanner.test.tsx — 杀软引导横幅（2.8 §12 F4 前置阻断 + F6 检测分流 + F8 逐 AV 确认）
 *  覆盖：Defender 条（verified=true 不渲染 / false 引导授权 / 成功后消失 /
 *  失败降级 fallback / 「暂不」关闭 / verified 翻 true 回退隐藏）；第三方条
 *  （F8 逐 AV 一条：verified=false 时与 Defender 条并存 / verified=true 后
 *  独立在场 / 多 AV 多条 / ack 后单条消失其余保留 / ackedNames 过滤 / ×
 *  会话级关闭重挂载再现 / 复制路径走 system:copyText / defenderSkipped 后
 *  Defender 条消 + 第三方条在场）。轮询用真 usePolling（挂载即首查，测试只
 *  依赖首轮 30s 间隔内的行为）。
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

/** F8：thirdPartyAv 数组 + ackedNames 台账（cacheDir 恒带，供路径展示/复制） */
function mockStatus(opts: {
  verified: boolean
  thirdPartyAv?: string[]
  ackedNames?: string[]
  cacheDir?: string
}) {
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'system:getUvExclusionStatus') {
      return {
        verified: opts.verified,
        cacheDir: opts.cacheDir ?? 'D:\\InkCube-dev-cache\\uv-cache',
        ...(opts.thirdPartyAv ? { thirdPartyAv: opts.thirdPartyAv } : {}),
        ackedNames: opts.ackedNames ?? [],
      }
    }
    return {}
  })
}

const DEFENDER_PROMPT =
  '代码执行与网页抓取环境需要一次系统授权（加入杀毒软件白名单），授权完成前依赖安装无法进行'

describe('AntivirusBanner (2.8 §12 F4/F6/F8)', () => {
  it('renders nothing when verified without third-party AV', async () => {
    mockStatus({ verified: true })
    const { container } = render(<AntivirusBanner />)
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:getUvExclusionStatus')
    })
    expect(container.querySelector('.banner')).toBeNull()
  })

  it('shows Defender prompt with authorize button when not verified', async () => {
    mockStatus({ verified: false })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeTruthy()
    })
    expect(screen.getByText(DEFENDER_PROMPT)).toBeTruthy()
    expect(screen.getByText('立即授权')).toBeTruthy()
  })

  it('closes Defender prompt via dismiss without invoking elevation', async () => {
    mockStatus({ verified: false })
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

  it('hides Defender banner again once verification turns true', async () => {
    vi.useFakeTimers()
    let verified = false
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified, ackedNames: [] }
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

  it('keeps polling when the first response omits thirdPartyAv, shows the banner once WSC resolves (H1)', async () => {
    vi.useFakeTimers()
    let wscResolved = false
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') {
        return wscResolved
          ? { verified: true, thirdPartyAv: ['Huorong'], ackedNames: [], cacheDir: 'D:\\cache\\uv-cache' }
          : { verified: true, ackedNames: [], cacheDir: 'D:\\cache\\uv-cache' }
      }
      return {}
    })
    const statusCalls = () =>
      invokeMock.mock.calls.filter((c) => c[0] === 'system:getUvExclusionStatus').length
    const { container } = render(<AntivirusBanner />)

    await act(async () => {}) // 首轮：字段缺省（WSC 首查 ~1s 未落）
    expect(container.querySelector('.banner')).toBeNull()
    expect(statusCalls()).toBe(1)

    wscResolved = true
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    // 回归断言：旧代码首轮 verified=true 即停轮，statusCalls 恒 1、条永不出现
    expect(statusCalls()).toBe(2)
    expect(screen.getByText(/检测到 Huorong/)).toBeTruthy()
  })

  it('stops polling once WSC resolves to an empty list (thirdPartyAv: [] is definitive, H1)', async () => {
    vi.useFakeTimers()
    let wscResolved = false
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') {
        return wscResolved
          ? { verified: true, thirdPartyAv: [], ackedNames: [], cacheDir: 'D:\\cache\\uv-cache' }
          : { verified: true, ackedNames: [], cacheDir: 'D:\\cache\\uv-cache' }
      }
      return {}
    })
    const statusCalls = () =>
      invokeMock.mock.calls.filter((c) => c[0] === 'system:getUvExclusionStatus').length
    render(<AntivirusBanner />)

    await act(async () => {}) // 首轮：字段缺省
    expect(statusCalls()).toBe(1)

    wscResolved = true
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(statusCalls()).toBe(2)
    // 查中无第三方 → allDone 成立 → 停轮（不因 avChecked 永不停）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(statusCalls()).toBe(2)
  })

  it('hides the Defender banner after successful exclusion', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified: false, ackedNames: [] }
      if (channel === 'system:addUvCacheExclusion') return { added: true }
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

  it('shows a per-AV third-party banner beside the Defender prompt (F8)', async () => {
    mockStatus({ verified: false, thirdPartyAv: ['Lenovo Anti-Virus powered by Huorong Security'] })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(container.querySelectorAll('.banner').length).toBe(2)
    })
    expect(screen.getByText(/检测到 Lenovo Anti-Virus powered by Huorong Security/)).toBeTruthy()
    expect(screen.getByText(DEFENDER_PROMPT)).toBeTruthy() // Defender 条独立在场
    expect(screen.getByText('立即授权')).toBeTruthy()
    expect(screen.getByText('我已处理')).toBeTruthy()
  })

  it('shows the third-party banner even when verified (F8 取代「verified 即整体隐藏」)', async () => {
    mockStatus({ verified: true, thirdPartyAv: ['Huorong'] })
    const { container } = render(<AntivirusBanner />)
    await waitFor(() => {
      expect(screen.getByText(/检测到 Huorong/)).toBeTruthy()
    })
    expect(container.querySelectorAll('.banner').length).toBe(1) // 仅第三方条
    expect(screen.queryByText('立即授权')).toBeNull()
  })

  it('renders one banner per AV and ack removes only its own', async () => {
    invokeMock.mockImplementation(async (channel: string, arg?: string) => {
      if (channel === 'system:getUvExclusionStatus') {
        return { verified: true, thirdPartyAv: ['AV-One', 'AV-Two'], ackedNames: [] }
      }
      if (channel === 'system:ackThirdPartyAv') {
        return { acked: true, ackedNames: [arg] }
      }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(container.querySelectorAll('.banner').length).toBe(2)
    })
    fireEvent.click(screen.getAllByText('我已处理')[0]) // 第一条（DOM 顺序）
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:ackThirdPartyAv', 'AV-One')
    })
    await waitFor(() => {
      expect(container.querySelectorAll('.banner').length).toBe(1)
    })
    expect(screen.getByText(/检测到 AV-Two/)).toBeTruthy()
    expect(screen.queryByText(/检测到 AV-One/)).toBeNull()
  })

  it('filters already-acked names via ackedNames ledger', async () => {
    mockStatus({ verified: true, thirdPartyAv: ['AV-Acked', 'AV-New'], ackedNames: ['AV-Acked'] })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(container.querySelectorAll('.banner').length).toBe(1)
    })
    expect(screen.getByText(/检测到 AV-New/)).toBeTruthy()
    expect(screen.queryByText(/检测到 AV-Acked/)).toBeNull()
  })

  it('session-dismiss via × hides only for this mount, returns after remount', async () => {
    mockStatus({ verified: true, thirdPartyAv: ['Huorong'] })
    const first = render(<AntivirusBanner />)
    await waitFor(() => {
      expect(screen.getByText(/检测到 Huorong/)).toBeTruthy()
    })
    fireEvent.click(first.container.querySelector('.btnClose') as Element)
    await waitFor(() => {
      expect(first.container.querySelector('.banner')).toBeNull()
    })
    expect(invokeMock).not.toHaveBeenCalledWith('system:ackThirdPartyAv', 'Huorong')

    first.unmount()
    const second = render(<AntivirusBanner />)
    await waitFor(() => {
      expect(second.container.querySelector('.banner')).toBeTruthy()
    })
    expect(screen.getByText(/检测到 Huorong/)).toBeTruthy()
  })

  it('copies the cache path via system:copyText and flashes copied state', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') {
        return { verified: true, cacheDir: 'D:\\cache\\uv-cache', thirdPartyAv: ['Huorong'], ackedNames: [] }
      }
      if (channel === 'system:copyText') return { copied: true }
      return {}
    })
    render(<AntivirusBanner />)
    await waitFor(() => {
      expect(screen.getByText('复制路径')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('复制路径'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:copyText', 'D:\\cache\\uv-cache')
    })
    await waitFor(() => {
      expect(screen.getByText('已复制')).toBeTruthy()
    })
  })

  it('keeps the third-party banner and drops the Defender banner after defender-skipped authorization', async () => {
    invokeMock.mockImplementation(async (channel: string, arg?: string) => {
      if (channel === 'system:getUvExclusionStatus') {
        return { verified: false, thirdPartyAv: ['Lenovo Anti-Virus powered by Huorong Security'], ackedNames: [] }
      }
      if (channel === 'system:addUvCacheExclusion') return { added: true, defenderSkipped: true }
      if (channel === 'system:ackThirdPartyAv') return { acked: true, ackedNames: [arg] }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('立即授权')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('立即授权'))

    await waitFor(() => {
      expect(container.querySelectorAll('.banner').length).toBe(1) // 仅第三方条（hint 态已废）
    })
    expect(screen.getByText(/检测到 Lenovo Anti-Virus powered by Huorong Security/)).toBeTruthy()

    fireEvent.click(screen.getByText('我已处理'))
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:ackThirdPartyAv', 'Lenovo Anti-Virus powered by Huorong Security')
    })
    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeNull()
    })
  })

  it('disappears directly after defender-skipped authorization without a third-party AV', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified: false, ackedNames: [] }
      if (channel === 'system:addUvCacheExclusion') return { added: true, defenderSkipped: true }
      return {}
    })
    const { container } = render(<AntivirusBanner />)

    await waitFor(() => {
      expect(screen.getByText('立即授权')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('立即授权'))

    await waitFor(() => {
      expect(container.querySelector('.banner')).toBeNull()
    })
  })

  it('falls back to manual guidance when exclusion fails', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getUvExclusionStatus') return { verified: false, ackedNames: [] }
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
      if (channel === 'system:getUvExclusionStatus') return { verified: false, ackedNames: [] }
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
