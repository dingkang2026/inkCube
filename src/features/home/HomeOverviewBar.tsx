/** src/features/home/HomeOverviewBar.tsx — 首页概览统计卡（0.5 §4.2 打样修订 2026-10-18：
 *  四张统计卡（标签 + 大数字 + 淡色图标），点击滚动到对应区块；空数据显示 0 不隐藏
 *  （Ruling 4 2026-10-18）；逾期 >0 红色强调、进行中 >0 蓝色数字 + 脉冲点 */
import { useTranslation } from 'react-i18next'
import { FolderOpen, AlertCircle, CalendarClock, Activity } from 'lucide-react'
import s from './HomeOverviewBar.module.css'

export type OverviewAnchor = 'wall' | 'todo' | 'activity'

interface HomeOverviewBarProps {
  projectCount: number
  overdue: number
  dueToday: number
  activeCount: number
  /** 卡点击 → HomePage 对应区块 scrollIntoView(projects→卡片墙,逾期/今日→今日待办,进行中→进行中区) */
  onJump: (anchor: OverviewAnchor) => void
}

export function HomeOverviewBar({ projectCount, overdue, dueToday, activeCount, onJump }: HomeOverviewBarProps) {
  const { t } = useTranslation()
  return (
    <div className={s.bar}>
      <button type="button" className={s.card} data-anchor="wall" onClick={() => onJump('wall')}>
        <span className={s.top}>
          <span className={s.label}>{t('home.stat.projects')}</span>
          <FolderOpen size={15} className={s.icon} />
        </span>
        <span className={s.num}>{projectCount}</span>
      </button>
      <button
        type="button"
        className={`${s.card} ${overdue > 0 ? s.cardWarn : ''}`}
        data-anchor="todo"
        onClick={() => onJump('todo')}
      >
        <span className={s.top}>
          <span className={s.label}>{t('home.stat.overdue')}</span>
          <AlertCircle size={15} className={overdue > 0 ? s.iconWarn : s.icon} />
        </span>
        <span className={`${s.num} ${overdue > 0 ? s.numWarn : ''}`}>{overdue}</span>
      </button>
      <button type="button" className={s.card} data-anchor="todo" onClick={() => onJump('todo')}>
        <span className={s.top}>
          <span className={s.label}>{t('home.stat.dueToday')}</span>
          <CalendarClock size={15} className={s.icon} />
        </span>
        <span className={s.num}>{dueToday}</span>
      </button>
      <button
        type="button"
        className={`${s.card} ${activeCount > 0 ? s.cardActive : ''}`}
        data-anchor="activity"
        onClick={() => onJump('activity')}
      >
        <span className={s.top}>
          <span className={s.label}>{t('home.stat.active')}</span>
          <Activity size={15} className={activeCount > 0 ? s.iconActive : s.icon} />
        </span>
        <span className={`${s.num} ${activeCount > 0 ? s.numActive : ''}`}>
          {activeCount}
          {activeCount > 0 && <span className={s.pulse} />}
        </span>
      </button>
    </div>
  )
}
