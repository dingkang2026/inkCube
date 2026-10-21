import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  BarChart3, Blocks, Bot, Cloud, Cpu, Database, Gauge, Globe, ListTodo, Route, Search, SlidersHorizontal,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { SettingsNav } from './SettingsNav'
import { GeneralSettings } from './GeneralSettings'
import { ExternalModelSettings } from './ExternalModelSettings'
import { InternalModelSettings } from './InternalModelSettings'
import { WebSearchSettings } from './WebSearchSettings'
import { ProjectRoutingSettings } from './ProjectRoutingSettings'
import { TaskModelSettings } from './TaskModelSettings'
import { ConcurrencySettings } from './ConcurrencySettings'
import { DataManagement } from './DataManagement'
import { TaskSettings } from './TaskSettings'
import { SkillManagement } from './SkillManagement'
import { WebLogins } from './WebLogins'
import { UsageStats } from '../usage/UsageStats'
import type { LLMSettings } from '../../types'
import { apiErrorText } from '../../i18n/errorCodes'
import { useHealthServices } from '../readiness/useHealthServices'
import s from './SettingsPage.module.css'

interface SettingsPageProps {
  settings: LLMSettings
  onSave: (settings: LLMSettings) => Promise<void> | void
  /** settings 表被清空（数据管理面板单类清理/全清成功）后回灌默认值，
   *  防旧 form 经自动保存复活写回（B2 复活竞态） */
  onSettingsReset: () => void
  /** 设置加载失败：form 为默认值回显基底，禁编辑防止默认值覆盖真实配置（D8） */
  loadFailed?: boolean
  /** 失败横幅上的重试回调（重新拉取 settings:get） */
  onRetryLoad?: () => Promise<void> | void
  /** 外部导航信号（含锚点 section；设置页条件渲染重挂载时用 name 初始化，同 section 重复导航靠引用变化生效）；
   *  projectId 可选：用量统计页项目筛选（2.10 §6.5 线框 5，项目徽章点击） */
  sectionSignal?: { name: string; ts: number; projectId?: string } | null
}

// 导航分组（0.3 §4.4 五组，2026-10-19 二次重组）：组标题小字分隔、不占条目；
// section key 为锚点契约（SystemAlerts / useErrorHandler / 向导 / ActivityMonitor
// 等直达导航依赖），禁改名。首组 label 置空 = 顶部无组名（单条目组省标题，
// 避免「常规 > 通用」同义重复）
const SECTION_GROUPS = [
  { label: '', sections: ['general'] },
  { label: 'settings.group.basics', sections: ['externalModel', 'internalModel', 'webSearch'] },
  { label: 'settings.group.project', sections: ['projectRouting', 'concurrency'] },
  { label: 'settings.group.tasks', sections: ['task', 'taskModel', 'skills', 'webLogins'] },
  { label: 'settings.group.data', sections: ['dataManagement', 'usage'] },
] as const

type SectionKey = (typeof SECTION_GROUPS)[number]['sections'][number]
// 分类图标（2026-10-19 设置页导航规范化：图标 + 选中态强化）
const SECTION_ICONS: Record<SectionKey, LucideIcon> = {
  general: SlidersHorizontal,
  externalModel: Cloud,
  internalModel: Cpu,
  webSearch: Search,
  projectRouting: Route,
  concurrency: Gauge,
  task: ListTodo,
  taskModel: Bot,
  skills: Blocks,
  webLogins: Globe,
  dataManagement: Database,
  usage: BarChart3,
}
// 自动保存静默期：连续输入/滑块拖动合并为一次落盘（0.3 §4.4）
const AUTO_SAVE_DELAY_MS = 800

type SaveState = 'idle' | 'saving' | 'saved' | 'failed'

