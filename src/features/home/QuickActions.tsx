/** src/features/home/QuickActions.tsx — 首页快捷动作(0.5 §4.2:动作化——新建任务/新建项目,
 *  替代旧「打开任务/打开项目」导航卡；0.6 §3.2 V2-E1a T10：第三卡新建订阅) */
import { useTranslation } from 'react-i18next'
import { FilePlus2, FolderPlus, Radar } from 'lucide-react'
import s from './QuickActions.module.css'

interface QuickActionsProps {
  /** = App.handleCreateTask:直接发起新任务(与任务侧栏「新建」同链) */
  onCreateTask: () => void
  /** = 切到项目导航 + 打开 ProjectList 创建模态(纯前端信号) */
  onCreateProject: () => void
  /** = App.openIntelAdmin:情报中心管理台 tab(弹层经页头「新建订阅」钮直开,0.6 §3.2) */
  onCreateSchedule: () => void
}

export function QuickActions({ onCreateTask, onCreateProject, onCreateSchedule }: QuickActionsProps) {
  const { t } = useTranslation()
  return (
    <div className={s.grid}>
      <button type="button" className={s.card} onClick={onCreateTask}>
        <span className={s.icon}><FilePlus2 size={24} /></span>
        <span className={s.text}>
          <span className={s.label}>{t('home.quick.newTask')}</span>
          <span className={s.desc}>{t('home.quick.newTaskDesc')}</span>
        </span>
      </button>
      <button type="button" className={s.card} onClick={onCreateProject}>
        <span className={s.icon}><FolderPlus size={24} /></span>
        <span className={s.text}>
          <span className={s.label}>{t('home.quick.newProject')}</span>
          <span className={s.desc}>{t('home.quick.newProjectDesc')}</span>
        </span>
      </button>
      <button type="button" className={s.card} onClick={onCreateSchedule}>
        <span className={s.icon}><Radar size={24} /></span>
        <span className={s.text}>
          <span className={s.label}>{t('home.quick.newSchedule')}</span>
          <span className={s.desc}>{t('home.quick.newScheduleDesc')}</span>
        </span>
      </button>
    </div>
  )
}
