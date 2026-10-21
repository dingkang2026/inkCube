/** tests/TaskTemplatePanel.test.tsx — 0.5 E2 T4:面板接 useTaskTemplates 合并视图。
 *  hook mock 注入内置+自定义模板;验证卡片渲染、scope 传参、分类过滤、onPick 透传
 *  merged prompt、管理模板入口打开弹层。mergeTemplates 排序见 useTaskTemplates.test.ts。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToastProvider } from '../src/features/knowledge/Toast'
import { ConfirmProvider } from '../src/components/ConfirmDialog'
import { TaskTemplatePanel } from '../src/features/task/TaskTemplatePanel'
import { useTaskTemplates } from '../src/features/task/useTaskTemplates'
import type { MergedTemplate, TemplateDraft } from '../src/features/task/promptTemplate'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
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
    title: '自定义A',
    desc: '',
    prompt: 'P{企业名}',
    pinned: false,
    ...over,
  }
}

const fixtures: MergedTemplate[] = [
  tpl({
    id: 'builtin_sentiment', builtin: true,
    title: 'task.tpl.sentiment.title', desc: 'task.tpl.sentiment.desc', prompt: 'task.tpl.sentiment.prompt',
  }),
  tpl({ id: 'tpl_custom_a', category: 'mon' }),
  tpl({ id: 'tpl_custom_b', category: 'info', title: '自定义B' }),
]

const hookValue = (templates: MergedTemplate[]) => ({
  templates,
  loading: false,
  refresh: vi.fn(async () => {}),
  save: vi.fn(async (_d: TemplateDraft) => {}),
  remove: vi.fn(async (_id: string) => {}),
  togglePin: vi.fn(async (_id: string, _p: boolean) => {}),
}) as unknown as ReturnType<typeof useTaskTemplates>

function renderPanel(onPick = vi.fn()) {
  render(
    <ToastProvider>
      <ConfirmProvider>
        <TaskTemplatePanel onPick={onPick} />
      </ConfirmProvider>
    </ToastProvider>,
  )
  return onPick
}

describe('TaskTemplatePanel (0.5 E2 T4 接持久化)', () => {
  beforeEach(() => {
    mockedHook.mockReturnValue(hookValue(fixtures))
  })

  it('渲染 hook 合并视图（内置 + 自定义），scope 默认 global', () => {
    renderPanel()
    expect(mockedHook).toHaveBeenCalledWith('global', undefined)
    expect(screen.getByText('task.tpl.sentiment.title')).toBeTruthy()
    expect(screen.getByText('自定义A')).toBeTruthy()
    expect(screen.getByText('自定义B')).toBeTruthy()
  })

  it('scope/projectId props 透传 hook（项目对话空态复用）', () => {
    render(
      <ToastProvider>
        <ConfirmProvider>
          <TaskTemplatePanel onPick={() => {}} scope="project" projectId="p1" />
        </ConfirmProvider>
      </ToastProvider>,
    )
    expect(mockedHook).toHaveBeenCalledWith('project', 'p1')
  })

  it('点「舆情监控」tab 只剩 mon 分类卡', () => {
    renderPanel()
    fireEvent.click(screen.getByText('task.cat.mon'))
    expect(screen.getByText('task.tpl.sentiment.title')).toBeTruthy()
    expect(screen.getByText('自定义A')).toBeTruthy()
    expect(screen.queryByText('自定义B')).toBeNull()
  })

  it('点卡回调 onPick(tpl.prompt)（只预填不发送）', () => {
    const onPick = renderPanel()
    fireEvent.click(screen.getByText('task.tpl.sentiment.title'))
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith('task.tpl.sentiment.prompt')
  })

  it('底部「管理模板」按钮打开管理弹层', () => {
    renderPanel()
    fireEvent.click(screen.getByText('task.tplMgmt.manage'))
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getAllByText('自定义A').length).toBeGreaterThanOrEqual(2) // 面板卡 + 弹层行
  })
})
