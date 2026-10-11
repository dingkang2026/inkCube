/** tests/ExternalModelSettings.test.tsx — 设置页 Wiki 模型路由(3.0 四期 T10:台账解析模型项) */
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
  api_url: '', api_key: '', model: '', temperature: 0.7, max_tokens: 4096,
  top_p: 0.9, reasoning_effort: 'low', reasoning_override: '', system_prompt: '',
  tavily_key: '', vlm_api_url: '', vlm_api_key: '', vlm_model: '',
  max_concurrent_file_parsing: 2, max_concurrent_image_description: 2,
  max_compile_concurrency: 2, llm_connect_timeout: 30, llm_read_timeout: 120,
  task_model: '', task_planner_model: '',
  wiki_ingest_model: '', wiki_lint_model: '', wiki_dedup_model: '',
  wiki_ledger_parse_model: '',
  task_timeout: 30, task_max_iterations: 25, task_auto_confirm_shell: false,
  task_shell_confirm_mode: 'smart', task_shell_domain_whitelist: '',
  task_show_execution_log: true, task_workspace_max_mb: 1024,
  task_max_count: 50, task_auto_archive_days: 30,
} as LLMSettings

describe('ExternalModelSettings wiki ledger parse model (3.0 四期 T10)', () => {
  it('renders ledger parse model input with form value', () => {
    const { container } = render(
      <ExternalModelSettings form={{ ...baseForm, wiki_ledger_parse_model: 'glm-4-flash' }} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.wikiLedgerParseModel')
    expect(container.textContent).toContain('settings.wikiLedgerParseModelHint')
    const input = container.querySelector('input[value="glm-4-flash"]') as HTMLInputElement
    expect(input).toBeTruthy()
  })

  it('emits onUpdate on change', () => {
    const onUpdate = vi.fn()
    const { container } = render(
      <ExternalModelSettings form={{ ...baseForm, wiki_ledger_parse_model: '' }} onUpdate={onUpdate} />)
    const label = [...container.querySelectorAll('label')].find((l) => l.textContent === 'settings.wikiLedgerParseModel')
    expect(label).toBeTruthy()
    const input = label!.nextElementSibling as HTMLInputElement
    fireEvent.change(input, { target: { value: 'glm-4-air' } })
    expect(onUpdate).toHaveBeenCalledWith('wiki_ledger_parse_model', 'glm-4-air')
  })
})
