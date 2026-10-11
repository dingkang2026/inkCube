export interface LLMSettings {
  api_url: string
  api_key: string
  model: string
  temperature: number
  max_tokens: number
  top_p: number
  // Reasoning/thinking params (vendor-specific; see ExternalModelSettings)
  reasoning_effort: string  // '' = omit (model default); low/medium/high/max/xhigh/minimal...
  reasoning_override: string  // advanced: JSON object merged into request body top-level
  system_prompt: string
  tavily_key: string
  vlm_api_url: string
  vlm_api_key: string
  vlm_model: string
  max_concurrent_file_parsing: number
  max_concurrent_image_description: number
  // Wiki compile settings
  max_compile_concurrency: number
  // LLM timeout settings
  llm_connect_timeout: number
  llm_read_timeout: number
  // Task-specific settings
  task_model: string
  task_planner_model: string
  // Wiki LLM task routing (ingest/lint/dedup/ledger_parse; empty = use global model)
  wiki_ingest_model: string
  wiki_lint_model: string
  wiki_dedup_model: string
  wiki_ledger_parse_model: string
  task_timeout: number
  task_max_iterations: number
  task_auto_confirm_shell: boolean
  task_shell_confirm_mode: string  // strict | smart | auto
  task_shell_domain_whitelist: string
  task_show_execution_log: boolean
  task_workspace_max_mb: number
  task_max_count: number
  task_auto_archive_days: number
}

/** models:* IPC 通道名集中定义（electron/ipc-handlers.js 注册），消灭调用点魔法串（0.4 §8.1 #8） */
export const MODELS_IPC = {
  status: 'models:status',
  libreofficeStatus: 'models:libreoffice-status',
  download: 'models:download',
  downloadAll: 'models:download-all',
} as const

export interface ModelStatusInfo {
  name: string
  category: string
  framework: string
  size: string
  size_bytes: number
  required: boolean
  downloaded: boolean
  path: string
  actual_size: number
  sources: string[]
  install_type: 'download' | 'bundled'
  downloading: boolean
  progress: number
  error: string | null
}

export type StreamStep =
  | { type: 'action'; tool: string; input: Record<string, unknown>; output?: string; diff?: string; filePath?: string; iteration?: number }

export interface PlanStep {
  id: number
  desc: string
  files: string[]
}

export interface PlanData {
  text: string
  steps: PlanStep[]
  total: number
}

export interface Task {
  id: string
  title: string
  status: string
  workspace_path: string
  mode?: 'coding' | 'general'
  created_at: string
  updated_at: string
  message_count?: number
}

export interface TaskMessage {
  id: string
  task_id: string
  role: 'user' | 'assistant' | 'thought' | 'action' | 'observation' | 'reasoning'
  content: string
  metadata?: {
    tool_name?: string
    tool_input?: Record<string, unknown>
    tool_output?: string
    diff?: string
    file_path?: string
    action_id?: string
    [key: string]: unknown
  }
  created_at: string
}
