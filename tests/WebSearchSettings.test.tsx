/** tests/WebSearchSettings.test.tsx — 设置 > 基础配置 > 网络搜索（2026-10-19 供应商化）。
 *  覆盖：provider 二选一无默认（空 = 未配置）；tavily/zhipu 分支字段渲染；
 *  Key 输入与引擎档位 onUpdate；双 Key 并存切换不丢值。 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/settings/WebSearchSettings.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { WebSearchSettings } from '../src/features/settings/WebSearchSettings'
import type { LLMSettings } from '../src/types'

afterEach(() => cleanup())

const baseForm = {
  api_url: '', api_key: '', standard_model: '', high_model: '',
  temperature: 0.7, max_tokens: 4096, context_window: 0, top_p: 0.9,
  reasoning_effort: '', reasoning_override: '', system_prompt: '',
  tavily_key: '', web_search_provider: '', zhipu_search_key: '', zhipu_search_engine: 'search_pro',
  vlm_api_url: '', vlm_api_key: '', vlm_model: '',
  max_concurrent_file_parsing: 2, max_concurrent_image_description: 2,
  max_compile_concurrency: 2, llm_connect_timeout: 30, llm_read_timeout: 120,
  task_model: 'standard', task_planner_model: '',
  wiki_ingest_model: 'standard', wiki_lint_model: 'standard', wiki_dedup_model: 'standard',
  wiki_ledger_parse_model: 'standard', wiki_enrich_model: 'standard',
  report_model: 'standard', report_extract_model: 'standard',
  form_analyze_model: 'standard', form_fill_model: 'standard', rag_model: 'standard',
  ppt_model: 'high',
  task_timeout: 300, task_max_iterations: 60, task_auto_confirm_shell: false,
  task_shell_confirm_mode: 'auto', task_shell_domain_whitelist: '',
  task_show_execution_log: true, task_workspace_max_mb: 1024,
  task_max_count: 50, task_auto_archive_days: 30,
} as LLMSettings

const radio = (container: HTMLElement, value: string) =>
  container.querySelector(`input[name="web_search_provider"][value="${value}"]`) as HTMLInputElement

describe('WebSearchSettings provider 二选一（无默认）', () => {
  it('renders two provider cards with none selected when provider is empty', () => {
    const { container } = render(<WebSearchSettings form={baseForm} onUpdate={vi.fn()} />)

    expect(container.textContent).toContain('settings.webSearch.title')
    expect(radio(container, 'tavily')).toBeTruthy()
    expect(radio(container, 'zhipu')).toBeTruthy()
    expect(radio(container, 'tavily').checked).toBe(false)
    expect(radio(container, 'zhipu').checked).toBe(false)
    // 未选供应商：两个分支的 Key 输入段都不渲染
    expect(container.querySelector('#web-search-key-input')).toBeNull()
    expect(container.querySelector('#zhipu-engine-select')).toBeNull()
  })

  it('selecting tavily reveals its key input and emits provider update', () => {
    const onUpdate = vi.fn()
    const { container } = render(<WebSearchSettings form={baseForm} onUpdate={onUpdate} />)

    fireEvent.click(radio(container, 'tavily'))
    expect(onUpdate).toHaveBeenCalledWith('web_search_provider', 'tavily')
  })

  it('selecting zhipu reveals key input and engine select with default search_pro', () => {
    const onUpdate = vi.fn()
    const { container } = render(
      <WebSearchSettings form={{ ...baseForm, web_search_provider: 'zhipu' }} onUpdate={onUpdate} />,
    )

    expect(radio(container, 'zhipu').checked).toBe(true)
    const keyInput = container.querySelector('#web-search-key-input') as HTMLInputElement
    expect(keyInput).toBeTruthy()
    expect(keyInput.value).toBe('')

    const select = container.querySelector('#zhipu-engine-select') as HTMLSelectElement
    expect(select.value).toBe('search_pro')

    fireEvent.change(select, { target: { value: 'search_std' } })
    expect(onUpdate).toHaveBeenCalledWith('zhipu_search_engine', 'search_std')
  })
})

describe('WebSearchSettings Key 输入与双 Key 并存', () => {
  it('emits tavily key updates from the tavily branch', () => {
    const onUpdate = vi.fn()
    const { container } = render(
      <WebSearchSettings form={{ ...baseForm, web_search_provider: 'tavily', tavily_key: 'tvly-1' }} onUpdate={onUpdate} />,
    )

    const keyInput = container.querySelector('#web-search-key-input') as HTMLInputElement
    expect(keyInput.value).toBe('tvly-1')
    fireEvent.change(keyInput, { target: { value: 'tvly-2' } })
    expect(onUpdate).toHaveBeenCalledWith('tavily_key', 'tvly-2')
    // tavily 分支不渲染引擎下拉
    expect(container.querySelector('#zhipu-engine-select')).toBeNull()
  })

  it('shows each provider key value without cross-contamination (双 Key 并存)', () => {
    const form = {
      ...baseForm,
      web_search_provider: 'zhipu',
      tavily_key: 'tvly-keep',
      zhipu_search_key: 'zp-keep',
    }
    const onUpdate = vi.fn()
    const { container } = render(<WebSearchSettings form={form} onUpdate={onUpdate} />)

    // zhipu 分支显示智谱 Key，tavily Key 仍在 form 中（切换供应商不丢）
    const keyInput = container.querySelector('#web-search-key-input') as HTMLInputElement
    expect(keyInput.value).toBe('zp-keep')
    expect(onUpdate).not.toHaveBeenCalled()
  })
})
