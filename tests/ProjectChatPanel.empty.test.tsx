/** tests/ProjectChatPanel.empty.test.tsx — 0.5 E2 T6:项目对话空态复用任务模板面板。
 *  空态渲染 TaskTemplatePanel(scope='project')；点卡预填输入框，企业类占位符(zh/en)
 *  被替换为项目名，其余变量在占位符表单按名去重填写、填一次全量替换（0.5 §4.4）；
 *  有消息后模板区消失。 */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ProjectChatPanel } from '../src/features/project/ProjectChatPanel'
import { useTaskTemplates } from '../src/features/task/useTaskTemplates'
import type { MergedTemplate, TemplateDraft } from '../src/features/task/promptTemplate'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => vi.fn(),
}))

vi.mock('../src/features/task/useTaskTemplates', () => ({
  useTaskTemplates: vi.fn(),
}))

const mockedHook = vi.mocked(useTaskTemplates)

function tpl(over: Partial<MergedTemplate>): MergedTemplate {
  return {
    id: 'tpl_custom_a',
    builtin: false,
    category: 'mon',
    title: '舆情模板',
    desc: '',
    prompt: '',
    pinned: false,
    ...over,
  }
}

const fixtures: MergedTemplate[] = [
  tpl({ id: 'tpl_custom_a', title: '舆情模板', prompt: '请收集「{企业名}」的舆情' }),
  tpl({ id: 'tpl_custom_b', title: '尽调模板', prompt: '基于「{企业名}」与「{关注点}」生成清单' }),
  tpl({ id: 'tpl_custom_c', title: '重复变量模板', prompt: '扫描「{企业名}」舆情，输出「{企业名}」报告' }),
]

const hookValue = (templates: MergedTemplate[]) => ({
  templates,
  loading: false,
  refresh: vi.fn(async () => {}),
  save: vi.fn(async (_d: TemplateDraft) => {}),
  remove: vi.fn(async (_id: string) => {}),
  togglePin: vi.fn(async (_id: string, _p: boolean) => {}),
}) as unknown as ReturnType<typeof useTaskTemplates>

function renderPanel() {
  render(<ProjectChatPanel projectId="p1" projectName="华东智造" isOpen />)
}

describe('ProjectChatPanel 空态模板（0.5 E2 T6）', () => {
  beforeAll(() => {
    // jsdom 未实现 scrollIntoView（消息列表滚动用）
    Element.prototype.scrollIntoView = vi.fn()
  })

  beforeEach(() => {
    mockedHook.mockReturnValue(hookValue(fixtures))
    // 永不 resolve：user 消息同步入列即可断言，避免异步 setState 触发 act 警告
    ;(window as unknown as { electronAPI: unknown }).electronAPI = {
      invoke: vi.fn(() => new Promise(() => {})),
    }
  })

  it('空态渲染模板面板（scope=project + projectId 透传 hook）', () => {
    renderPanel()
    expect(mockedHook).toHaveBeenCalledWith('project', 'p1')
    expect(screen.getByText('project.chat.emptyHint')).toBeTruthy()
    expect(screen.getByText('舆情模板')).toBeTruthy()
    expect(screen.getByText('尽调模板')).toBeTruthy()
  })

  it('点卡预填输入框，{企业名} 替换为项目名', () => {
    renderPanel()
    fireEvent.click(screen.getByText('舆情模板'))
    const input = screen.getByPlaceholderText('knowledge.chatInputPlaceholder') as HTMLInputElement
    expect(input.value).toBe('请收集「华东智造」的舆情')
  })

  it('其余占位符保留原文，表单按名去重填写、填一次全量替换', () => {
    renderPanel()
    fireEvent.click(screen.getByText('尽调模板'))
    const input = screen.getByPlaceholderText('knowledge.chatInputPlaceholder') as HTMLInputElement
    expect(input.value).toBe('基于「华东智造」与「{关注点}」生成清单')
    // 表单只剩去重后的 {关注点} 字段（企业类已预填项目名）
    const field = screen.getByLabelText('关注点') as HTMLInputElement
    fireEvent.change(field, { target: { value: '股权结构' } })
    expect(input.value).toBe('基于「华东智造」与「股权结构」生成清单')
  })

  it('企业类占位符重复出现：表单零字段（全部已预填项目名）', () => {
    renderPanel()
    fireEvent.click(screen.getByText('重复变量模板'))
    const input = screen.getByPlaceholderText('knowledge.chatInputPlaceholder') as HTMLInputElement
    expect(input.value).toBe('扫描「华东智造」舆情，输出「华东智造」报告')
    expect(screen.queryByLabelText('企业名')).toBeNull()
  })

  it('有消息后模板区与空态提示消失', () => {
    renderPanel()
    const input = screen.getByPlaceholderText('knowledge.chatInputPlaceholder') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'hi' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('hi')).toBeTruthy() // user 消息已入列
    expect(screen.queryByText('project.chat.emptyHint')).toBeNull()
    expect(screen.queryByText('舆情模板')).toBeNull()
  })
})
