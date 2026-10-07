import { useTranslation } from 'react-i18next'
import type { LLMSettings } from '../../types'
import { ExternalDepsSettings } from './ExternalDepsSettings'
import styles from './TaskSettings.module.css'

interface TaskSettingsProps {
  settings?: LLMSettings
  onUpdate?: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

export function TaskSettings({ settings, onUpdate }: TaskSettingsProps) {
  const { t } = useTranslation()

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{t('settings.task.title')}</h3>

      {/* Task Model */}
      <label className={styles.label}>{t('settings.task.model')}</label>
      <input
        type="text"
        className={styles.input}
        placeholder={settings?.model || 'glm-4-flash'}
        value={settings?.task_model ?? ''}
        onChange={(e) => onUpdate?.('task_model', e.target.value)}
      />
      <p className={styles.hint}>{t('settings.task.modelHint')}</p>

      {/* Planner Model (dual-model architecture) */}
      <label className={styles.label}>{t('settings.task.plannerModel')}</label>
      <input
        type="text"
        className={styles.input}
        placeholder={t('settings.task.plannerModelPlaceholder')}
        value={settings?.task_planner_model ?? ''}
        onChange={(e) => onUpdate?.('task_planner_model', e.target.value)}
      />
      <p className={styles.hint}>{t('settings.task.plannerModelHint')}</p>

      {/* Execution Timeout */}
      <label className={styles.label}>{t('settings.task.timeout')}</label>
      <input
        type="number"
        min={5}
        max={300}
        className={styles.input}
        value={settings?.task_timeout ?? 30}
        onChange={(e) => onUpdate?.('task_timeout', parseInt(e.target.value) || 30)}
      />
      <p className={styles.hint}>{t('settings.task.timeoutHint')}</p>

      {/* Max Iterations (FC loop limit) */}
      <label className={styles.label}>{t('settings.task.maxIterations')}</label>
      <input
        type="number"
        min={1}
        max={200}
        className={styles.input}
        value={settings?.task_max_iterations ?? 25}
        onChange={(e) => onUpdate?.('task_max_iterations', parseInt(e.target.value) || 25)}
      />
      <p className={styles.hint}>{t('settings.task.maxIterationsHint')}</p>

      {/* Shell Confirm Mode (3-mode) */}
      <label className={styles.label}>{t('settings.task.shellConfirmMode')}</label>
      <select
        className={styles.input}
        value={settings?.task_shell_confirm_mode ?? 'smart'}
        onChange={(e) => onUpdate?.('task_shell_confirm_mode', e.target.value)}
      >
        <option value="strict">{t('settings.task.shellConfirmStrict')}</option>
        <option value="smart">{t('settings.task.shellConfirmSmart')}</option>
        <option value="auto">{t('settings.task.shellConfirmAuto')}</option>
      </select>
      <p className={styles.hint}>{t('settings.task.shellConfirmModeHint')}</p>

      {/* Shell Domain Whitelist */}
      <label className={styles.label}>{t('settings.task.shellDomainWhitelist')}</label>
      <textarea
        className={styles.input}
        rows={3}
        placeholder="pypi.org&#10;github.com"
        value={settings?.task_shell_domain_whitelist ?? ''}
        onChange={(e) => onUpdate?.('task_shell_domain_whitelist', e.target.value)}
      />
      <p className={styles.hint}>{t('settings.task.shellDomainWhitelistHint')}</p>

      {/* Show Execution Log */}
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.task.showLog')}</span>
          <p className={styles.hint}>{t('settings.task.showLogHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.task_show_execution_log ?? true}
            onChange={(e) => onUpdate?.('task_show_execution_log', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>

      {/* Workspace Max MB */}
      <label className={styles.label}>{t('settings.task.workspaceMaxMb')}</label>
      <input
        type="number"
        min={0}
        max={10240}
        className={styles.input}
        value={settings?.task_workspace_max_mb ?? 1024}
        onChange={(e) => onUpdate?.('task_workspace_max_mb', parseInt(e.target.value) || 0)}
      />
      <p className={styles.hint}>{t('settings.task.workspaceMaxMbHint')}</p>

      {/* Max Task Count */}
      <label className={styles.label}>{t('settings.task.maxCount')}</label>
      <input
        type="number"
        min={0}
        max={500}
        className={styles.input}
        value={settings?.task_max_count ?? 50}
        onChange={(e) => onUpdate?.('task_max_count', parseInt(e.target.value) || 0)}
      />
      <p className={styles.hint}>{t('settings.task.maxCountHint')}</p>

      {/* Auto-archive Days */}
      <label className={styles.label}>{t('settings.task.autoArchiveDays')}</label>
      <input
        type="number"
        min={0}
        max={365}
        className={styles.input}
        value={settings?.task_auto_archive_days ?? 30}
        onChange={(e) => onUpdate?.('task_auto_archive_days', parseInt(e.target.value) || 0)}
      />
      <p className={styles.hint}>{t('settings.task.autoArchiveDaysHint')}</p>

      {/* External Dependencies Detection */}
      <ExternalDepsSettings />
    </div>
  )
}