export function SettingsPage({ settings, onSave, onSettingsReset, loadFailed = false, onRetryLoad, sectionSignal }: SettingsPageProps) {
  const { t } = useTranslation()
  // 导航必配状态点数据源：与服务异常红条同源（system:getHealth services 段）
  const { services } = useHealthServices()
  const [activeSection, setActiveSection] = useState(sectionSignal?.name ?? 'general')
  const [form, setForm] = useState<LLMSettings>({ ...settings })
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState('')
  const [testResult, setTestResult] = useState<{
    ok: boolean
    message: string
    code?: string
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
      // 2.11：保存成功广播（ActivityMonitor 即时重读 OS 通知开关，不等重启）
      window.dispatchEvent(new Event('app:settings-changed'))
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err))
      setSaveState('failed')
    }
  }, [])

  const update = <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => {
    // 加载失败态禁编辑：此时 form 是默认值基底，改动会经自动保存覆盖真实配置（D8）。
    // 失败态下 update 从未生效，不存在 pending timer，卸载 flush 无需另行防御。
    if (loadFailed) return
    setForm((prev) => ({ ...prev, [key]: value }))
    // 自动保存：改动后静默期全量落盘，连续改动合并为一次
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null
      void doSave()
    }, AUTO_SAVE_DELAY_MS)
  }

  // settings 表被清空（单类清理/全清）后的回灌入口：先取消 pending 的
  // 自动保存——否则旧 form 在 reloadSettings IPC 往返完成前抢先落盘，
  // 清理被复活（与 B2 同族竞态，窗口 = 800ms debounce）
  const handleSettingsReset = () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    onSettingsReset()
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = (await window.electronAPI.invoke('settings:personal_test', form)) as {
        ok: boolean
        message: string
        code?: string
      }
      setTestResult(res)
    } catch (err) {
      // IPC throw 路径：handlePythonResponse 已把响应体 code 挂到 err.errorCode
      const errCode = (err as { errorCode?: string })?.errorCode
      setTestResult({
        ok: false,
        message: err instanceof Error ? err.message : String(err),
        code: errCode,
      })
    } finally {
      setTesting(false)
    }
  }

  // 导航状态点映射（0.3 §4.4）：判定与后端 overall 口径一致——
  // 红（missing）= 必配缺失（2026-10-19 全必配统一 + 供应商化，readiness.OVERALL_REQUIRED_SERVICES）：
  // llm（外部模型）、web_search（网络搜索）或 embedding/parsing（内部模型）任一非 ok。
  // services 未拉到（null）按就绪处理，避免首屏闪点
  const svc = (key: string) => (services ? services[key]?.status ?? 'ok' : 'ok')
  const statusFor = (key: string): { missing?: boolean; title?: string } => {
    if (key === 'externalModel' && svc('llm') !== 'ok') {
      return { missing: true, title: t('settings.navMissingTitle') }
    }
    if (key === 'webSearch' && svc('web_search') !== 'ok') {
      return { missing: true, title: t('settings.navMissingTitle') }
    }
    if (key === 'internalModel' && (svc('embedding') !== 'ok' || svc('parsing') !== 'ok')) {
      return { missing: true, title: t('settings.navMissingTitle') }
    }
    return {}
  }

  const navGroups = SECTION_GROUPS.map((g) => ({
    label: g.label ? t(g.label) : '',
    items: g.sections.map((key) => {
      const st = statusFor(key)
      return {
        key,
        label: t(`settings.nav.${key}`),
        icon: SECTION_ICONS[key],
        missing: st.missing,
        missingTitle: st.title,
      }
    }),
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

      {loadFailed && (
        <div className={s.loadFailed} role="alert">
          <span>{t('settings.loadFailed')}</span>
          <button className="btn btn-secondary" onClick={() => void onRetryLoad?.()}>
            {t('settings.retryLoad')}
          </button>
        </div>
      )}

      <div className={s.body}>
        <SettingsNav active={activeSection} onChange={setActiveSection} groups={navGroups} />

        <div className={s.content}>
          {activeSection === 'general' && (
            <GeneralSettings settings={form} onUpdate={update} />
          )}
          {activeSection === 'externalModel' && <ExternalModelSettings form={form} onUpdate={update} />}
          {activeSection === 'internalModel' && <InternalModelSettings />}
          {activeSection === 'webSearch' && <WebSearchSettings form={form} onUpdate={update} />}
          {activeSection === 'projectRouting' && <ProjectRoutingSettings form={form} onUpdate={update} />}
          {activeSection === 'concurrency' && <ConcurrencySettings form={form} onUpdate={update} />}
          {activeSection === 'task' && <TaskSettings settings={form} onUpdate={update} />}
          {activeSection === 'taskModel' && <TaskModelSettings form={form} onUpdate={update} />}
          {activeSection === 'skills' && <SkillManagement />}
          {activeSection === 'webLogins' && <WebLogins />}
          {activeSection === 'dataManagement' && <DataManagement form={form} onUpdate={update} onSettingsReset={handleSettingsReset} />}
          {activeSection === 'usage' && <UsageStats projectSignal={sectionSignal} />}

          {showTest && (
            <div className={s.actions}>
              {testResult && (
                <div className={`${s.testResult} ${testResult.ok ? s.success : s.error}`}>
                  {apiErrorText(testResult.code, testResult.message, t)}
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
              <button className="btn btn-secondary" onClick={handleTest} disabled={testing || loadFailed}>
                {testing ? t('settings.testing') : t('settings.testConnection')}
              </button>
            </div>
          )}
        </div>
      </div>
    </main>
  )
}
