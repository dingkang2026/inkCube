/** tests/TemplateManageModal.test.tsx — 0.5 E2 T4:模板管理弹层。
 *  覆盖:内置行只读(无编辑/删除)、新建表单非空校验、保存成功 refresh+关表单、
 *  删除走 useConfirm、内置置顶 togglePin、关闭回调。 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ToastProvider } from '../src/features/knowledge/Toast'
import { ConfirmProvider } from '../src/components/ConfirmDialog'
import { TemplateManageModal } from '../src/features/task/TemplateManageModal'
import type { MergedTemplate } from '../src/features/task/promptTemplate'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

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

function renderModal(overrides: Partial<Parameters<typeof TemplateManageModal>[0]> = {}) {
  const props = {
    templates: [
      tpl({ id: 'builtin_sentiment', builtin: true, title: '企业舆情扫描' }),
      tpl({}),
    ],
    refresh: vi.fn(),
    save: vi.fn().mockResolvedValue(undefined),
    remove: vi.fn().mockResolvedValue(undefined),
    togglePin: vi.fn().mockResolvedValue(undefined),
    onClose: vi.fn(),
    ...overrides,
  }
  render(
    <ToastProvider>
      <ConfirmProvider>
        <TemplateManageModal {...props} />
      </ConfirmProvider>
    </ToastProvider>,
  )
  return props
}

describe('TemplateManageModal (0.5 E2 T4)', () => {
  it('列表含内置行（builtinReadonly 标记、无编辑/删除按钮）与自定义行（有）', () => {
    renderModal()
    expect(screen.getByText('task.tplMgmt.builtinReadonly')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'task.tplMgmt.editTpl' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'task.tplMgmt.delete' })).toHaveLength(1)
    // 置顶开关两种行都有
    expect(screen.getAllByRole('button', { name: 'task.tplMgmt.pinnedOnly' })).toHaveLength(2)
  })

  it('新建表单：标题为空时保存按钮 disabled', () => {
    renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'task.tplMgmt.newTpl' }))
    const save = screen.getByRole('button', { name: 'task.tplMgmt.save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
  })

  it('保存成功：save 携带草稿、refresh 回调、表单关闭回列表并出 toast', async () => {
    const props = renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'task.tplMgmt.newTpl' }))
    fireEvent.change(screen.getByLabelText('task.tplMgmt.title'), { target: { value: '华润舆情' } })
    fireEvent.change(screen.getByLabelText('task.tplMgmt.prompt'), { target: { value: '扫描{企业名}' } })
    fireEvent.click(screen.getByRole('button', { name: 'task.tplMgmt.save' }))
    await waitFor(() => expect(props.save).toHaveBeenCalledTimes(1))
    expect(props.save).toHaveBeenCalledWith({ category: 'info', title: '华润舆情', prompt: '扫描{企业名}' })
    await waitFor(() => expect(props.refresh).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('button', { name: 'task.tplMgmt.save' })).toBeNull()
    expect(screen.getByText('task.tplMgmt.saved')).toBeTruthy()
  })

  it('编辑自定义模板：save 回传 id 与当前 pinned（防 upsert 默认值覆盖置顶态）', async () => {
    const props = renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'task.tplMgmt.editTpl' }))
    const save = screen.getByRole('button', { name: 'task.tplMgmt.save' }) as HTMLButtonElement
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    await waitFor(() => expect(props.save).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'tpl_custom_a', pinned: false }),
    ))
  })

  it('删除自定义模板：确认后 remove+refresh；取消则不动', async () => {
    const props = renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'task.tplMgmt.delete' }))
    fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }))
    expect(props.remove).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'task.tplMgmt.delete' }))
    fireEvent.click(screen.getByRole('button', { name: 'common.confirm' }))
    await waitFor(() => expect(props.remove).toHaveBeenCalledWith('tpl_custom_a'))
    await waitFor(() => expect(props.refresh).toHaveBeenCalledTimes(1))
    expect(screen.getByText('task.tplMgmt.deleted')).toBeTruthy()
  })

  it('内置行仅置顶开关可用：togglePin(builtin_sentiment, true) 后 refresh', async () => {
    const props = renderModal()
    const pinBtns = screen.getAllByRole('button', { name: 'task.tplMgmt.pinnedOnly' })
    fireEvent.click(pinBtns[0])
    await waitFor(() => expect(props.togglePin).toHaveBeenCalledWith('builtin_sentiment', true))
    await waitFor(() => expect(props.refresh).toHaveBeenCalled())
  })

  it('右上角关闭按钮回调 onClose', () => {
    const props = renderModal()
    fireEvent.click(screen.getByRole('button', { name: 'common.close' }))
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })
})
