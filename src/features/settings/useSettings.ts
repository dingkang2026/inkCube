import { useState, useEffect, useCallback } from 'react'
import type { LLMSettings } from '../../types'

const DEFAULT_SETTINGS: LLMSettings = {
  api_url: 'https://open.bigmodel.cn/api/paas/v4/',
  api_key: '',
  model: 'glm-4-flash',
  temperature: 0.7,
  // 与后端 defaults 对齐（schemas.py SettingsModel / database.py get_settings）：
  // 后端不可达时本表作为回显基底，数值漂移会让用户保存时写入与预期不同的值
  max_tokens: 8192,
  top_p: 0.9,
  reasoning_effort: 'low',
  reasoning_override: '',
  system_prompt: '',
  tavily_key: '',
  vlm_api_url: 'https://open.bigmodel.cn/api/paas/v4/',
  vlm_api_key: '',
  vlm_model: 'glm-4v',
  max_concurrent_file_parsing: 2,
  max_concurrent_image_description: 2,
  // Wiki compile settings
  max_compile_concurrency: 2,
  // LLM timeout settings
  llm_connect_timeout: 30,
  llm_read_timeout: 300,
  task_model: '',
  task_planner_model: '',
  // Wiki LLM task routing (empty = use global model)
  wiki_ingest_model: '',
  wiki_lint_model: '',
  wiki_dedup_model: '',
  wiki_ledger_parse_model: '',
  task_timeout: 30,
  task_max_iterations: 25,
  task_auto_confirm_shell: false,
  task_shell_confirm_mode: 'smart',
  task_shell_domain_whitelist: '',
  task_show_execution_log: true,
  task_workspace_max_mb: 1024,
  task_max_count: 50,
  task_auto_archive_days: 30,
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
      setSettings(res as LLMSettings)
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
