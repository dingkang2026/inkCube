/** tests/PptTemplatePanel.test.tsx — PPT模板 Tab 面板（2.3 §6.1 四类分组 + 创建中任务卡）
 *  覆盖：JobCard 各状态操作组显隐（running/awaiting → 取消；终态/paused → 删除）、
 *  取消/删除经确认后走 ppt:cancel / ppt:delete、确认拒绝不发起 IPC、删除成功 toast、
 *  awaiting 提案置顶 + brief 门按钮。i18n/Toast/ConfirmDialog/CSS 打桩隔离
 *  （同 PptDetail.test 约定）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const toastMock = vi.fn()
const confirmState = { value: true }

vi.mock('react-i18next', () => ({
  // i18n.language：TaskHistoryReplay 气泡时间戳格式化需要
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh-CN' } }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => confirmState.value,
}))

vi.mock('../src/features/project/tabs/TemplateManagement.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { PptTemplatePanel } from '../src/features/project/tabs/PptTemplatePanel'

const invokeMock = vi.fn()

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const baseJob = {
  id: 'job-1',
  title: '品牌模板甲',
  status: 'running',
  stage: 'strategy',
  error: null as string | null,
  template_kind: 'brand',
  template_id: 'tpl-new',
}

beforeEach(() => {
  toastMock.mockReset()
  confirmState.value = true
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'ppt-template:list') return { templates: {} }
    if (channel === 'ppt-template:jobs') return { jobs: [{ ...baseJob }], has_more: false }
    if (channel === 'tasks:getMessages') {
      return {
        messages: [
          { id: 'm1', task_id: 'job-1', role: 'assistant', content: '提案正文', created_at: '2026-10-20T10:00:00Z' },
        ],
      }
    }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

/** 渲染面板并展开唯一任务卡（点击标题行切 expanded） */
const expandJob = async () => {
  render(<PptTemplatePanel />)
  await flush()
  fireEvent.click(screen.getByText('品牌模板甲'))
  await flush()
}

describe('PptTemplatePanel JobCard 操作', () => {
  it('running：展开后取消可见、删除不可见；取消经确认后走 ppt:cancel', async () => {
    await expandJob()
    expect(screen.getByText('project.pptTpl.job.cancel')).toBeTruthy()
    expect(screen.queryByText('project.pptTpl.job.delete')).toBeNull()
    fireEvent.click(screen.getByText('project.pptTpl.job.cancel'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('ppt:cancel', 'job-1')
    expect(toastMock).toHaveBeenCalledWith('project.pptTpl.job.cancelled', 'success')
  })

  it('awaiting_input：提案置顶 + brief 门 + 取消，无删除', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt-template:list') return { templates: {} }
      if (channel === 'ppt-template:jobs') {
        return { jobs: [{ ...baseJob, status: 'awaiting_input' }], has_more: false }
      }
      if (channel === 'tasks:getMessages') {
        return {
          messages: [
            { id: 'm1', task_id: 'job-1', role: 'assistant', content: '提案正文', created_at: '2026-10-20T10:00:00Z' },
          ],
        }
      }
      return {}
    })
    await expandJob()
    expect(screen.getByText('project.pptTpl.job.proposalTitle')).toBeTruthy()
    expect(screen.getByText('project.pptTpl.job.confirmBrief')).toBeTruthy()
    expect(screen.getByText('project.pptTpl.job.cancel')).toBeTruthy()
    expect(screen.queryByText('project.pptTpl.job.delete')).toBeNull()
  })

  it('completed：删除可见、取消不可见；删除经确认后走 ppt:delete 并成功 toast', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt-template:list') return { templates: {} }
      if (channel === 'ppt-template:jobs') {
        return { jobs: [{ ...baseJob, status: 'completed' }], has_more: false }
      }
      if (channel === 'tasks:getMessages') return { messages: [] }
      return {}
    })
    await expandJob()
    expect(screen.getByText('project.pptTpl.job.delete')).toBeTruthy()
    expect(screen.queryByText('project.pptTpl.job.cancel')).toBeNull()
    fireEvent.click(screen.getByText('project.pptTpl.job.delete'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('ppt:delete', 'job-1')
    expect(toastMock).toHaveBeenCalledWith('project.pptTpl.job.deleted', 'success')
  })

  it('paused：继续与删除并存、无取消', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt-template:list') return { templates: {} }
      if (channel === 'ppt-template:jobs') {
        return { jobs: [{ ...baseJob, status: 'paused' }], has_more: false }
      }
      if (channel === 'tasks:getMessages') return { messages: [] }
      return {}
    })
    await expandJob()
    expect(screen.getByText('project.pptTpl.job.resume')).toBeTruthy()
    expect(screen.getByText('project.pptTpl.job.delete')).toBeTruthy()
    expect(screen.queryByText('project.pptTpl.job.cancel')).toBeNull()
  })

  it('确认拒绝 → 不发起 IPC', async () => {
    confirmState.value = false
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt-template:list') return { templates: {} }
      if (channel === 'ppt-template:jobs') {
        return { jobs: [{ ...baseJob, status: 'completed' }], has_more: false }
      }
      if (channel === 'tasks:getMessages') return { messages: [] }
      return {}
    })
    await expandJob()
    fireEvent.click(screen.getByText('project.pptTpl.job.delete'))
    await flush()
    expect(invokeMock).not.toHaveBeenCalledWith('ppt:delete', 'job-1')
  })

  it('failed：错误行可见、删除可用', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'ppt-template:list') return { templates: {} }
      if (channel === 'ppt-template:jobs') {
        return { jobs: [{ ...baseJob, status: 'failed', error: '模型超时' }], has_more: false }
      }
      if (channel === 'tasks:getMessages') return { messages: [] }
      return {}
    })
    await expandJob()
    expect(screen.getByText('模型超时')).toBeTruthy()
    expect(screen.getByText('project.pptTpl.job.delete')).toBeTruthy()
    fireEvent.click(screen.getByText('project.pptTpl.job.delete'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('ppt:delete', 'job-1')
  })
})
