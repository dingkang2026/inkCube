/** tests/TaskModelSettings.test.tsx — 任务模型档位化（0.3 §4.4 任务组 2026-10-19）。
 *  用例自 ModelRoutingSettings.test.tsx 迁入并改造为档位制：
 *  任务模型二态下拉；规划模型三态（off = 不启用，单模型模式）。 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, opts?: Record<string, unknown>) =>
    opts && 'model' in opts ? `${k}:${opts.model}` : k }),
}))
vi.mock('../src/features/settings/routingFields.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { TaskModelSettings } from '../src/features/settings/TaskModelSettings'
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
  task_timeout: 300, task_max_iterations: 60, task_auto_confirm_shell: false,
  task_shell_confirm_mode: 'auto', task_shell_domain_whitelist: '',
  task_show_execution_log: true, task_workspace_max_mb: 1024,
  task_max_count: 50, task_auto_archive_days: 30,
} as LLMSettings

describe('TaskModelSettings — 任务/规划模型档位', () => {
  it('renders section with task model (standard) and planner (off)', () => {
    const { container } = render(<TaskModelSettings form={baseForm} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.taskModelRouting')
    expect(container.textContent).toContain('settings.task.model')
    expect(container.textContent).toContain('settings.task.plannerModel')
    const selects = container.querySelectorAll('select')
    expect(selects).toHaveLength(2)
    expect((selects[0] as HTMLSelectElement).value).toBe('standard')
    expect((selects[1] as HTMLSelectElement).value).toBe('')
  })

  it('shows resolved slot name for task model, none for planner off', () => {
    const { container } = render(<TaskModelSettings form={baseForm} onUpdate={vi.fn()} />)
    // 任务模型副文本：standard 档解析到标准槽
    expect(container.textContent).toContain('settings.level.current:glm-5.3-flash')
    // planner off：resolved 为空，仅一处副文本
    expect(
      (container.textContent!.match(/settings\.level\.current/g) || []).length,
    ).toBe(1)
  })

  it('emits onUpdate for planner tier selection', () => {
    const onUpdate = vi.fn()
    const { container } = render(<TaskModelSettings form={baseForm} onUpdate={onUpdate} />)
    const label = [...container.querySelectorAll('label')].find((l) => l.textContent === 'settings.task.plannerModel')
    expect(label).toBeTruthy()
    const select = label!.parentElement!.querySelector('select') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'high' } })
    expect(onUpdate).toHaveBeenCalledWith('task_planner_model', 'high')
  })

  it('planner high tier resolves to high slot as subtext', () => {
    const { container } = render(
      <TaskModelSettings form={{ ...baseForm, task_planner_model: 'high' }} onUpdate={vi.fn()} />)
    expect(container.textContent).toContain('settings.level.current:glm-5.3')
  })

  it('emits onUpdate for task model tier change', () => {
    const onUpdate = vi.fn()
    const { container } = render(<TaskModelSettings form={baseForm} onUpdate={onUpdate} />)
    const label = [...container.querySelectorAll('label')].find((l) => l.textContent === 'settings.task.model')
    expect(label).toBeTruthy()
    const select = label!.parentElement!.querySelector('select') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'high' } })
    expect(onUpdate).toHaveBeenCalledWith('task_model', 'high')
  })
})
