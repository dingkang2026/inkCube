/** src/features/knowledge/ProjectWelcomeGuide.tsx — 0.5 E1 T6:零项目三步引导卡(0.5 §4.3)。
 *  创建走 ProjectList 创建模态同一回调链:App 注入 handleCreateProject,
 *  链内含 loadProjects + 自动选中新项目 + 失败 toast;成功后本组件随欢迎态被详情页替换。 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PROJECT_TYPE_CONFIG } from '../project/types'
import type { ProjectType } from '../project/types'
import s from './ProjectWelcomeGuide.module.css'

/** 与 ProjectList onCreate 同签名(App handleCreateProject) */
export type CreateProjectFn = (
  name: string,
  description?: string,
  type?: ProjectType,
  companyName?: string,
  creditCode?: string,
) => Promise<{ id?: string; seeded_tasks_count?: number } | undefined>

const TYPE_CARDS: { key: ProjectType; labelKey: string; descKey: string }[] = [
  { key: 'recruitment', labelKey: 'guide.typeRecruit', descKey: 'guide.typeRecruitDesc' },
  { key: 'investment', labelKey: 'guide.typeInvest', descKey: 'guide.typeInvestDesc' },
  { key: 'general', labelKey: 'guide.typeGeneral', descKey: 'guide.typeGeneralDesc' },
]

export function ProjectWelcomeGuide({ onCreate }: { onCreate: CreateProjectFn }) {
  const { t } = useTranslation()
  const [type, setType] = useState<ProjectType | null>(null)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)

  const canCreate = !!type && !!name.trim() && !creating

  const handleCreate = async () => {
    if (!type || !name.trim() || creating) return
    setCreating(true)
    try {
      // 成功:App 已 loadProjects + 选中(欢迎态卸载);失败:App 已 toast,状态保留可重试
      await onCreate(name.trim(), undefined, type)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className={s.guide}>
      <h2 className={s.title}>{t('guide.title')}</h2>
      <p className={s.subtitle}>{t('guide.subtitle')}</p>

      <div className={s.step}>
        <div className={s.stepHead}>
          <span className={s.stepNum}>1</span>
          {t('guide.step1')}
        </div>
        <div className={s.stepHint}>{t('guide.step1Hint')}</div>
        <div className={s.typeCards}>
          {TYPE_CARDS.map(({ key, labelKey, descKey }) => {
            const Icon = PROJECT_TYPE_CONFIG[key].Icon
            return (
              <button
                key={key}
                className={`${s.typeCard} ${type === key ? s.typeCardActive : ''}`}
                aria-pressed={type === key}
                onClick={() => setType(key)}
              >
                <span className={s.typeIcon}><Icon size={20} /></span>
                <span className={s.typeLabel}>{t(labelKey)}</span>
                <span className={s.typeDesc}>{t(descKey)}</span>
              </button>
            )
          })}
        </div>
      </div>

      <div className={s.step}>
        <div className={s.stepHead}>
          <span className={s.stepNum}>2</span>
          {t('guide.step2')}
        </div>
        <input
          className={s.nameInput}
          placeholder={t('guide.namePlaceholder')}
          value={name}
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && canCreate) void handleCreate() }}
        />
      </div>

      <button className={s.createBtn} disabled={!canCreate} onClick={() => void handleCreate()}>
        {t('guide.create')}
      </button>
    </div>
  )
}
