/** tests/SkillManagement.test.tsx — builtin 徽标与卸载保护（2.8 §9.1）
 *  覆盖：builtin skill 显示「内置」徽标且无卸载按钮（与后端 DELETE 拒绝
 *  双保险，2.8 §11）；外部 skill 维持卸载入口且删除走确认框。
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

  it('deletes an external skill after confirmation', async () => {
    render(<SkillManagement />)
    await screen.findByText('my-skill')

    const del = cardOf('my-skill').querySelector('[title="skills.delete"]') as HTMLElement
    fireEvent.click(del)

    await waitFor(() => expect(confirmMock).toHaveBeenCalled())
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('skills:delete', 'my-skill'))
  })
})
