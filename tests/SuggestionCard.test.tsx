/** tests/SuggestionCard.test.tsx — AI 建议卡(3.0 四期 T8,3.1 §5.3)
 *  pending 列表渲染 / 采纳 create_task 成功派发 app:tasks-changed + 移除 + 成功 Toast /
 *  采纳目标失效(IPC errorDetail=target_task_missing)→ 失效 Toast + 移除 / 忽略移除(不派发事件)。
 *  整体 review W1 增:onAccepted 回调(采纳成功才触发,父级看板显式重拉;
 *  忽略与目标失效任务未变不触发)。
 *  组件自包含数据域(mock invoke 直测),零新增轮询(0.4 §3.1)。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const toastMock = vi.hoisted(() => vi.fn())
const invokeMock = vi.fn()
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k,
    i18n: { language: 'zh-CN' },
  }),
}))
vi.mock('../src/features/project/affairs/SuggestionCard.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({ useToast: () => toastMock }))

import { render, cleanup, fireEvent, act } from '@testing-library/react'
import { SuggestionCard } from '../src/features/project/affairs/SuggestionCard'
import type { TaskSuggestion } from '../src/features/project/affairs/types'

const sugA: TaskSuggestion = {
  id: 's1', project_id: 'p1', kind: 'create_task', title: '跟进经营异常移出进度',
  description: '风险台账 2026-09 发现', suggested_phase: 'during', suggested_task_type: 'milestone',
  target_task_id: null, accepted_task_id: null, status: 'pending',
  created_at: '2026-10-10T00:00:00', decided_at: null,
}
const sugB: TaskSuggestion = {
  id: 's2', project_id: 'p1', kind: 'complete_task', title: '设备验收完成,建议打卡',
  description: '', suggested_phase: 'during', suggested_task_type: null,
  target_task_id: 't9', accepted_task_id: null, status: 'pending',
  created_at: '2026-10-10T00:00:00', decided_at: null,
}

/** 排空 microtask(load/采纳 async,无 fake timers) */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  toastMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'affairs:listSuggestions') return { suggestions: [sugA, sugB] }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('SuggestionCard (3.0 四期 T8)', () => {
  it('renders pending suggestions; hides entirely when none', async () => {
    const { container, getByText } = render(<SuggestionCard projectId="p1" />)
    await flush()
    expect(container.textContent).toContain('affairs.suggestion.title')
    getByText('跟进经营异常移出进度')
    getByText('设备验收完成,建议打卡')

    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'affairs:listSuggestions' ? { suggestions: [] } : {})
    const { container: empty } = render(<SuggestionCard projectId="p1" />)
    await flush()
    expect(empty.textContent).not.toContain('affairs.suggestion.title')
  })

  it('accept create_task: dispatches tasks-changed, removes entry, success toast', async () => {
    const spy = vi.spyOn(window, 'dispatchEvent')
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'affairs:listSuggestions') return { suggestions: [sugA] }
      if (channel === 'affairs:acceptSuggestion') return { task: { id: 'new1', title: sugA.title } }
      return {}
    })
    const { getByText, queryByText } = render(<SuggestionCard projectId="p1" />)
    await flush()
    fireEvent.click(getByText('affairs.suggestion.accept'))
    await flush()
    const evt = spy.mock.calls.find((c) => (c[0] as CustomEvent).type === 'app:tasks-changed')
    expect(evt).toBeTruthy()
    expect(invokeMock).toHaveBeenCalledWith('affairs:acceptSuggestion', { projectId: 'p1', suggestionId: 's1' })
    expect(queryByText('跟进经营异常移出进度')).toBeNull()
    expect(toastMock).toHaveBeenCalledWith('affairs.suggestion.acceptedToast', 'success')
  })

  it('accept on missing target (404 target_task_missing): invalidated toast + removal', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'affairs:listSuggestions') return { suggestions: [sugB] }
      if (channel === 'affairs:acceptSuggestion') {
        const e = new Error('Python backend error (404)') as Error & { errorDetail?: string }
        e.errorDetail = 'target_task_missing'
        throw e
      }
      return {}
    })
    const { getByText, queryByText } = render(<SuggestionCard projectId="p1" />)
    await flush()
    fireEvent.click(getByText('affairs.suggestion.accept'))
    await flush()
    expect(toastMock).toHaveBeenCalledWith('affairs.suggestion.invalidated', 'error')
    expect(queryByText('设备验收完成,建议打卡')).toBeNull()
  })

  it('dismiss removes entry without dispatching tasks-changed', async () => {
    const spy = vi.spyOn(window, 'dispatchEvent')
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'affairs:listSuggestions') return { suggestions: [sugB] }
      if (channel === 'affairs:dismissSuggestion') return { suggestion: { ...sugB, status: 'dismissed' } }
      return {}
    })
    const { getByText, queryByText } = render(<SuggestionCard projectId="p1" />)
    await flush()
    fireEvent.click(getByText('affairs.suggestion.dismiss'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:dismissSuggestion', { projectId: 'p1', suggestionId: 's2' })
    expect(queryByText('设备验收完成,建议打卡')).toBeNull()
    expect(spy.mock.calls.some((c) => (c[0] as CustomEvent).type === 'app:tasks-changed')).toBe(false)
  })
})

describe('SuggestionCard onAccepted (整体 review W1)', () => {
  it('calls onAccepted after successful accept (父级看板重拉)', async () => {
    const onAccepted = vi.fn()
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'affairs:listSuggestions' ? { suggestions: [sugA] } : {})
    const { getByText } = render(<SuggestionCard projectId="p1" onAccepted={onAccepted} />)
    await flush()
    fireEvent.click(getByText('affairs.suggestion.accept'))
    await flush()
    expect(onAccepted).toHaveBeenCalledTimes(1)
  })

  it('does NOT call onAccepted on dismiss (任务未变)', async () => {
    const onAccepted = vi.fn()
    invokeMock.mockImplementation(async (channel: string) =>
      channel === 'affairs:listSuggestions' ? { suggestions: [sugA] } : {})
    const { getByText } = render(<SuggestionCard projectId="p1" onAccepted={onAccepted} />)
    await flush()
    fireEvent.click(getByText('affairs.suggestion.dismiss'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:dismissSuggestion', { projectId: 'p1', suggestionId: 's1' })
    expect(onAccepted).not.toHaveBeenCalled()
  })

  it('does NOT call onAccepted when target task is missing (任务未变)', async () => {
    const onAccepted = vi.fn()
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'affairs:listSuggestions') return { suggestions: [sugB] }
      if (channel === 'affairs:acceptSuggestion') {
        const e = new Error('Python backend error (404)') as Error & { errorDetail?: string }
        e.errorDetail = 'target_task_missing'
        throw e
      }
      return {}
    })
    const { getByText } = render(<SuggestionCard projectId="p1" onAccepted={onAccepted} />)
    await flush()
    fireEvent.click(getByText('affairs.suggestion.accept'))
    await flush()
    expect(onAccepted).not.toHaveBeenCalled()
  })
})
