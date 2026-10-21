/** tests/SkillManagement.test.tsx — builtin 徽标与卸载/禁用保护（2.8 §9.1）
 *  覆盖：builtin skill 显示「内置」徽标、无卸载按钮、开关置灰不可禁用
 *  （与后端 DELETE/toggle 拒绝双保险，2.8 §11）；外部 skill 维持卸载入口
 *  且删除走确认框；安装表单归右栏（2026-10-19 左右两栏语义：左 tools 右 skills）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/settings/SkillManagement.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

const confirmMock = vi.hoisted(() => vi.fn())
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))

// 稳定引用（返回新 vi.fn() 会因 useCallback 依赖失效触发无限重渲染）
const handleErrorMock = vi.hoisted(() => vi.fn())
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/hooks/useErrorHandler', () => ({
  useErrorHandler: () => handleErrorMock,
}))

// 列表加载失败提示：组件渲染期直接调用 useToast（原先仅操作回调间接经 useErrorHandler）→ 需 mock 掉
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))

import { SkillManagement } from '../src/features/settings/SkillManagement'

const invokeMock = vi.fn()

const mkSkill = (name: string, sourceType: string) => ({
  id: name,
  name,
  description: '',
  source_type: sourceType,
  source_path: '',
  install_path: '',
  enabled: true,
  needs_confirm: false,
  permissions: [],
  created_at: '',
})

const cardOf = (name: string) => {
  const card = screen.getByText(name).closest('.card')
  expect(card).toBeTruthy()
  return card as HTMLElement
}

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'skills:list') {
      return { skills: [mkSkill('browser-fetch', 'builtin'), mkSkill('my-skill', 'git')] }
    }
    if (channel === 'skills:builtinTools') return { tools: [] }
    return {}
  })
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(true)
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('SkillManagement builtin protection (2.8 §9.1)', () => {
  it('marks the builtin skill with a badge and hides its delete button', async () => {
    render(<SkillManagement />)
    await screen.findByText('browser-fetch')

    const card = cardOf('browser-fetch')
    expect(card.querySelector('.builtinBadge')).toBeTruthy()
    expect(card.querySelector('[title="skills.delete"]')).toBeNull()
  })

  it('keeps the delete button (no builtin badge) for external skills', async () => {
    render(<SkillManagement />)
    await screen.findByText('my-skill')

    const card = cardOf('my-skill')
    expect(card.querySelector('.builtinBadge')).toBeNull()
    expect(card.querySelector('[title="skills.delete"]')).toBeTruthy()
  })

  it('disables the builtin skill toggle and keeps external toggles active', async () => {
    render(<SkillManagement />)
    await screen.findByText('browser-fetch')

    const builtinToggle = cardOf('browser-fetch').querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(builtinToggle.disabled).toBe(true)
    // 置灰提示 title 挂在包裹 label 上（整块可感知）
    expect(builtinToggle.closest('label')!.getAttribute('title')).toContain('skills.builtinProtected')

    const externalToggle = cardOf('my-skill').querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(externalToggle.disabled).toBe(false)
  })

  it('renders the install form inside the skills column (right)', async () => {
    const { container } = render(<SkillManagement />)
    await screen.findByText('browser-fetch')

    // 安装表单位于右栏（skills.listTitle 标题所在 column）内，而非两栏之外的页首
    const columns = container.querySelectorAll('.column')
    expect(columns).toHaveLength(2)
    const rightColumn = columns[1]
    expect(rightColumn.querySelector('.installForm')).toBeTruthy()
    expect(rightColumn.textContent).toContain('skills.listTitle')
  })

  it('deletes an external skill after confirmation', async () => {
    render(<SkillManagement />)
    await screen.findByText('my-skill')

    const del = cardOf('my-skill').querySelector('[title="skills.delete"]') as HTMLElement
    fireEvent.click(del)

    await waitFor(() => expect(confirmMock).toHaveBeenCalled())
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('skills:delete', 'my-skill'))
  })
})
