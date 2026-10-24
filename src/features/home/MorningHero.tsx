/**
 * src/features/home/MorningHero.tsx — 首页晨会英雄区（0.6 §3.1① V2-E1b）。
 * 概览统计卡阵列退役（§7）升格：display 级日期问候 + 晨报句（规则拼装，
 * V2.1 接 AI 摘要）+ KPI 降为行内文本链（滚动锚点行为保留）。
 * 数据零新增：项目/待办/进行中由 HomePage 下发（dueData + useActivity 派生）；
 * 跟踪/新情报 = useSchedules().totals（schedules:summary 既有通道，与 NavTabs
 * 徽章同源——HomePage 侧直读下发，本组件不重复拉取）。无卡片容器直接排于
 * 页底色（§3.0-2 去框化），fade-up 入场（§3.0-6）。
 */
import { useTranslation } from 'react-i18next'
import s from './MorningHero.module.css'

export type OverviewAnchor = 'wall' | 'todo' | 'activity'

interface MorningHeroProps {
  /** 跟踪订阅数（useSchedules totals.n） */
  trackingCount: number
  /** 新情报未读数（useSchedules totals.total） */
  unreadIntel: number
  projectCount: number
  overdue: number
  dueToday: number
  activeCount: number
  /** KPI 链点击 → HomePage 对应区块 scrollIntoView（wall→卡片墙 / todo→今日待办 / activity→进行中区） */
  onJump: (anchor: OverviewAnchor) => void
}

export function MorningHero({
  trackingCount,
  unreadIntel,
  projectCount,
  overdue,
  dueToday,
  activeCount,
  onJump,
}: MorningHeroProps) {
  const { t, i18n } = useTranslation()
  const now = new Date()
  const hour = now.getHours()
  const greeting = hour < 12
    ? t('home.hero.greetingMorning')
    : hour < 18
      ? t('home.hero.greetingAfternoon')
      : t('home.hero.greetingEvening')
  const dateStr = now.toLocaleDateString(i18n.language || 'zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  })
  const kpis: Array<{ anchor: OverviewAnchor; label: string; num: number; warn?: boolean }> = [
    { anchor: 'wall', label: t('home.stat.projects'), num: projectCount },
    { anchor: 'todo', label: t('home.stat.overdue'), num: overdue, warn: overdue > 0 },
    { anchor: 'todo', label: t('home.stat.dueToday'), num: dueToday },
    { anchor: 'activity', label: t('home.stat.active'), num: activeCount },
  ]
  return (
    <section className={s.hero} data-testid="morning-hero">
      <h1 className={s.greeting}>
        {greeting}
        <span className={s.date}>{dateStr}</span>
      </h1>
      <p className={s.briefing}>
        {t('home.hero.briefing', { tracking: trackingCount, intel: unreadIntel, todo: dueToday })}
      </p>
      <nav className={s.kpis}>
        {kpis.map((k) => (
          <button
            key={`${k.anchor}-${k.label}`}
            type="button"
            className={s.kpi}
            data-testid="hero-kpi"
            onClick={() => onJump(k.anchor)}
          >
            <span className={s.kpiLabel}>{k.label}</span>
            <span className={`${s.kpiNum} ${k.warn ? s.kpiNumWarn : ''}`}>{k.num}</span>
          </button>
        ))}
      </nav>
    </section>
  )
}
