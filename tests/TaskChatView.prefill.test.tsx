/**
 * TaskChatView 跨页预填信号消费测试（0.6 §3.2 V2-E1a T9）。
 * 覆盖：incomingPrefill 消费设值（不走模板 start）、消费后回调清信号、
 * ts 去重防重复消费。
 * 隔离：mock electronAPI.invoke（readiness/模板等内部拉取全部空响应）、
 * react-i18next 透传 key、Toast。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { TaskChatView } from '../src/features/task/TaskChatView'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({ useToast: () => vi.fn() }))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockResolvedValue({})
  ;(window as unknown as { electronAPI: { invoke: typeof invokeMock } }).electronAPI = {
    invoke: invokeMock,
  }
})

function renderChat(prefill?: { text: string; ts: number } | null, onConsumed?: () => void) {
  return render(
    <TaskChatView
      taskId={null}
      hasActiveTask={false}
      messages={[]}
      isGenerating={false}
      streamContent=""
      streamSteps={[]}
      onSendMessage={vi.fn()}
      onStopGenerate={vi.fn()}
      incomingPrefill={prefill}
      onPrefillConsumed={onConsumed}
    />,
  )
}

const getInput = (): HTMLTextAreaElement =>
  screen.getByPlaceholderText('task.inputPlaceholder') as HTMLTextAreaElement

describe('TaskChatView incomingPrefill', () => {
  it('消费预填信号：设输入框值 + 回调清信号（不自动发送）', async () => {
    const onConsumed = vi.fn()
    renderChat({ text: '# 华润舆情\n要点一', ts: 1 }, onConsumed)

    await waitFor(() => {
      expect(getInput().value).toBe('# 华润舆情\n要点一')
    })
    expect(onConsumed).toHaveBeenCalledTimes(1)
    // 不自动发送：无 chat 通道调用
    const chatCalls = invokeMock.mock.calls.filter((c) => c[0] === 'tasks:chat')
    expect(chatCalls).toHaveLength(0)
  })

  it('相同 ts 的信号不重复消费（App 清信号前重渲染防抖）', async () => {
    const onConsumed = vi.fn()
    const { rerender } = renderChat({ text: '第一版', ts: 7 }, onConsumed)
    await waitFor(() => expect(onConsumed).toHaveBeenCalledTimes(1))

    rerender(
      <TaskChatView
        taskId={null}
        hasActiveTask={false}
        messages={[]}
        isGenerating={false}
        streamContent=""
        streamSteps={[]}
        onSendMessage={vi.fn()}
        onStopGenerate={vi.fn()}
        incomingPrefill={{ text: '第二版', ts: 7 }}
        onPrefillConsumed={onConsumed}
      />,
    )
    await waitFor(() => {
      expect(getInput().value).toBe('第一版')
    })
    expect(onConsumed).toHaveBeenCalledTimes(1)
  })

  it('新 ts 信号再次消费（连续两次转任务）', async () => {
    const onConsumed = vi.fn()
    const { rerender } = renderChat({ text: '第一条', ts: 1 }, onConsumed)
    await waitFor(() => expect(getInput().value).toBe('第一条'))

    rerender(
      <TaskChatView
        taskId={null}
        hasActiveTask={false}
        messages={[]}
        isGenerating={false}
        streamContent=""
        streamSteps={[]}
        onSendMessage={vi.fn()}
        onStopGenerate={vi.fn()}
        incomingPrefill={{ text: '第二条', ts: 2 }}
        onPrefillConsumed={onConsumed}
      />,
    )
    await waitFor(() => {
      expect(getInput().value).toBe('第二条')
    })
    expect(onConsumed).toHaveBeenCalledTimes(2)
  })
})
