export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'thought' | 'action' | 'observation' | 'reasoning'
  content: string
  metadata?: {
    tool_name?: string
    tool_input?: Record<string, unknown>
    tool_output?: string
    tokens?: number
    duration_ms?: number
  }
  created_at: string
}

export interface LLMSettings {
  api_url: string
  api_key: string
  model: string
  temperature: number
  max_tokens: number
  top_p: number
  system_prompt: string
  tavily_key: string
  vlm_api_url: string
  vlm_api_key: string
  vlm_model: string
  max_concurrent_file_parsing: number
  max_concurrent_image_description: number
  // Task-specific settings
  task_model: string
  task_planner_model: string
  task_timeout: number
  task_auto_confirm_shell: boolean
  task_shell_confirm_mode: string  // strict | smart | auto
  task_shell_domain_whitelist: string
  task_show_execution_log: boolean
  task_workspace_max_mb: number
  task_max_count: number
  task_auto_archive_days: number
}

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

export interface GpuStatus {
  mode: 'cpu' | 'gpu'
  gpu_downloaded: boolean
  gpu_package_size: string
}

export type StreamStep =
  | { type: 'reasoning'; content: string }
  | { type: 'thought'; content: string }
  | { type: 'action'; tool: string; input: Record<string, unknown>; output?: string; diff?: string; filePath?: string }

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
  }
  created_at: string
}
