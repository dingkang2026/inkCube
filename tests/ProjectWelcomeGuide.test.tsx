import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ProjectWelcomeGuide } from '../src/features/knowledge/ProjectWelcomeGuide'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) => (o ? `${k}:${JSON.stringify(o)}` : k) }),
}))

describe('ProjectWelcomeGuide (0.5 E1 T6)', () => {
  const baseProps = () => ({ onCreate: vi.fn().mockResolvedValue({ id: 'p1' }) })

  it('渲染标题与三张类型卡（含场景描述）', () => {
    render(<ProjectWelcomeGuide {...baseProps()} />)
    expect(screen.getByText('guide.title')).toBeTruthy()
    expect(screen.getByText('guide.typeRecruit')).toBeTruthy()
    expect(screen.getByText('guide.typeInvest')).toBeTruthy()
    expect(screen.getByText('guide.typeGeneral')).toBeTruthy()
    expect(screen.getByText('guide.typeRecruitDesc')).toBeTruthy()
  })

  it('未选类型时命名，创建按钮仍 disabled；选类型后 enabled', () => {
    render(<ProjectWelcomeGuide {...baseProps()} />)
    const nameInput = screen.getByPlaceholderText('guide.namePlaceholder')
    fireEvent.change(nameInput, { target: { value: '华东智能制造产业招商' } })
    const createBtn = screen.getByRole('button', { name: 'guide.create' }) as HTMLButtonElement
    expect(createBtn.disabled).toBe(true)
    fireEvent.click(screen.getByText('guide.typeRecruit'))
    expect(createBtn.disabled).toBe(false)
  })

  it('类型卡点击出现选中态（aria-pressed）', () => {
    render(<ProjectWelcomeGuide {...baseProps()} />)
    const card = screen.getByText('guide.typeInvest').closest('button') as HTMLButtonElement
    expect(card.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(card)
    expect(card.getAttribute('aria-pressed')).toBe('true')
  })

  it('点击创建：onCreate 以 (name, undefined, type) 调用', async () => {
    const onCreate = vi.fn().mockResolvedValue({ id: 'p1' })
    render(<ProjectWelcomeGuide onCreate={onCreate} />)
    fireEvent.click(screen.getByText('guide.typeGeneral'))
    fireEvent.change(screen.getByPlaceholderText('guide.namePlaceholder'), { target: { value: '通用资料库' } })
    fireEvent.click(screen.getByRole('button', { name: 'guide.create' }))
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('通用资料库', undefined, 'general'))
  })

  it('创建中防止重复提交（creating 守卫）', async () => {
    let resolveCreate: (v: { id: string }) => void = () => {}
    const onCreate = vi.fn().mockImplementation(() => new Promise<{ id: string }>((r) => { resolveCreate = r }))
    render(<ProjectWelcomeGuide onCreate={onCreate} />)
    fireEvent.click(screen.getByText('guide.typeGeneral'))
    fireEvent.change(screen.getByPlaceholderText('guide.namePlaceholder'), { target: { value: '通用资料库' } })
    const createBtn = screen.getByRole('button', { name: 'guide.create' }) as HTMLButtonElement
    fireEvent.click(createBtn)
    expect(createBtn.disabled).toBe(true)
    resolveCreate({ id: 'p1' })
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
  })
})
