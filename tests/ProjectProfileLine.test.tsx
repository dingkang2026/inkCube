/** tests/ProjectProfileLine.test.tsx — 详情头部产业画像行（2.1 §4.5/§6.2，2.6 §4.8）
 *  条目渲染与盯产业事件（segment 优先、空回落 chain）/ NULL 与 '[]' 两态文案区分 /
 *  提取中态 / 增删人工编辑置 manual（人工优先，编译不覆盖）/ 重新提取 409 分流
 *  （受理语义经 structuredPythonResponse 返回值 code 透传，不抛异常）。
 *  invoke 打桩同 WikiTab 测试惯例；toast spy 断言提示文案（FileUploader 惯例）。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const { toastSpy } = vi.hoisted(() => ({ toastSpy: vi.fn() }))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastSpy,
}))

vi.mock('../src/features/project/ProjectProfileLine.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { ProjectProfileLine } from '../src/features/project/ProjectProfileLine'
import type { Project } from '../src/features/knowledge/types'

const invokeMock = vi.fn()

const makeProject = (overrides: Partial<Project> = {}): Project =>
  ({
    id: 'p1',
    name: 'Acme',
    industry_profile: null,
    profile_source: null,
    ...overrides,
  }) as unknown as Project

const mount = (project: Project, extracting = false, onChanged?: () => void) =>
  render(
    <ProjectProfileLine
      project={project}
      extracting={extracting}
      onProjectDataChanged={onChanged}
    />,
  )

beforeEach(() => {
  vi.clearAllMocks()
  ;(window as unknown as { electronAPI?: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const ENTRIES = [
  { chain: '具身智能', segment: '四足机器狗' },
  { chain: '机器人', segment: '' },
]
const RAW = JSON.stringify(ENTRIES)

describe('ProjectProfileLine 渲染', () => {
  it('renders chips and watch dispatches open-schedule-wizard (segment 优先,空回落 chain)', () => {
    const wizardSpy = vi.fn()
    window.addEventListener('app:open-schedule-wizard', wizardSpy)
    try {
      const { container, getAllByText } = mount(makeProject({ industry_profile: RAW }))
      expect(container.querySelectorAll('.profileChip')).toHaveLength(2)
      expect(container.textContent).toContain('具身智能')
      expect(container.textContent).toContain('四足机器狗')
      // 盯产业 → App 级新建订阅弹层预填（2.11 §8.1「从项目画像」渠道）
      fireEvent.click(getAllByText('project.profile.watch')[0])
      expect(wizardSpy).toHaveBeenCalledTimes(1)
      expect((wizardSpy.mock.calls[0][0] as CustomEvent).detail).toEqual({
        name: 'Acme',
        phValue: '四足机器狗',
      })
      // segment 为空 → 回落链条名
      fireEvent.click(getAllByText('project.profile.watch')[1])
      expect((wizardSpy.mock.calls[1][0] as CustomEvent).detail).toEqual({
        name: 'Acme',
        phValue: '机器人',
      })
    } finally {
      window.removeEventListener('app:open-schedule-wizard', wizardSpy)
    }
  })

  it('NULL (未提取) and [] (已提取未识别) show distinct empty hints', () => {
    const a = mount(makeProject({ industry_profile: null }))
    expect(a.container.textContent).toContain('project.profile.emptyHint')
    expect(a.container.textContent).not.toContain('project.profile.noneFound')
    a.unmount()
    const b = mount(makeProject({ industry_profile: '[]' }))
    expect(b.container.textContent).toContain('project.profile.noneFound')
    expect(b.container.textContent).not.toContain('project.profile.emptyHint')
  })

  it('extracting=true shows extracting state instead of chips', () => {
    const { container } = mount(makeProject({ industry_profile: RAW }), true)
    expect(container.textContent).toContain('project.profile.extracting')
    expect(container.querySelector('.profileChip')).toBeNull()
  })
})

describe('ProjectProfileLine 人工编辑', () => {
  it('remove persists remaining entries with profile_source=manual', async () => {
    invokeMock.mockResolvedValue({ success: true })
    const onChanged = vi.fn()
    const { getAllByTitle } = mount(makeProject({ industry_profile: RAW }), false, onChanged)
    fireEvent.click(getAllByTitle('project.profile.removeTitle')[0])
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('knowledge:updateProject', {
        projectId: 'p1',
        industry_profile: JSON.stringify([ENTRIES[1]]),
        profile_source: 'manual',
      }))
    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith('project.profile.manualSaved', 'success'))
    expect(onChanged).toHaveBeenCalled()
  })

  it('add form persists new entry with profile_source=manual', async () => {
    invokeMock.mockResolvedValue({ success: true })
    const { getByTitle, getByPlaceholderText } = mount(makeProject({ industry_profile: RAW }))
    fireEvent.click(getByTitle('project.profile.addTitle'))
    fireEvent.change(getByPlaceholderText('project.profile.chainPh'), {
      target: { value: '新能源' },
    })
    fireEvent.change(getByPlaceholderText('project.profile.segmentPh'), {
      target: { value: '储能' },
    })
    fireEvent.click(getByTitle('project.profile.confirmAdd'))
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('knowledge:updateProject', {
        projectId: 'p1',
        industry_profile: JSON.stringify([...ENTRIES, { chain: '新能源', segment: '储能' }]),
        profile_source: 'manual',
      }))
  })

  it('add failure keeps form open with input preserved (s-1 regression)', async () => {
    invokeMock.mockRejectedValue(new Error('boom'))
    const { getByTitle, getByPlaceholderText } = mount(makeProject({ industry_profile: RAW }))
    fireEvent.click(getByTitle('project.profile.addTitle'))
    fireEvent.change(getByPlaceholderText('project.profile.chainPh'), {
      target: { value: '新能源' },
    })
    fireEvent.click(getByTitle('project.profile.confirmAdd'))
    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith('project.profile.editFailed', 'error'))
    // 失败：表单不收起、输入保留，可直接重试（不再让用户重敲）
    expect(getByTitle('project.profile.confirmAdd')).toBeTruthy()
    expect((getByPlaceholderText('project.profile.chainPh') as HTMLInputElement).value).toBe('新能源')
  })

  it('persist failure shows error toast and skips onChanged', async () => {
    invokeMock.mockRejectedValue(new Error('boom'))
    const onChanged = vi.fn()
    const { getAllByTitle } = mount(makeProject({ industry_profile: RAW }), false, onChanged)
    fireEvent.click(getAllByTitle('project.profile.removeTitle')[0])
    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith('project.profile.editFailed', 'error'))
    expect(onChanged).not.toHaveBeenCalled()
  })
})

describe('ProjectProfileLine 重新提取', () => {
  it('409 running code surfaces info toast (受理在途经返回值 code 透传)', async () => {
    invokeMock.mockResolvedValue({ ok: false, code: 'PROFILE_EXTRACT_RUNNING', error: 'busy' })
    const { getByTitle } = mount(makeProject({ industry_profile: '[]' }))
    fireEvent.click(getByTitle('project.profile.reExtractTitle'))
    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith('project.profile.extractRunning', 'info'))
    expect(invokeMock).toHaveBeenCalledWith('knowledge:extractProfile', 'p1')
  })

  it('accepted silently waits for heartbeat edge refresh (no toast)', async () => {
    invokeMock.mockResolvedValue({ ok: true, accepted: true })
    const { getByTitle } = mount(makeProject({ industry_profile: '[]' }))
    fireEvent.click(getByTitle('project.profile.reExtractTitle'))
    await waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('knowledge:extractProfile', 'p1'))
    // 受理成功不弹 toast——进行中态经心跳浮现，完成靠父组件边沿刷新
    expect(toastSpy).not.toHaveBeenCalled()
  })

  it('channel error shows error toast', async () => {
    invokeMock.mockRejectedValue(new Error('boom'))
    const { getByTitle } = mount(makeProject({ industry_profile: '[]' }))
    fireEvent.click(getByTitle('project.profile.reExtractTitle'))
    await waitFor(() =>
      expect(toastSpy).toHaveBeenCalledWith('project.profile.extractFailed', 'error'))
  })
})
