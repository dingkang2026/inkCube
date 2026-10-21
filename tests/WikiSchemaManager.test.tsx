/** tests/WikiSchemaManager.test.tsx — Schema 管理
 *
 *  台账页开关(3.0 四期 T10:checkbox/回填/透传)。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { WikiSchemaManager } from '../src/features/project/tabs/WikiSchemaManager'

const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve() }) }

const invokeMock = vi.fn()
beforeEach(() => {
  invokeMock.mockReset()
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

const renderView = async () => {
  const r = render(<WikiSchemaManager projectId="p1" />)
  await flush()
  return r
}

const rowEditBtns = (container: HTMLElement) =>
  ([...container.querySelectorAll('button[class*="editBtn"]')] as HTMLButtonElement[])
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
