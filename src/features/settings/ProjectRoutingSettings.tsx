import { useTranslation } from 'react-i18next'
import type { LLMSettings } from '../../types'
import styles from './routingFields.module.css'

interface ProjectRoutingSettingsProps {
  form: LLMSettings
  onUpdate: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

type RoutingKey =
  | 'wiki_ingest_model'
  | 'wiki_lint_model'
  | 'wiki_dedup_model'
  | 'wiki_ledger_parse_model'
  | 'wiki_enrich_model'
  | 'report_model'
  | 'report_extract_model'
  | 'form_analyze_model'
  | 'form_fill_model'
  | 'rag_model'
  | 'ppt_model'
  | 'meeting_note_model'

/** 档位下拉（standard/high，2026-10-19 档位制）：后端 resolve_model 解析为对应槽位模型名 */
function LevelSelect({
  form,
  onUpdate,
  settingKey,
  labelKey,
  hintKey,
}: {
  form: LLMSettings
  onUpdate: ProjectRoutingSettingsProps['onUpdate']
  settingKey: RoutingKey
  labelKey: string
  hintKey: string
}) {
  const { t } = useTranslation()
  const level = form[settingKey] || 'standard'
  // 当前档位解析到的模型名（与后端互为回退规则一致，仅作副文本展示）
  const resolved =
    level === 'high'
      ? form.high_model || form.standard_model
      : form.standard_model || form.high_model
  return (
    <div className={styles.field}>
      <label className={styles.label}>{t(labelKey)}</label>
      <select
        className={styles.select}
        value={level}
        onChange={(e) => onUpdate(settingKey, e.target.value)}
      >
        <option value="standard">{t('settings.level.standard')}</option>
        <option value="high">{t('settings.level.high')}</option>
      </select>
      <p className={styles.hint}>
        {t(hintKey)}
        {resolved ? t('settings.level.current', { model: resolved }) : ''}
      </p>
    </div>
  )
}

/** 项目模型路由（0.3 §4.4 项目组）：Wiki / 业务管线（报告·填表·RAG·PPT）档位覆盖 */
export function ProjectRoutingSettings({ form, onUpdate }: ProjectRoutingSettingsProps) {
  const { t } = useTranslation()
  return (
    <>
      <section className={styles.section}>
        <h3 className={styles.heading}>{t('settings.wikiRoutingSection')}</h3>
        <p className={styles.hint}>{t('settings.routingFallbackHint')}</p>
        <div className={styles.fieldGrid}>
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="wiki_ingest_model" labelKey="settings.wikiIngestModel" hintKey="settings.wikiIngestModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="wiki_lint_model" labelKey="settings.wikiLintModel" hintKey="settings.wikiLintModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="wiki_dedup_model" labelKey="settings.wikiDedupModel" hintKey="settings.wikiDedupModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="wiki_ledger_parse_model" labelKey="settings.wikiLedgerParseModel" hintKey="settings.wikiLedgerParseModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="wiki_enrich_model" labelKey="settings.wikiEnrichModel" hintKey="settings.wikiEnrichModelHint" />
        </div>
      </section>

      <section className={styles.section}>
        <h3 className={styles.heading}>{t('settings.pipelineRoutingSection')}</h3>
        <div className={styles.fieldGrid}>
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="report_model" labelKey="settings.reportModel" hintKey="settings.reportModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="report_extract_model" labelKey="settings.reportExtractModel" hintKey="settings.reportExtractModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="form_analyze_model" labelKey="settings.formAnalyzeModel" hintKey="settings.formAnalyzeModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="form_fill_model" labelKey="settings.formFillModel" hintKey="settings.formFillModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="rag_model" labelKey="settings.ragModel" hintKey="settings.ragModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="ppt_model" labelKey="settings.pptModel" hintKey="settings.pptModelHint" />
          <LevelSelect form={form} onUpdate={onUpdate} settingKey="meeting_note_model" labelKey="settings.meetingNoteModel" hintKey="settings.meetingNoteModelHint" />
        </div>
      </section>
    </>
  )
}
