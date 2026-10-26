import { useTranslation } from 'react-i18next'
import type { LLMSettings } from '../../types'
import styles from './GeneralSettings.module.css'

// persona 值域白名单（review S1 守卫）：后端 schemas.py 为裸 str 无枚举校验，
// DB 脏值（手工改库等异常前提）不得漏进 UI——否则动态 i18n key 渲染裸 key、
// select 匹配不到 option 呈空白态。选项列表由本常量派生，本文件内单源
const PERSONA_KEYS = ['business_attraction', 'investment', 'both', 'other'] as const

/** 白名单外（含未设置 ''）一律归一为未设置，hint 与 select value 共用同一归一值 */
function normalizePersona(raw: string | undefined): string {
  return (PERSONA_KEYS as readonly string[]).includes(raw ?? '') ? (raw as string) : ''
}

interface GeneralSettingsProps {
  settings?: LLMSettings
  onUpdate?: <K extends keyof LLMSettings>(key: K, value: LLMSettings[K]) => void
}

export function GeneralSettings({ settings, onUpdate }: GeneralSettingsProps) {
  const { t, i18n } = useTranslation()
  const persona = normalizePersona(settings?.persona)

  const handleLanguageChange = (lng: string) => {
    i18n.changeLanguage(lng)
  }

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{t('settings.general')}</h3>

      <label className={styles.label}>{t('settings.language')}</label>
      <select
        className={styles.select}
        value={i18n.language}
        onChange={(e) => handleLanguageChange(e.target.value)}
      >
        <option value="zh-CN">中文</option>
        <option value="en-US">English</option>
      </select>

      {/* 身份（0.6 §3.8 设置页身份入口，2026-10-25 触发落地）：首启 PersonaPicker
          选定后的修改入口，读改 settings.persona 走自动保存链即时生效（模板/
          订阅排序实时重排，promptTemplate.sortTemplatesByPersona），零新通道。
          hint 动态消费 onboarding.persona.*.desc——选中态解释当前身份，
          未设置态说明驱动面（§2.10 既有信息必须消费到决策点） */}
      <label className={styles.label}>{t('settings.identity')}</label>
      <select
        className={styles.select}
        value={persona}
        onChange={(e) => onUpdate?.('persona', e.target.value as LLMSettings['persona'])}
      >
        <option value="">{t('settings.identityUnset')}</option>
        {PERSONA_KEYS.map((key) => (
          <option key={key} value={key}>{t(`onboarding.persona.${key}.label`)}</option>
        ))}
      </select>
      <p className={styles.hint}>
        {persona
          ? t(`onboarding.persona.${persona}.desc`)
          : t('settings.identityUnsetHint')}
      </p>

      {/* Notification (2.11 §6.1)：OS 通知开关即时生效（SettingsPage 保存后广播
          app:settings-changed，ActivityMonitor 重读） */}
      <h3 className={styles.heading}>{t('settings.notification.title')}</h3>
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.osEnabled')}</span>
          <p className={styles.hint}>{t('settings.notification.osEnabledHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.os_notify_enabled ?? true}
            onChange={(e) => onUpdate?.('os_notify_enabled', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.sound')}</span>
          <p className={styles.hint}>{t('settings.notification.soundHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.os_notify_sound ?? true}
            onChange={(e) => onUpdate?.('os_notify_sound', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.affairReminder')}</span>
          <p className={styles.hint}>{t('settings.notification.affairReminderHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.affair_reminder_enabled ?? true}
            onChange={(e) => onUpdate?.('affair_reminder_enabled', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>
      {/* R4 订阅完成通知（review P2-7 提前落地）：默认关闭 = 静默（通知中心
          可见+静默已读）；开启后 completed 实时弹 Toast/OS 通知 */}
      <div className={styles.toggleRow}>
        <div className={styles.toggleLabel}>
          <span className={styles.label}>{t('settings.notification.scheduleNotify')}</span>
          <p className={styles.hint}>{t('settings.notification.scheduleNotifyHint')}</p>
        </div>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={settings?.schedule_notify ?? false}
            onChange={(e) => onUpdate?.('schedule_notify', e.target.checked)}
          />
          <span className={styles.toggleSlider}></span>
        </label>
      </div>

      {/* 处理并发已移至「项目」组 ConcurrencySettings（0.3 §4.4 五组导航 2026-10-19） */}
    </div>
  )
}
