import { useState, useEffect, useCallback } from 'react'
import type { LLMSettings } from '../../types'

const DEFAULT_SETTINGS: LLMSettings = {
  api_url: 'https://open.bigmodel.cn/api/paas/v4/',
  api_key: '',
  model: 'glm-4-flash',
  temperature: 0.7,
  max_tokens: 4096,
  top_p: 0.9,
  reasoning_effort: 'low',
  reasoning_override: '',
  system_prompt: '',
  tavily_key: '',
  vlm_api_url: 'https://open.bigmodel.cn/api/paas/v4/',
  vlm_api_key: '',
  vlm_model: 'glm-4v',
  max_concurrent_file_parsing: 1,
  max_concurrent_image_description: 2,
  // Wiki compile settings
  max_compile_concurrency: 2,
  compile_max_retries: 3,
  compile_backoff_base: 2,
  // LLM timeout settings
  llm_connect_timeout: 30,
  llm_read_timeout: 120,
  task_model: '',
  task_planner_model: '',
  task_timeout: 30,
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

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    try {
      const res = await window.electronAPI.invoke('settings:get')
      setSettings(res as LLMSettings)
    } catch (err) {
      console.error('Failed to load settings:', err)
    } finally {
      setSettingsLoaded(true)
    }
  }

  const handleSaveSettings = useCallback(async (newSettings: LLMSettings) => {
    await window.electronAPI.invoke('settings:save', newSettings)
    setSettings(newSettings)
  }, [])

  return {
    settings,
    settingsLoaded,
    handleSaveSettings,
  }
}
