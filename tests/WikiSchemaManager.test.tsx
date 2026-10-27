/** tests/WikiSchemaManager.test.tsx — 知识框架视图（结构页 schema 管理 + Wiki 目标区块）
 *
 *  台账页开关(3.0 四期 T10:checkbox/回填/透传);Wiki 目标编辑(2026-10-26 二次收敛自首页迁入:
 *  wiki:updatePurpose 保存 + onMetaSaved 父级回流 + 失败 toast)。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
// Wiki 目标区块（二次收敛迁入）消费 useToast + Markdown，统一打桩
const toastMock = vi.hoisted(() => vi.fn())
vi.mock('../src/features/knowledge/Toast', () => ({ useToast: () => toastMock }))
vi.mock('../src/components/Markdown', () => ({ Markdown: () => null }))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { WikiSchemaManager } from '../src/features/project/tabs/WikiSchemaManager'

const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve() }) }

const invokeMock = vi.fn()
beforeEach(() => {
  invokeMock.mockReset()
  toastMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'wiki:getSchemas') {
      return {
        project_type: 'recruitment',
        schemas: [
          { id: 'wiki_schema_recruitment_milestones', project_type: 'recruitment', page_key: 'milestones',
            title: '里程碑台账', compile_hint: '', base_lib_search_hints: [], position: 1, is_ledger: 1 },
          { id: 'wiki_schema_recruitment_profile', project_type: 'recruitment', page_key: 'profile',
            title: '企业画像', compile_hint: '', base_lib_search_hints: [], position: 2, is_ledger: 0 },
        ],
      }
    }
    return { success: true, schema: {} }
  })
  ;(window as any).electronAPI = { invoke: invokeMock }
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

const renderView = async (extra: { purpose?: string; onMetaSaved?: () => void } = {}) => {
  const r = render(
    <WikiSchemaManager projectId="p1" purpose={extra.purpose ?? ''} onMetaSaved={extra.onMetaSaved} />,
  )
  await flush()
  return r
}

// schema 行编辑按钮：purpose 区块（二次收敛迁入）也有同款 editBtn，须收窄到 schemaList 内
const rowEditBtns = (container: HTMLElement) =>
  ([...container.querySelector('[class*="schemaList"]')!.querySelectorAll('button[class*="editBtn"]')] as HTMLButtonElement[])
    .filter((b) => b.textContent === 'project.wiki.edit')

describe('WikiSchemaManager is_ledger switch (3.0 四期 T10)', () => {
  it('shows ledger badge only on is_ledger rows', async () => {
    const { container } = await renderView()
    expect(container.textContent).toContain('里程碑台账')
    const badges = container.querySelectorAll('span[class*="schemaLedgerBadge"]')
    expect(badges).toHaveLength(1)
    expect(badges[0].textContent).toBe('project.wiki.schemaLedgerBadge')
  })

  it('create with checkbox checked passes isLedger=true', async () => {
    const { container, getByText } = await renderView()
    fireEvent.click(getByText('project.wiki.schemaAdd'))
    // 表单内文本 input 顺序:page_key/title/position/hintsText(另有 1 个 checkbox)
    const textInputs = [...container.querySelectorAll('input[class*="schemaFormInput"]')] as HTMLInputElement[]
    expect(textInputs.length).toBe(4)
    fireEvent.change(textInputs[0], { target: { value: 'my_ledger' } })   // page_key
    fireEvent.change(textInputs[1], { target: { value: '我的台账' } })     // title
    const checkbox = container.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    fireEvent.click(checkbox)
    expect(checkbox.checked).toBe(true)
    fireEvent.click(getByText('project.wiki.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('wiki:createSchema', expect.objectContaining({
      projectId: 'p1', pageKey: 'my_ledger', title: '我的台账', isLedger: true,
    }))
  })

  it('edit backfills checkbox from row and passes isLedger on save', async () => {
    const { getByText, container } = await renderView()
    // 第一行(里程碑台账,is_ledger=1)的编辑按钮
    const editBtns = rowEditBtns(container)
    expect(editBtns.length).toBe(2)
    fireEvent.click(editBtns[0])
    const checkbox = container.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(checkbox.checked).toBe(true)   // startEdit 从 schema.is_ledger 回填
    fireEvent.click(getByText('project.wiki.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('wiki:updateSchema', expect.objectContaining({
      projectId: 'p1', schemaId: 'wiki_schema_recruitment_milestones', isLedger: true,
    }))
  })
})

describe('WikiSchemaManager Wiki 目标区块（2026-10-26 二次收敛自首页迁入）', () => {
  it('shows the empty placeholder when purpose is blank', async () => {
    const { container } = await renderView()
    expect(container.textContent).toContain('project.wiki.purpose')
    expect(container.textContent).toContain('project.wiki.noPurpose')
  })

  it('saves edits via wiki:updatePurpose and notifies parent (props 回流)', async () => {
    const onMetaSaved = vi.fn()
    const { container, getByText } = await renderView({ purpose: '旧目标', onMetaSaved })
    // 展示态：有目标时无空占位
    expect(container.textContent).not.toContain('project.wiki.noPurpose')
    // purpose 区块编辑按钮是全容器第一个 editBtn（DOM 顺序：purpose 区块 → schema 区块）
    fireEvent.click(container.querySelector('button[class*="editBtn"]')!)
    const textarea = container.querySelector('textarea[class*="editTextarea"]') as HTMLTextAreaElement
    expect(textarea.value).toBe('旧目标')   // 草稿回填自 props
    fireEvent.change(textarea, { target: { value: '新目标' } })
    fireEvent.click(getByText('project.wiki.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('wiki:updatePurpose', { projectId: 'p1', purpose: '新目标' })
    expect(onMetaSaved).toHaveBeenCalledTimes(1)
    // 保存成功回到展示态
    expect(container.querySelector('textarea[class*="editTextarea"]')).toBeNull()
  })

  it('toasts saveMetaFailed and keeps the editor open on failure', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:getSchemas') return { project_type: 'x', schemas: [] }
      if (channel === 'wiki:updatePurpose') throw new Error('boom')
      return {}
    })
    const { container, getByText } = await renderView({ purpose: '旧目标' })
    fireEvent.click(container.querySelector('button[class*="editBtn"]')!)
    fireEvent.change(container.querySelector('textarea[class*="editTextarea"]')!, { target: { value: '新目标' } })
    fireEvent.click(getByText('project.wiki.save'))
    await flush()
    // i18n mock 只透 key，断言通道与级别
    expect(toastMock).toHaveBeenCalledWith('project.wiki.saveMetaFailed', 'error')
    // 失败不关编辑器（输入保留可重试）
    expect(container.querySelector('textarea[class*="editTextarea"]')).not.toBeNull()
  })
})
