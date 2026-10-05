import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { SettingsNav } from './SettingsNav'
import { GeneralSettings } from './GeneralSettings'
import { ExternalModelSettings } from './ExternalModelSettings'
import { InternalModelSettings } from './InternalModelSettings'
import { GPUSettings } from './GPUSettings'
import { DataManagement } from './DataManagement'
import { TaskSettings } from './TaskSettings'
import { SkillManagement } from './SkillManagement'
import type { LLMSettings } from '../../types'
import s from './SettingsPage.module.css'

interface SettingsPageProps {
  settings: LLMSettings
  onSave: (settings: LLMSettings) => void
  /** 外部导航信号（含锚点 section；设置页条件渲染重挂载时用 name 初始化，同 section 重复导航靠引用变化生效） */
  sectionSignal?: { name: string; ts: number } | null
}

const SECTIONS = ['general', 'externalModel', 'internalModel', 'gpu', 'task', 'skills', 'dataManagement'] as const

export function SettingsPage({ settings, onSave, sectionSignal }: SettingsPageProps) {
  const { t } = useTranslation()
  const [activeSection, setActiveSection] = useState(sectionSignal?.name ?? 'general')
  const [form, setForm] = useState<LLMSettings>({ ...settings })
  const [testResult, setTestResult] = useState<{
    ok: boolean
    message: string
    capabilities?: { function_calling: boolean | null; reasoning_effort: boolean | null; thinking_toggle: boolean | null }
  } | null>(null)
  const [testing, setTesting] = useState(false)

  // 外部锚点直达（0.4 §7.4）：设置页已打开时再次导航（同 section 也生效，signal 每次新引用）
  useEffect(() => {
    if (sectionSignal) setActiveSection(sectionSignal.name)
  }, [sectionSignal])

  useEffect(() => {
    setForm({ ...settings })
  }, [settings])

  const update = <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = (await window.electronAPI.invoke('settings:personal_test', form)) as {
        ok: boolean
        message: string
      }
      setTestResult(res)
    } catch (err) {
      setTestResult({ ok: false, message: String(err) })
    } finally {
      setTesting(false)
    }
  }

  const navItems = SECTIONS.map((key) => ({
    key,
    label: t(`settings.nav.${key}`),
  }))

  const showSave = activeSection === 'externalModel' || activeSection === 'general' || activeSection === 'task'
  const showTest = activeSection === 'externalModel'

  return (
    <main className={s.page}>
      <div className={s.header}>
        <h2>{t('settings.title')}</h2>
      </div>

      <div className={s.body}>
        <SettingsNav active={activeSection} onChange={setActiveSection} items={navItems} />

        <div className={s.content}>
          {activeSection === 'general' && (
            <GeneralSettings settings={form} onUpdate={update} />
          )}
          {activeSection === 'externalModel' && <ExternalModelSettings form={form} onUpdate={update} />}
          {activeSection === 'internalModel' && <InternalModelSettings />}
          {activeSection === 'gpu' && <GPUSettings />}
          {activeSection === 'task' && <TaskSettings settings={form} onUpdate={update} />}
          {activeSection === 'skills' && <SkillManagement />}
          {activeSection === 'dataManagement' && <DataManagement />}

          {(showSave || showTest) && (
            <div className={s.actions}>
              {testResult && (
                <div className={`${s.testResult} ${testResult.ok ? s.success : s.error}`}>
                  {testResult.message}
                </div>
              )}
              {testResult?.ok && testResult.capabilities && (
                <div className={s.capabilities}>
                  {(
                    [
                      ['function_calling', t('settings.capFc')],
                      ['reasoning_effort', t('settings.capReasoning')],
                      ['thinking_toggle', t('settings.capThinking')],
                    ] as const
                  ).map(([key, label]) => {
                    const value = testResult.capabilities?.[key]
                    const state = value === true ? s.ok : value === false ? s.no : ''
                    const mark = value === true ? '✓' : value === false ? '✗' : '?'
                    return (
                      <span key={key} className={`${s.capBadge} ${state}`}>
                        {label} {mark}
                      </span>
                    )
                  })}
                </div>
              )}
              {showTest && (
                <button className="btn btn-secondary" onClick={handleTest} disabled={testing}>
                  {testing ? t('settings.testing') : t('settings.testConnection')}
                </button>
              )}
              {showSave && (
                <button className="btn btn-primary" onClick={() => onSave(form)}>
                  {t('settings.save')}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </main>
  )
}
