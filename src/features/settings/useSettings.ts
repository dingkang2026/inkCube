import { useState, useEffect, useCallback } from 'react'
import type { LLMSettings } from '../../types'

const DEFAULT_SETTINGS: LLMSettings = {
  api_url: 'https://open.bigmodel.cn/api/paas/v4/',
  api_key: '',
  model: 'glm-4-flash',
  temperature: 0.7,
  max_tokens: 4096,
  top_p: 0.9,
  system_prompt: '',
  tavily_key: '',
  vlm_api_url: 'https://open.bigmodel.cn/api/paas/v4/',
  vlm_api_key: '',
  vlm_model: 'glm-4v',
  max_concurrent_file_parsing: 1,
  max_concurrent_image_description: 2,
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

  useEffect(() => {
    loadSettings()
  }, [])

  const loadSettings = async () => {
    const res = await window.electronAPI.invoke('settings:get')
    setSettings(res as LLMSettings)
  }

  const handleSaveSettings = useCallback(async (newSettings: LLMSettings) => {
    await window.electronAPI.invoke('settings:save', newSettings)
    setSettings(newSettings)
  }, [])

  return {
    settings,
    handleSaveSettings,
  }
}
