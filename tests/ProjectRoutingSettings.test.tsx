/** tests/ProjectRoutingSettings.test.tsx — 项目模型路由档位化（0.3 §4.4 五组导航 2026-10-19）。
 *  用例自 ModelRoutingSettings.test.tsx 迁入并改造为档位制：
 *  12 个路由键下拉选档（standard/high），resolved 槽位名作副文本。 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: Record<string, unknown>) =>
    opts && 'model' in opts ? `${k}:${opts.model}` : k }),
}))
vi.mock('../src/features/settings/routingFields.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { ProjectRoutingSettings } from '../src/features/settings/ProjectRoutingSettings'
import type { LLMSettings } from '../src/types'

afterEach(() => cleanup())

const baseForm = {
  api_url: '', api_key: '',
  standard_model: 'glm-5.3-flash', high_model: 'glm-5.3',
  temperature: 0.7, max_tokens: 4096, context_window: 0,
  top_p: 0.9, reasoning_effort: 'low', reasoning_override: '', system_prompt: '',
  tavily_key: '', vlm_api_url: '', vlm_api_key: '', vlm_model: '',
  max_concurrent_file_parsing: 2, max_concurrent_image_description: 2,
  max_compile_concurrency: 2, llm_connect_timeout: 30, llm_read_timeout: 120,
  task_model: 'standard', task_planner_model: '',
  wiki_ingest_model: 'standard', wiki_lint_model: 'standard', wiki_dedup_model: 'standard',
  wiki_ledger_parse_model: 'standard', wiki_enrich_model: 'standard',
  report_model: 'standard', report_extract_model: 'standard',
  form_analyze_model: 'standard', form_fill_model: 'standard', rag_model: 'standard',
  ppt_model: 'high',
  meeting_note_model: 'standard',
  task_timeout: 300, task_max_iterations: 60, task_auto_confirm_shell: false,
  task_shell_confirm_mode: 'auto', task_shell_domain_whitelist: '',
  task_show_execution_log: true, task_workspace_max_mb: 1024,
  task_max_count: 50, task_auto_archive_days: 30,
} as LLMSettings

describe('ProjectRoutingSettings — 12 键档位下拉', () => {
  it('renders both routing sections', () => {
    const { container } = render(<ProjectRoutingSettings form={baseForm} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.wikiRoutingSection')
    expect(container.textContent).toContain('settings.pipelineRoutingSection')
    expect(container.textContent).toContain('settings.routingFallbackHint')
  })

  it('renders 12 level selects with tier values from form', () => {
    const { container } = render(<ProjectRoutingSettings form={baseForm} onUpdate={vi.fn()} />)
    const selects = container.querySelectorAll('select')
    expect(selects).toHaveLength(12)
    // ppt 默认高配：第 11 个 select（业务管线末位第二）值为 high
    expect((selects[10] as HTMLSelectElement).value).toBe('high')
    // meeting_note（末位，2.13）默认 standard
    expect((selects[11] as HTMLSelectElement).value).toBe('standard')
    // 其余默认 standard
    expect((selects[0] as HTMLSelectElement).value).toBe('standard')
  })

  it('shows resolved slot model name as subtext', () => {
    const { container } = render(<ProjectRoutingSettings form={baseForm} onUpdate={vi.fn()} />)
    // standard 档解析到标准槽
    expect(container.textContent).toContain('settings.level.current:glm-5.3-flash')
  })

  it('resolves high tier to high slot, falling back when empty', () => {
    // 高配槽清空 → 回退标准槽（与后端 resolve_model 互回退规则一致）
    const { container } = render(
      <ProjectRoutingSettings form={{ ...baseForm, high_model: '' }} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.level.current:glm-5.3-flash')
  })

  it('emits onUpdate with tier value on select change', () => {
    const onUpdate = vi.fn()
    const { container } = render(<ProjectRoutingSettings form={baseForm} onUpdate={onUpdate} />)
    const label = [...container.querySelectorAll('label')].find((l) => l.textContent === 'settings.reportModel')
    expect(label).toBeTruthy()
    const select = label!.parentElement!.querySelector('select') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'high' } })
    expect(onUpdate).toHaveBeenCalledWith('report_model', 'high')
  })
})
