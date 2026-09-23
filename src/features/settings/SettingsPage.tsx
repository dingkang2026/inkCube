import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft } from 'lucide-react'
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
  onClose: () => void
}

const SECTIONS = ['general', 'externalModel', 'internalModel', 'gpu', 'task', 'skills', 'dataManagement'] as const

export function SettingsPage({ settings, onSave, onClose }: SettingsPageProps) {
  const { t } = useTranslation()
  const [activeSection, setActiveSection] = useState('general')
  const [form, setForm] = useState<LLMSettings>({ ...settings })
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [testing, setTesting] = useState(false)

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
        <button className={s.backBtn} onClick={onClose} title={t('settings.back')}>
          <ArrowLeft size={18} />
        </button>
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
