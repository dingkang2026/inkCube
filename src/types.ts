export interface LLMSettings {
  api_url: string
  api_key: string
  // 模型档位槽（2026-10-19 档位制，替代原单一 model）：standard = 标准档（默认），
  // high = 高配档；路由键存档位，后端 llm/routing.resolve_model 解析（互为回退）
  standard_model: string
  high_model: string
  temperature: number
  max_tokens: number
  // 预算键：0 = 自动（后端 budget.floor 落下限，详见 llm/budget.py）
  context_window: number
  top_p: number
  // Reasoning/thinking params (vendor-specific; see ExternalModelSettings)
  reasoning_effort: string  // '' = omit (model default); low/medium/high/max/xhigh/minimal...
  reasoning_override: string  // advanced: JSON object merged into request body top-level
  system_prompt: string
  tavily_key: string
  // 网络搜索供应商化（2026-10-19）：tavily | zhipu 二选一，'' = 未选择（必配未完成）；
  // 两把 Key 并存，切换供应商不丢配置
  web_search_provider: string
  zhipu_search_key: string
  zhipu_search_engine: string  // search_std | search_pro（智谱引擎档位）
  max_concurrent_file_parsing: number
  max_concurrent_image_description: number
  // Wiki compile settings
  max_compile_concurrency: number
  // LLM timeout settings
  llm_connect_timeout: number
  llm_read_timeout: number
  // Task-specific routing（值 = 档位 standard/high；planner 空 = 不启用规划阶段）
  task_model: string
  task_planner_model: string
  // LLM task routing（llm/routing.py 唯一入口；值 = 档位 standard/high，除 ppt 默认 high 外全部 standard）
  wiki_ingest_model: string
  wiki_lint_model: string
  wiki_dedup_model: string
  wiki_ledger_parse_model: string
  wiki_enrich_model: string
  report_model: string
  report_extract_model: string
  form_analyze_model: string
  form_fill_model: string
  rag_model: string
  ppt_model: string
  task_timeout: number
  task_max_iterations: number
  task_auto_confirm_shell: boolean
  task_shell_confirm_mode: string  // strict | smart | auto
  task_shell_domain_whitelist: string
  task_show_execution_log: boolean
  task_workspace_max_mb: number
  task_max_count: number
  task_auto_archive_days: number
  // 存储保留策略（2026-10-20 数据管理审计）：数据管理面板可改，启动时由
  // maintenance 后台任务执行；0 = 永不清理
  archived_task_cleanup_days: number
  llm_usage_retention_days: number
  schedule_runs_retention_days: number
  // Notification settings (2.11 §2.3; consumed by ActivityMonitor for OS notifications)
  os_notify_enabled: boolean
  os_notify_sound: boolean
  // 事务到期提醒开关（2.11 §3.2；affairs_service.maybe_scan_reminders 心跳消费）
  affair_reminder_enabled: boolean
  // R4 订阅完成通知开关（review P2-7 提前落地）：默认 false = 静默；
  // ActivityMonitor dispatchToasts 消费（completed 弹出与否）
  schedule_notify: boolean
  // 用户身份层（0.6 §3.8 V2-E1a）：'' = 未设置，驱动模板排序；
  // persona_prompted = PersonaPicker 已呈现过（True 后不再弹）
  persona: '' | 'business_attraction' | 'investment' | 'both' | 'other'
  persona_prompted: boolean
}

/** models:* IPC 通道名集中定义（electron/ipc-handlers.js 注册），消灭调用点魔法串（0.4 §8.1 #8） */
export const MODELS_IPC = {
  status: 'models:status',
  libreofficeStatus: 'models:libreoffice-status',
  download: 'models:download',
  downloadAll: 'models:download-all',
  delete: 'models:delete',
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
  | { type: 'note'; text: string; iteration?: number }

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

/** LLM token 用量聚合（2.10 §3/§4.5；API 返回精确值的聚合，前端禁止二次估算） */
export interface UsageSummary {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
  /** 该轮去重模型列表（GROUP_CONCAT 逗号串；仅轮级聚合返回，列表/实体行聚合无此字段） */
  models?: string
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
  /** 任务累计 token 消耗（GET /tasks 列表聚合；无记录为 null，2.10 §4.5） */
  usage?: UsageSummary | null
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
    /** 该条 assistant 消息所属轮次的用量汇总（2.10 §3.2；仅 assistant 消息有） */
    usage?: UsageSummary
    [key: string]: unknown
  }
  created_at: string
}
