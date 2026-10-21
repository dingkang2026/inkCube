/** tests/SystemAlerts.test.tsx — App 级统一告警区·服务异常条（0.5 §4.2 打样修订 2026-10-18）
 *  覆盖：异常时显示红条并可跳设置 / 正常时不渲染 / invoke 抛错视为 down /
 *  app:health-changed 边沿重拉（异常→恢复正常后条消失）/ window focus 重拉。
 *  AvExclusionAlerts mock 为空渲染，隔离杀软条状态机（其行为归
 *  AvExclusionAlerts.test.tsx 全量覆盖）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) =>
      opts?.defaultValue ?? key,
  }),
}))

vi.mock('../src/components/layout/SystemAlerts.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/components/layout/AvExclusionAlerts', () => ({
  AvExclusionAlerts: () => null,
}))

import { SystemAlerts } from '../src/components/layout/SystemAlerts'

const invokeMock = vi.fn()
const onOpenSettings = vi.fn()

const HEALTH_OK = { status: 'ok', services: {} }
const HEALTH_DOWN = { status: 'down', services: {}, errorCode: 'PYTHON_SERVICE_DOWN' }
// degraded：embedding + vlm 缺失（2026-10-19 全必配统一，二者均触发 degraded）——明细逐项显示；
// web_search ok 对齐真实 compute_service_status 五键产出
const HEALTH_DEGRADED = {
  status: 'degraded',
  services: {
    llm: { status: 'ok', message: 'glm-4 — 已配置' },
    vlm: { status: 'error', code: 'VLM_NOT_CONFIGURED', action: 'settings:external' },
    web_search: { status: 'ok', message: '网络搜索（Tavily）— 已配置' },
    embedding: { status: 'error', code: 'EMBEDDING_MODEL_MISSING', action: 'settings:internal' },
    parsing: { status: 'ok', message: '文档解析 — 就绪' },
  },
}

beforeEach(() => {
  invokeMock.mockReset()
  onOpenSettings.mockReset()
  invokeMock.mockImplementation(async () => HEALTH_OK)
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const healthCalls = () =>
  invokeMock.mock.calls.filter((c) => c[0] === 'system:getHealth').length

describe('SystemAlerts service alert (0.5 §4.2 打样修订)', () => {
  it('shows the service alert with settings action when health is down', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'system:getHealth' ? HEALTH_DOWN : {})
    render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(screen.getByText('alerts.serviceMessage')).toBeTruthy()
    })
    expect(screen.getByText('alerts.serviceAction')).toBeTruthy()
  })

  it('renders no alert when health is ok', async () => {
    const { container } = render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('system:getHealth')
    })
    expect(container.querySelector('.alert')).toBeNull()
  })

  it('treats an invoke rejection as down (沿用原 HomeServiceBanner 语义)', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'system:getHealth') throw new Error('bridge gone')
      return {}
    })
    const { container } = render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(container.querySelector('.alert')).toBeTruthy()
    })
  })

  it('navigates to settings via the action button', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'system:getHealth' ? HEALTH_DOWN : {})
    render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(screen.getByText('alerts.serviceAction')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('alerts.serviceAction'))
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })

  it('lists missing-service details and navigates per-item to the matching section', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'system:getHealth' ? HEALTH_DEGRADED : {})
    render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(screen.getByText('alerts.serviceMessage')).toBeTruthy()
    })
    expect(screen.getByText('alerts.serviceItem.EMBEDDING_MODEL_MISSING')).toBeTruthy()
    expect(screen.getByText('alerts.serviceItem.VLM_NOT_CONFIGURED')).toBeTruthy()

    fireEvent.click(screen.getByText('alerts.serviceItem.EMBEDDING_MODEL_MISSING'))
    expect(onOpenSettings).toHaveBeenCalledWith('internalModel')
    fireEvent.click(screen.getByText('alerts.serviceItem.VLM_NOT_CONFIGURED'))
    expect(onOpenSettings).toHaveBeenCalledWith('externalModel')
  })

  it('navigates the web_search detail item to the webSearch section (供应商化锚点)', async () => {
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'system:getHealth'
        ? {
            status: 'degraded',
            services: {
              ...HEALTH_DEGRADED.services,
              web_search: { status: 'error', code: 'WEB_SEARCH_NOT_CONFIGURED', action: 'settings:webSearch' },
            },
          }
        : {})
    render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(screen.getByText('alerts.serviceItem.WEB_SEARCH_NOT_CONFIGURED')).toBeTruthy()
    })
    fireEvent.click(screen.getByText('alerts.serviceItem.WEB_SEARCH_NOT_CONFIGURED'))
    expect(onOpenSettings).toHaveBeenCalledWith('webSearch')
  })

  it('refetches on the app:health-changed edge and drops the alert once healthy', async () => {
    let down = true
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'system:getHealth' ? (down ? HEALTH_DOWN : HEALTH_OK) : {})
    const { container } = render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(container.querySelector('.alert')).toBeTruthy()
    })

    down = false
    await act(async () => {
      window.dispatchEvent(new CustomEvent('app:health-changed'))
    })
    await waitFor(() => {
      expect(container.querySelector('.alert')).toBeNull()
    })
    expect(healthCalls()).toBeGreaterThanOrEqual(2)
  })

  it('refetches on window focus', async () => {
    render(<SystemAlerts onOpenSettings={onOpenSettings} />)

    await waitFor(() => {
      expect(healthCalls()).toBe(1)
    })
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
    })
    await waitFor(() => {
      expect(healthCalls()).toBe(2)
    })
  })
})
