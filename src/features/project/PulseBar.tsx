/** src/features/project/PulseBar.tsx — 项目脉搏带 v1(3.0 §2/§6.1.1)
 *  状态感知层:规则拼接状态句 + chips 行;reviews 计数由调用方传入,运行态走
 *  0.4 全局心跳(useActivity);折叠偏好 per 项目落 localStorage。
 *  刷新机制:组件自身零拉取零轮询——数据由父级「挂载拉取 + 编译终态边沿补拉」注入。
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity, AlertTriangle, Loader2 } from 'lucide-react'
import { useActivity } from '../activity/ActivityContext'
import type { PulseDueSummary } from './affairs/types'
import s from './PulseBar.module.css'

export interface PulseReviewCounts { contradiction: number; gap: number }

export interface PulseBarProps {
  projectId: string
  /** 阶段显示名(状态句前缀),由调用方 t() 后传入 */
  stageLabel: string
  /** wiki:listReviews 分型计数(未裁决) */
  reviews: PulseReviewCounts
  /** 待编译文件数(调用方从 listPages 计算) */
  pendingCompile: number
  /** chip 点击;lint=治理视图 / compile=触发编译(裁决 6) */
  onOpenWiki: (view?: 'lint' | 'compile') => void
  /** 首拉/刷新失败:句子切失败文案,绝不显示「一切正常」(3.0 一期最终 review minor②) */
  loadFailed?: boolean
  /** 应办概要(3.0 二期):overdue+7 日内;null/undefined = 未加载或失败,chip 隐藏 */
  dueTasks?: PulseDueSummary | null
  /** 应办 chip 点击 → 事务 Tab */
  onOpenAffairs?: () => void
  /** AI 状态句(3.0 四期):编译时点快照,projects 行随 loadProjects 流出;空 = 无素材/未生成,回落规则句 */
  aiStatus?: string
  /** 待裁决建议数(3.0 四期);0/undefined 隐藏 chip */
  suggestionCount?: number
}

export function PulseBar({ projectId, stageLabel, reviews, pendingCompile, loadFailed, dueTasks, onOpenAffairs, onOpenWiki, aiStatus, suggestionCount }: PulseBarProps) {
  const { t } = useTranslation()
  const { byProject } = useActivity()
  // 防御性双查:byProject.compiles 与 wiki_compile items 同源(心跳 active),
  // 此子句被首子句蕴含,仅为隔离 ActivityContext 实现细节(Task2 review)
  const compileItems = useActivity('wiki_compile').items
  const act = byProject.get(projectId)
  const compiling = (act?.compiles ?? 0) > 0 || compileItems.some((a) => a.project_id === projectId)
  const reportsActive = (act?.reports ?? 0) > 0

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(`pulsebar:collapsed:${projectId}`) === '1'
  )
  // 项目切换不重挂载(App 层无 key,页面同 ProjectDetailPage 既有 re-sync 模式):
  // 重读本项目折叠偏好,防跨项目泄漏与误覆写(Task2 review major)
  useEffect(() => {
    setCollapsed(localStorage.getItem(`pulsebar:collapsed:${projectId}`) === '1')
  }, [projectId])
  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem(`pulsebar:collapsed:${projectId}`, next ? '1' : '0')
  }

  // 无培训可用性：每个 chip 必须带 tooltip 解释「这是什么、点了会发生什么」，
  // 否则新用户面对「待编译/矛盾/缺口」等自造概念无从下手
  const chips: Array<{ key: string; label: string; count?: number; cls: string; view?: 'lint' | 'compile'; tip: string }> = []
  const dueTotal = (dueTasks?.overdue ?? 0) + (dueTasks?.weekDue ?? 0)
  if ((suggestionCount ?? 0) > 0) chips.push({ key: 'suggestions', label: t('pulse.chipSuggestions'), count: suggestionCount, cls: s.chipWarn, tip: t('pulse.tipSuggestions') })
  if (dueTotal > 0) chips.push({ key: 'due', label: t('pulse.chipDue'), count: dueTotal, cls: s.chipErr, tip: t('pulse.tipDue') })
  if (reviews.contradiction > 0) chips.push({ key: 'contradiction', label: t('pulse.chipContradiction'), count: reviews.contradiction, cls: s.chipErr, view: 'lint', tip: t('pulse.tipContradiction') })
  if (reviews.gap > 0) chips.push({ key: 'gap', label: t('pulse.chipGap'), count: reviews.gap, cls: s.chipErr, view: 'lint', tip: t('pulse.tipGap') })
  if (pendingCompile > 0) chips.push({ key: 'pending', label: t('pulse.chipPendingCompile'), count: pendingCompile, cls: s.chipWarn, view: 'compile', tip: t('pulse.tipPendingCompile') })
  if (reportsActive) chips.push({ key: 'reports', label: t('pulse.chipReports'), cls: s.chipAcc, tip: t('pulse.tipReports') })

  // 状态句:AI 句最前(3.0 四期,最新鲜的编译时点快照;空/未生成回落规则链)>
  // 拉取失败 > 逾期 > 矛盾 > 缺口 > 报告生成中 > 一切正常(3.0 §2 规则拼接 v1;
  // 失败态最高优先——首拉失败时计数全 0,不得落入「一切正常」假象;
  // 行动项(逾期)比知识问题更即时,插在失败之后,二期 Task 12)
  const sentence = aiStatus
    ? aiStatus
    : loadFailed
    ? t('pulse.loadFailed')
    : dueTasks != null && dueTasks.overdue > 0
      ? t('pulse.sentenceOverdue', { count: dueTasks.overdue })
      : reviews.contradiction > 0
        ? t('pulse.sentenceContradiction', { count: reviews.contradiction })
        : reviews.gap > 0
          ? t('pulse.sentenceGap', { count: reviews.gap })
          : reportsActive
            ? t('pulse.sentenceReports')
            : t('pulse.allClear')

  if (collapsed) {
    return (
      <div className={`${s.pulse} ${s.folded}`}>
        <button className={s.foldedRow} onClick={toggle}>
          <Activity size={14} />
          <span className={s.sent}>{t('pulse.title')}</span>
          {dueTasks != null && dueTasks.overdue > 0 && (
            <span className={`${s.chip} ${s.chipErr}`}>
              <AlertTriangle size={12} />{dueTasks.overdue}
            </span>
          )}
          {reviews.contradiction > 0 && (
            <span className={`${s.chip} ${s.chipErr}`}>
              <AlertTriangle size={12} />{reviews.contradiction}
            </span>
          )}
          <span className={s.expandHint}>{t('pulse.expand')} ▾</span>
        </button>
      </div>
    )
  }
  return (
    <div className={s.pulse}>
      <div className={s.head}>
        {compiling
          ? <Loader2 size={14} className={s.spin} />
          : <span className={`${s.dot} ${chips.length ? s.dotWarn : s.dotOk}`} />}
        <span className={s.sent}>
          {stageLabel ? <><b>{stageLabel}</b> · </> : null}{sentence}
        </span>
        <button className={s.collapseBtn} onClick={toggle}>{t('pulse.collapse')} ▴</button>
      </div>
      {chips.length > 0 && (
        <div className={s.chips}>
          {chips.map((c) => (
            <button key={c.key} className={`${s.chip} ${c.cls}`}
              title={c.tip}
              onClick={() => { if (c.key === 'due' || c.key === 'suggestions') onOpenAffairs?.(); else onOpenWiki(c.view) }}>
              {c.key === 'reports' && <Loader2 size={12} className={s.spin} />}
              {c.label}{c.count != null ? ` ${c.count}` : ''}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
