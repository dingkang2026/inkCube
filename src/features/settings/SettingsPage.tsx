import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { SettingsNav } from './SettingsNav'
import { GeneralSettings } from './GeneralSettings'
import { ExternalModelSettings } from './ExternalModelSettings'
import { InternalModelSettings } from './InternalModelSettings'
import { DataManagement } from './DataManagement'
import { TaskSettings } from './TaskSettings'
import { SkillManagement } from './SkillManagement'
import type { LLMSettings } from '../../types'
import s from './SettingsPage.module.css'

interface SettingsPageProps {
  settings: LLMSettings
  onSave: (settings: LLMSettings) => Promise<void> | void
  /** 外部导航信号（含锚点 section；设置页条件渲染重挂载时用 name 初始化，同 section 重复导航靠引用变化生效） */
  sectionSignal?: { name: string; ts: number } | null
}

const SECTIONS = ['general', 'externalModel', 'internalModel', 'task', 'skills', 'dataManagement'] as const
// 自动保存静默期：连续输入/滑块拖动合并为一次落盘（0.3 §3.5 v0.8.0）
const AUTO_SAVE_DELAY_MS = 800

type SaveState = 'idle' | 'saving' | 'saved' | 'failed'

export function SettingsPage({ settings, onSave, sectionSignal }: SettingsPageProps) {
  const { t } = useTranslation()
  const [activeSection, setActiveSection] = useState(sectionSignal?.name ?? 'general')
  const [form, setForm] = useState<LLMSettings>({ ...settings })
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState('')
  const [testResult, setTestResult] = useState<{
    ok: boolean
    message: string
    capabilities?: { function_calling: boolean | null; reasoning_effort: boolean | null; thinking_toggle: boolean | null }
  } | null>(null)
  const [testing, setTesting] = useState(false)

  // 最新 form / onSave 经 ref 供 debounce 到期与卸载 flush 读取（闭包不陈旧）
  const formRef = useRef(form)
  formRef.current = form
  const onSaveRef = useRef(onSave)
  onSaveRef.current = onSave
  // 我方保存触发的 settings 回灌（引用 = 保存快照）跳过——避免保存飞行中的新输入被旧值回滚
  const lastSavedRef = useRef<LLMSettings | null>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 外部锚点直达（0.4 §7.4）：设置页已打开时再次导航（同 section 也生效，signal 每次新引用）
  useEffect(() => {
    if (sectionSignal) setActiveSection(sectionSignal.name)
  }, [sectionSignal])

  useEffect(() => {
    if (settings === lastSavedRef.current) return
    setForm({ ...settings })
  }, [settings])

  // 卸载兜底：设置页条件渲染，关闭/切换 section 时未落盘的修改立即保存
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = null
        void Promise.resolve(onSaveRef.current(formRef.current)).catch(() => {})
      }
    }
  }, [])

  const doSave = useCallback(async () => {
    setSaveState('saving')
    const snapshot = formRef.current
    try {
      await onSaveRef.current(snapshot)
      lastSavedRef.current = snapshot
      setSaveState('saved')
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err))
      setSaveState('failed')
    }
  }, [])

  const update = <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    // 自动保存：改动后静默期全量落盘，连续改动合并为一次
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null
      void doSave()
    }, AUTO_SAVE_DELAY_MS)
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

  const showTest = activeSection === 'externalModel'

  return (
    <main className={s.page}>
      <div className={s.header}>
        <h2>{t('settings.title')}</h2>
        {saveState !== 'idle' && (
          <span className={`${s.autoSave} ${saveState === 'failed' ? s.failed : ''}`}>
            {saveState === 'saving'
              ? t('settings.autoSave.saving')
              : saveState === 'failed'
                ? t('settings.autoSave.failed', { error: saveError })
                : t('settings.autoSave.saved')}
          </span>
        )}
      </div>

      <div className={s.body}>
        <SettingsNav active={activeSection} onChange={setActiveSection} items={navItems} />

        <div className={s.content}>
          {activeSection === 'general' && (
            <GeneralSettings settings={form} onUpdate={update} />
          )}
          {activeSection === 'externalModel' && <ExternalModelSettings form={form} onUpdate={update} />}
          {activeSection === 'internalModel' && <InternalModelSettings />}
          {activeSection === 'task' && <TaskSettings settings={form} onUpdate={update} />}
          {activeSection === 'skills' && <SkillManagement />}
          {activeSection === 'dataManagement' && <DataManagement />}

          {showTest && (
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
              <button className="btn btn-secondary" onClick={handleTest} disabled={testing}>
                {testing ? t('settings.testing') : t('settings.testConnection')}
              </button>
            </div>
          )}
        </div>
      </div>
    </main>
  )
}
