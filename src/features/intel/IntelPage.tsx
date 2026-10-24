/**
 * IntelPage（0.6 §3.2/§6.1 V2-E1a/E2）：情报中心页 —— 三子 Tab 导航。
 * 子 Tab：情报流（IntelFeed，inbox 卡片流）/ 线索池（LeadPool，V2-E2）/
 * 订阅管理（SubscriptionAdminView 迁入）。页头「新建订阅」主钮 = 全局唯一新建
 * 入口（feed 态可见）：切管理台 tab + createSignal 直开弹层。
 * read/replay/archive 仍由 App 级 scheduleView 承载（本页经 props 回调切出）；
 * 线索立项/升级订阅经 props 上抛 App 层（createProject / ScheduleFormModal prefill）。
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import { IntelFeed } from './IntelFeed'
import { LeadPool, type LeadRow } from './LeadPool'
import { SubscriptionAdminView } from '../schedule/SubscriptionAdminView'
import type { ProjectType } from '../project/types'
import type { ScheduleRow, ScheduleRunRow } from '../schedule/useSchedules'
import s from './IntelPage.module.css'

interface IntelPageProps {
  /** 当前子 Tab（App 级 intelTab，read 返回时保持来路 tab） */
  tab: 'feed' | 'leads' | 'admin'
  onTabChange: (tab: 'feed' | 'leads' | 'admin') => void
  /** 期次打开 → App 切 scheduleView read 态（from='intel'；action='extract' 产业卡快捷直达） */
  onOpenRead: (run: ScheduleRunRow, action?: 'extract') => void
  /** 管理台「查看档案」→ App 切 scheduleView archive 态 */
  onOpenArchive: (row: ScheduleRow) => void
  /** 线索立项 = App 层 knowledge:createProject（失败 toast 留原页） */
  onConvertProject: (name: string, type: ProjectType) => Promise<{ id?: string } | undefined>
  /** 已立项卡「查看项目」→ App 切项目页 */
  onOpenProject: (projectId: string) => void
  /** 线索升级订阅 → App 级 ScheduleFormModal（盯梢 prompt 预填） */
  onUpgrade: (lead: LeadRow) => void
  /** read/replay/archive 返回管理台时恢复选中订阅（仅挂载消费） */
  restoreScheduleId?: string
}

export function IntelPage({
  tab, onTabChange, onOpenRead, onOpenArchive,
  onConvertProject, onOpenProject, onUpgrade, restoreScheduleId,
}: IntelPageProps) {
  const { t } = useTranslation()
  // 「新建订阅」主钮信号：切管理台 tab 后直开弹层（SubscriptionAdminView 消费）
  const [createSignal, setCreateSignal] = useState(0)

  return (
    <div className={s.page} data-testid="intel-page">
      <div className={s.header}>
        <h1 className={s.title}>{t('intel.title')}</h1>
        {tab === 'feed' && (
          <button
            type="button"
            className={s.newBtn}
            onClick={() => {
              onTabChange('admin')
              setCreateSignal((v) => v + 1)
            }}
          >
            <Plus size={14} />{t('schedule.newSubscription')}
          </button>
        )}
      </div>
      <div className={s.tabs} role="tablist">
        {(['feed', 'leads', 'admin'] as const).map((tb) => (
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
      {tab === 'feed' ? (
        <IntelFeed onOpenRead={onOpenRead} onOpenAdmin={() => onTabChange('admin')} />
      ) : tab === 'leads' ? (
        <LeadPool
          onConvertProject={onConvertProject}
          onOpenProject={onOpenProject}
          onUpgrade={onUpgrade}
          onGoFeed={() => onTabChange('feed')}
        />
      ) : (
        <SubscriptionAdminView
          // 管理台无提取快捷钮：收窄签名转发（两参函数直传会窄化不兼容）
          onOpenRead={(run) => onOpenRead(run)}
          onOpenArchive={onOpenArchive}
          restoreScheduleId={restoreScheduleId}
          createSignal={createSignal}
          onCreateSignalConsumed={() => setCreateSignal(0)}
        />
      )}
    </div>
  )
}
