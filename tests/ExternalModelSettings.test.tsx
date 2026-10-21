/** tests/ExternalModelSettings.test.tsx — 设置页外部模型 section（连接配置）。
 *  模型路由用例已迁至 ProjectRoutingSettings / TaskModelSettings 测试
 *  （0.3 §4.4 五组导航重组：连接与路由分离）。
 *  本文件覆盖：模型档位两槽（2026-10-19 档位制）+ context window 预算 UI
 *  （2026-10-15）+ LLM 超时（自通用 section 移入）。 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
vi.mock('../src/features/settings/ExternalModelSettings.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { ExternalModelSettings } from '../src/features/settings/ExternalModelSettings'
import type { LLMSettings } from '../src/types'

afterEach(() => cleanup())

const baseForm = {
  api_url: '', api_key: '',
  standard_model: 'glm-5.3-flash', high_model: 'glm-5.3',
  temperature: 0.7, max_tokens: 4096, context_window: 0,
  top_p: 0.9, reasoning_effort: 'low', reasoning_override: '', system_prompt: '',
  tavily_key: '', vlm_api_url: '', vlm_api_key: '', vlm_model: 'glm-4.6v',
  max_concurrent_file_parsing: 2, max_concurrent_image_description: 2,
  max_compile_concurrency: 2, llm_connect_timeout: 30, llm_read_timeout: 120,
  task_model: 'standard', task_planner_model: '',
  wiki_ingest_model: 'standard', wiki_lint_model: 'standard', wiki_dedup_model: 'standard',
  wiki_ledger_parse_model: 'standard', wiki_enrich_model: 'standard',
  report_model: 'standard', report_extract_model: 'standard',
  form_analyze_model: 'standard', form_fill_model: 'standard', rag_model: 'standard',
  ppt_model: 'high',
  task_timeout: 300, task_max_iterations: 60, task_auto_confirm_shell: false,
  task_shell_confirm_mode: 'smart', task_shell_domain_whitelist: '',
  task_show_execution_log: true, task_workspace_max_mb: 1024,
  task_max_count: 50, task_auto_archive_days: 30,
} as LLMSettings

describe('ExternalModelSettings 模型档位两槽（2026-10-19 档位制）', () => {
  it('renders standard/high slots with form values', () => {
    const { container } = render(<ExternalModelSettings form={baseForm} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.standardModel')
    expect(container.textContent).toContain('settings.highModel')
    expect(container.querySelector('input[value="glm-5.3-flash"]')).toBeTruthy()
    expect(container.querySelector('input[value="glm-5.3"]')).toBeTruthy()
    // 旧单一 model 字段已废叶
    expect(container.textContent).not.toContain('settings.modelName')
  })

  it('emits onUpdate for slot changes', () => {
    const onUpdate = vi.fn()
    const { container } = render(<ExternalModelSettings form={baseForm} onUpdate={onUpdate} />)
    const label = [...container.querySelectorAll('label')].find((l) => l.textContent === 'settings.highModel')
    expect(label).toBeTruthy()
    const input = label!.nextElementSibling as HTMLInputElement
    fireEvent.change(input, { target: { value: 'glm-6' } })
    expect(onUpdate).toHaveBeenCalledWith('high_model', 'glm-6')
  })
})

describe('ExternalModelSettings context window (2026-10-15 budget UI)', () => {
  it('renders context window input with form value', () => {
    const { container } = render(
      <ExternalModelSettings form={{ ...baseForm, context_window: 200000 }} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.contextWindow')
    expect(container.textContent).toContain('settings.contextWindowHint')
    expect(container.querySelector('input[value="200000"]')).toBeTruthy()
  })

  it('emits onUpdate for context window change and shows max tokens hint', () => {
    const onUpdate = vi.fn()
    const { container } = render(<ExternalModelSettings form={baseForm} onUpdate={onUpdate} />)
    expect(container.textContent).toContain('settings.maxTokensHint')
    const label = [...container.querySelectorAll('label')].find((l) => l.textContent === 'settings.contextWindow')
    expect(label).toBeTruthy()
    const input = label!.nextElementSibling as HTMLInputElement
    fireEvent.change(input, { target: { value: '131072' } })
    expect(onUpdate).toHaveBeenCalledWith('context_window', 131072)
  })
})

describe('ExternalModelSettings LLM timeouts (0.3 §4.4 自通用 section 移入)', () => {
  it('renders timeout inputs with form values and no longer exposes routing sections', () => {
    const { container } = render(
      <ExternalModelSettings form={{ ...baseForm, llm_connect_timeout: 45, llm_read_timeout: 600 }} onUpdate={vi.fn()} />)
    expect(container.querySelector('input[value="45"]')).toBeTruthy()
    expect(container.querySelector('input[value="600"]')).toBeTruthy()
    // 路由区块已迁出，连接 section 内不再出现
    expect(container.textContent).not.toContain('settings.wikiRoutingSection')
    expect(container.textContent).not.toContain('settings.pipelineRoutingSection')
  })

  it('emits onUpdate for timeout changes', () => {
    const onUpdate = vi.fn()
    const { container } = render(<ExternalModelSettings form={baseForm} onUpdate={onUpdate} />)
    const label = [...container.querySelectorAll('label')].find((l) => l.textContent === 'settings.llm.connectTimeout')
    expect(label).toBeTruthy()
    const input = label!.nextElementSibling as HTMLInputElement
    fireEvent.change(input, { target: { value: '60' } })
    expect(onUpdate).toHaveBeenCalledWith('llm_connect_timeout', 60)
  })
})
