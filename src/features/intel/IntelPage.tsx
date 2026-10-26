/**
 * IntelPage（0.6 §3.2 V2-E1a/E2；两 Tab 收敛 2026-10-25）：情报中心页。
 * 子 Tab：订阅管理（SubscriptionAdminView，默认——收件箱即跨订阅期次消费面）/
 * 线索池（LeadPool，V2-E2）。原「情报流」Tab 退役：inbox 卡片流与收件箱数据
 * 同源双呈现属未收敛残影，能力（排序/分类过滤/边沿刷新）已迁入收件箱。
 * 期次 read/replay/archive 由 App 级 ScheduleDrawer 右侧抽屉承载（第二步三态
 * 统一 2026-10-25）——本页不被替换卸载，tab 与订阅选中在抽屉开关间天然保持；
 * 线索立项/升级订阅经 props 上抛 App 层（createProject / ScheduleFormModal prefill）。
 */
import { useTranslation } from 'react-i18next'
import { LeadPool, type LeadRow } from './LeadPool'
import { SubscriptionAdminView } from '../schedule/SubscriptionAdminView'
import type { ProjectType } from '../project/types'
import type { ScheduleRow, ScheduleRunRow } from '../schedule/useSchedules'
import s from './IntelPage.module.css'

interface IntelPageProps {
  /** 当前子 Tab（App 级 intelTab，抽屉开关间天然保持） */
  tab: 'admin' | 'leads'
  onTabChange: (tab: 'admin' | 'leads') => void
  /** 期次打开 → App 开期次阅读抽屉（ScheduleDrawer read 态） */
  onOpenRead: (run: ScheduleRunRow) => void
  /** 管理台「查看档案」→ App 开档案抽屉（ScheduleDrawer archive 态） */
  onOpenArchive: (row: ScheduleRow) => void
  /** 线索立项 = App 层 knowledge:createProject（失败 toast 留原页）；
   *  五参与 ProjectList.onCreate 同构（2026-10-25 签名对位根修） */
  onConvertProject: (
    name: string, description: string, type: ProjectType,
    companyName?: string, creditCode?: string,
  ) => Promise<{ id?: string } | undefined>
  /** 已立项卡「查看项目」→ App 切项目页 */
  onOpenProject: (projectId: string) => void
  /** 线索升级订阅 → App 级 ScheduleFormModal（跟踪 prompt 预填） */
  onUpgrade: (lead: LeadRow) => void
}

export function IntelPage({
  tab, onTabChange, onOpenRead, onOpenArchive,
  onConvertProject, onOpenProject, onUpgrade,
}: IntelPageProps) {
  const { t } = useTranslation()

  return (
    <div className={s.page} data-testid="intel-page">
      <div className={s.header}>
        <h1 className={s.title}>{t('intel.title')}</h1>
      </div>
      <div className={s.tabs} role="tablist">
        {(['admin', 'leads'] as const).map((tb) => (
          <button
            key={tb}
            type="button"
            role="tab"
            aria-selected={tab === tb}
            className={`${s.tab} ${tab === tb ? s.tabActive : ''}`}
            onClick={() => onTabChange(tb)}
          >
            {t(`intel.tab.${tb}`)}
          </button>
        ))}
      </div>
      {tab === 'leads' ? (
        <LeadPool
          onConvertProject={onConvertProject}
          onOpenProject={onOpenProject}
          onUpgrade={onUpgrade}
          onGoAdmin={() => onTabChange('admin')}
        />
      ) : (
        <SubscriptionAdminView
          onOpenRead={onOpenRead}
          onOpenArchive={onOpenArchive}
        />
      )}
    </div>
  )
}
