import { useState, useEffect, useCallback } from 'react'
import type { LLMSettings } from '../../types'

const DEFAULT_SETTINGS: LLMSettings = {
  api_url: 'https://open.bigmodel.cn/api/paas/v4/',
  api_key: '',
  // 模型档位槽（2026-10-19 档位制，替代原单一 model 键）
  standard_model: 'glm-5.3-flash',
  high_model: 'glm-5.3',
  temperature: 0.7,
  // 与后端 defaults 对齐（schemas.py SettingsModel / database.py get_settings）：
  // 后端不可达时本表作为回显基底，数值漂移会让用户保存时写入与预期不同的值
  max_tokens: 32768,
  context_window: 131072,  // 0 = 自动（后端 budget.floor 落 128K 下限）
  top_p: 0.9,
  reasoning_effort: 'low',
  reasoning_override: '',
  system_prompt: '',
  tavily_key: '',
  // 与后端 defaults 对齐（web_search_provider 空 = 未选择，2026-10-19 供应商化）
  web_search_provider: '',
  zhipu_search_key: '',
  zhipu_search_engine: 'search_pro',
  max_concurrent_file_parsing: 2,
  max_concurrent_image_description: 2,
  // Wiki compile settings
  max_compile_concurrency: 2,
  auto_wiki_compile: true,  // 2.6 §4.1 路径 A：解析完成即自动汇编（默认开）
  // LLM timeout settings
  llm_connect_timeout: 30,
  llm_read_timeout: 300,
  // 任务路由（值 = 档位 standard/high；planner 空串 = 不启用规划）
  task_model: 'standard',
  task_planner_model: '',
  // Wiki LLM task routing（值 = 档位，空值按 standard）
  wiki_ingest_model: 'standard',
  wiki_lint_model: 'standard',
  wiki_dedup_model: 'standard',
  wiki_ledger_parse_model: 'standard',
  wiki_enrich_model: 'standard',
  // Pipeline LLM routing（值 = 档位，空值按 standard；ppt 唯一默认 high）
  report_model: 'standard',
  report_extract_model: 'standard',
  form_analyze_model: 'standard',
  form_fill_model: 'standard',
  rag_model: 'standard',
  ppt_model: 'high',
  meeting_note_model: 'standard',  // 拜访/会议纪要结构化（2.13）
  task_timeout: 30,
  task_max_iterations: 60,
  task_auto_confirm_shell: false,
  task_shell_confirm_mode: 'auto',
  task_shell_domain_whitelist: '',
  task_show_execution_log: true,
  task_workspace_max_mb: 1024,
  task_max_count: 50,
  task_auto_archive_days: 30,
  // 存储保留策略（与 schemas.py SettingsModel 对齐；0 = 永不清理）
  archived_task_cleanup_days: 30,
  llm_usage_retention_days: 90,
  schedule_runs_retention_days: 90,
  // Notification settings (2.11 §2.3)
  os_notify_enabled: true,
  os_notify_sound: true,
  affair_reminder_enabled: true,
  schedule_notify: false,   // R4 订阅完成通知：默认静默（review P2-7 提前落地）
  // 身份层（0.6 §3.8 V2-E1a）：'' 未设置 + 未呈现过 → 首启 PersonaPicker
  persona: '',
  persona_prompted: false,
}

export function useSettings() {
  const [settings, setSettings] = useState<LLMSettings>(DEFAULT_SETTINGS)
  // 首启向导判定需要区分「尚未加载」与「已加载但未配置」（0.4 §7.3）
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  // 加载失败时 settings 停留在 DEFAULT_SETTINGS 回显基底：继续编辑会把默认值
  // 当真实配置保存，覆盖用户原有配置——失败态必须显式暴露，供设置页禁编辑（D8）
  const [loadFailed, setLoadFailed] = useState(false)

  // useCallback 稳定引用：既作首次加载 effect 依赖，也作失败横幅的重试入口
  const loadSettings = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('settings:get')
      // 以 DEFAULT_SETTINGS 为基底合并：后端响应可能缺新增字段的键——
      // dev 模式后端进程无热重载，新增键要等进程重启才进响应（
      // 2026-10-15 实证：后端 08:55 启动，defaults 新增两键 14:50 落盘，
      // 响应缺 context_window/max_tokens）。直接断言会让 form 字段为
      // undefined，受控输入 value 收到 undefined 被 React 判为非受控，
      // 首次输入即报 "changing an uncontrolled input to be controlled"。
      setSettings({ ...DEFAULT_SETTINGS, ...(res as Partial<LLMSettings>) })
      setLoadFailed(false)
    } catch (err) {
      console.error('Failed to load settings:', err)
      setLoadFailed(true)
    } finally {
      setSettingsLoaded(true)
    }
  }, [])

  useEffect(() => {
    void loadSettings()
  }, [loadSettings])

  const handleSaveSettings = useCallback(async (newSettings: LLMSettings) => {
    await window.electronAPI.invoke('settings:save', newSettings)
    setSettings(newSettings)
  }, [])

  return {
    settings,
    settingsLoaded,
    loadFailed,
    handleSaveSettings,
    reloadSettings: loadSettings,
  }
}
