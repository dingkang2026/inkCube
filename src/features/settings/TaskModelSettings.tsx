import { useTranslation } from 'react-i18next'
import type { LLMSettings } from '../../types'
import styles from './routingFields.module.css'

interface TaskModelSettingsProps {
  form: LLMSettings
  onUpdate: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

/** 任务模型（0.3 §4.4 任务组，2026-10-19 档位制）：
 * 主任务模型二态下拉；规划模型三态（off = 不启用规划，单模型模式）。
 * 档位由后端 llm/routing.resolve_model 解析为对应槽位模型名。 */
export function TaskModelSettings({ form, onUpdate }: TaskModelSettingsProps) {
  const { t } = useTranslation()
  const taskLevel = form.task_model || 'standard'
  // 当前档位解析到的模型名（与后端互为回退规则一致，仅作副文本展示）
  const taskResolved =
    taskLevel === 'high'
      ? form.high_model || form.standard_model
      : form.standard_model || form.high_model
  const plannerLevel = form.task_planner_model || ''
  // 规划档位解析到的模型名（off 时不展示；与后端互为回退规则一致，仅作副文本）
  const plannerResolved =
    plannerLevel === 'high'
      ? form.high_model || form.standard_model
      : plannerLevel === 'standard'
        ? form.standard_model || form.high_model
        : ''
  return (
    <section className={styles.section}>
      <h3 className={styles.heading}>{t('settings.taskModelRouting')}</h3>
      <p className={styles.hint}>{t('settings.taskModelRoutingHint')}</p>
      <div className={styles.fieldGrid}>
        <div className={styles.field}>
          <label className={styles.label}>{t('settings.task.model')}</label>
          <select
            className={styles.select}
            value={taskLevel}
            onChange={(e) => onUpdate('task_model', e.target.value)}
          >
            <option value="standard">{t('settings.level.standard')}</option>
            <option value="high">{t('settings.level.high')}</option>
          </select>
          <p className={styles.hint}>
            {t('settings.task.modelHint')}
            {taskResolved ? t('settings.level.current', { model: taskResolved }) : ''}
          </p>
        </div>

        <div className={styles.field}>
          <label className={styles.label}>{t('settings.task.plannerModel')}</label>
          <select
            className={styles.select}
            value={plannerLevel}
            onChange={(e) => onUpdate('task_planner_model', e.target.value)}
          >
            <option value="">{t('settings.level.off')}</option>
            <option value="standard">{t('settings.level.standard')}</option>
            <option value="high">{t('settings.level.high')}</option>
          </select>
          <p className={styles.hint}>
            {t('settings.task.plannerModelHint')}
            {plannerResolved ? t('settings.level.current', { model: plannerResolved }) : ''}
          </p>
        </div>
      </div>
    </section>
  )
}
