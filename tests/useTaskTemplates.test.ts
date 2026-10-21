/** tests/useTaskTemplates.test.ts — 0.5 E2 T4:mergeTemplates 合并排序与内置 i18n 解析。
 *  hook 本体是 taskTemplates:* 四通道的薄封装,契约由 TemplateManageModal 交互测试覆盖。 */
import { describe, it, expect } from 'vitest'
import {
  TASK_TEMPLATES,
  BUILTIN_PREFIX,
  mergeTemplates,
} from '../src/features/task/promptTemplate'
import type { CustomRow } from '../src/features/task/promptTemplate'

const t = (key: string) => key

function row(over: Partial<CustomRow>): CustomRow {
  return {
    id: 'tpl_custom_x',
    scope: 'global',
    project_id: null,
    category: 'monitor',
    title: '自定义',
    prompt: 'P',
    pinned: 0,
    created_at: '',
    updated_at: '',
    ...over,
  }
}

describe('mergeTemplates', () => {
  const custom = [
    row({ id: 'c1', pinned: 1, updated_at: '2026-01-01T00:00:00' }),
    row({ id: 'c2', pinned: 1, updated_at: '2026-01-02T00:00:00' }),
    row({ id: 'c3', pinned: 0, updated_at: '2026-01-04T00:00:00' }),
    row({ id: 'c4', pinned: 0, updated_at: '2026-01-03T00:00:00' }),
  ]
  // industryEvents 置顶（内置置顶记录行的 id 形态）
  const merged = mergeTemplates(TASK_TEMPLATES, t, custom, ['tpl_builtin_industryEvents'])

  it('置顶优先：内置置顶在前，自定义 pinned 按 updated_at 倒序次之', () => {
    expect(merged[0].id).toBe(`${BUILTIN_PREFIX}industryEvents`)
    expect(merged[0].pinned).toBe(true)
    expect(merged[1].id).toBe('c2')
    expect(merged[2].id).toBe('c1')
  })

  it('非置顶：内置保持自然序在前，自定义按 updated_at 倒序收尾', () => {
    expect(merged[3].id).toBe(`${BUILTIN_PREFIX}companyBasic`)
    expect(merged[3].pinned).toBe(false)
    // 内置共 10 条，1 条置顶 → 非置顶内置占下标 3..11
    expect(merged[11].id).toBe(`${BUILTIN_PREFIX}dataSummary`)
    expect(merged[12].id).toBe('c3')
    expect(merged[13].id).toBe('c4')
  })

  it('合并总数 = 内置 10 + 自定义 N', () => {
    expect(merged).toHaveLength(TASK_TEMPLATES.length + custom.length)
  })

  it('内置 title/prompt 经 t 解析（恒等 t 下即 i18n 键名）', () => {
    const sentiment = merged.find((m) => m.id === `${BUILTIN_PREFIX}sentiment`)
    expect(sentiment).toBeTruthy()
    expect(sentiment!.builtin).toBe(true)
    expect(sentiment!.title).toBe('task.tpl.sentiment.title')
    expect(sentiment!.prompt).toBe('task.tpl.sentiment.prompt')
  })

  it('自定义 category 按别名映射到面板 tab 分类，未知分类兜底 info', () => {
    const rep = mergeTemplates(TASK_TEMPLATES, t, [row({ id: 'r1', category: 'report_invest' })], [])
    expect(rep.find((m) => m.id === 'r1')!.category).toBe('rep')
    const fallback = mergeTemplates(TASK_TEMPLATES, t, [row({ id: 'r2', category: 'whatever' })], [])
    expect(fallback.find((m) => m.id === 'r2')!.category).toBe('info')
  })

  it('自定义 desc 取 prompt 前 50 字符', () => {
    const out = mergeTemplates(TASK_TEMPLATES, t, [row({ id: 'r3', prompt: '长'.repeat(80) })], [])
    expect(out.find((m) => m.id === 'r3')!.desc).toBe('长'.repeat(50))
  })
})
