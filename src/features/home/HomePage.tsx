import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, FileText, Wrench, Download, Table, FolderOutput, FolderInput, Presentation, Radio } from 'lucide-react'
import type { Project } from '../knowledge/types'
import { useActivity } from '../activity/ActivityContext'
import { navigateToNotification } from '../activity/ActivityMonitor'
import type { ActiveItem } from '../activity/types'
import { TodayTodo, type HomeTab } from './TodayTodo'
import { UpcomingCalendar } from './UpcomingCalendar'
import { HomeOverviewBar, type OverviewAnchor } from './HomeOverviewBar'
import { QuickActions } from './QuickActions'
import { ProjectCardWall, type LatestEntry } from './ProjectCardWall'
import { useRecentOutputs } from './useRecentOutputs'
import { IntelDigest } from '../schedule/IntelDigest'
import { NotificationRow } from '../activity/NotificationRow'
import { isOverdue, localISODate, type DueTask, type ProjectHealthSummary } from '../project/affairs/types'
import s from './HomePage.module.css'

/** 进行中条目图标 */
const ONGOING_ICONS = {
  file: FileText,
  wiki_compile: BookOpen,
  report: FileText,
  task: Wrench,
  model_download: Download,
  ledger_parse: BookOpen,
  form_template: FileText,
  form_fill: Table,
  project_export: FolderOutput,
  project_import: FolderInput,
  ppt: Presentation,
  schedule: Radio,
} as const

interface HomePageProps {
  projects: Project[]
  /** 项目总数（knowledge:listProjects 响应 total）：分页后 projects.length 只是已加载数 */
  projectsTotal?: number | null
  /** 卡片墙分页（0.1dev-guide 统一约定）：加载更多状态与回调 */
  hasMoreProjects?: boolean
  loadingMoreProjects?: boolean
  onLoadMoreProjects?: () => void
  onSelectProject: (id: string) => void
  /** 0.5 E1 T4:新建任务(App.handleCreateTask,与任务侧栏「新建」同链) */
  onCreateTask: () => void
  /** 0.5 E1 T4:新建项目(切项目导航 + 打开创建模态) */
  onCreateProject: () => void
  /** 3.0 三期:今日工作台单一数据快照(App 下发;null/undefined = 未就绪或降级,区块隐藏) */
  dueData?: { tasks: DueTask[]; reviews_counts: Record<string, number> } | null
  healthByProject?: Record<string, ProjectHealthSummary>
}

export function HomePage({
  projects,
  projectsTotal,
  hasMoreProjects,
  loadingMoreProjects,
  onLoadMoreProjects,
  onSelectProject,
  onCreateTask,
  onCreateProject,
  dueData,
  healthByProject,
}: HomePageProps) {
  const { t } = useTranslation()
  const { status, feed, markRead } = useActivity()
  // 卡片墙「新产出」角标(0.5 E2 T7):全局心跳快照纯派生,零新增轮询
  const newOutputsByProject = useRecentOutputs()
  // 台账解析心跳(3.0 四期 T9):活跃→终态边沿重拉最新条目快照(零新增轮询)
  const { items: ledgerParseItems } = useActivity('ledger_parse')
  const ledgerParsing = ledgerParseItems.length > 0

  // Sort projects by created_at desc (card wall 顺序;替代旧「最近项目 top5」)
  const recentProjects = [...projects]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  // ── 3.0 三期:今日工作台(§7.1)──
  const todoTasks = dueData?.tasks ?? []
  // 概览条计数(0.5 §4.2;与 TodayTodo 同源同口径:isOverdue/当日 ISO,dueData 未就绪时为 0——Ruling 4)
  const overdueCount = useMemo(() => todoTasks.filter((task) => isOverdue(task)).length, [todoTasks])
  const dueTodayCount = useMemo(
    () => todoTasks.filter((task) => !isOverdue(task) && task.due_date?.slice(0, 10) === localISODate()).length,
    [todoTasks],
  )

  /** 卡片墙「下一事务」:该项目 due 最早的任务(日期+事务名;listDue 已限逾期+7 日内) */
  const nextDueByProject = useMemo(() => {
    const acc: Record<string, { date: string; title: string | null }> = {}
    for (const task of todoTasks) {
      const due = task.due_date?.slice(0, 10)
      if (!due) continue
      const cur = acc[task.project_id]
      if (!cur || due < cur.date) acc[task.project_id] = { date: due, title: task.title }
    }
    return acc
  }, [dueData])

  // 台账最新条目快照(3.0 四期 T9):挂载拉一次 + ledger_parse 活动从有到无边沿重拉
  // (零新增轮询;拉取失败静默——摘要行是增强信息,不阻断卡片墙主体)
  const [latestEntryByProject, setLatestEntryByProject] = useState<Record<string, LatestEntry>>({})
  const loadLatestEntries = useCallback(async () => {
    try {
      const r: any = await window.electronAPI.invoke('ledger:listLatest')
      setLatestEntryByProject(
        Object.fromEntries((r?.latest ?? []).map((e: any) => [e.project_id, e])),
      )
    } catch (err) {
      console.error('Failed to load latest ledger entries:', err)
    }
  }, [])
  useEffect(() => { void loadLatestEntries() }, [loadLatestEntries])
  const prevLedgerParsingRef = useRef(ledgerParsing)
  useEffect(() => {
    if (prevLedgerParsingRef.current && !ledgerParsing) void loadLatestEntries()
    prevLedgerParsingRef.current = ledgerParsing
  }, [ledgerParsing, loadLatestEntries])

  /** 待办/日历直达:进项目详情对应 Tab(affairs/materials/reports);
   *  taskId=待办行点击携带 → 事务 Tab + 自动打开执行抽屉(§6.1.8 直达执行动线) */
  const handleOpenProjectTab = (projectId: string, tab: HomeTab, taskId?: string) => {
    window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
      detail: { projectId, tab, container: 'project', taskId },
    }))
  }

  /** 概览条锚点跳转(0.5 §4.2):projects→卡片墙,逾期/今日→今日待办,进行中→进行中区 */
  const wallRef = useRef<HTMLDivElement>(null)
  const todoRef = useRef<HTMLDivElement>(null)
  const activityRef = useRef<HTMLDivElement>(null)
  const handleJump = (anchor: OverviewAnchor) => {
    const targets = { wall: wallRef, todo: todoRef, activity: activityRef } as const
    targets[anchor].current?.scrollIntoView({ behavior: 'smooth' })
  }

  /** 进行中条目点击导航（0.4 §5.4）：项目类 → 项目 Tab / 基础库详情页（container 分流，0.4 §3.6），
   *  任务 → 任务页，模型 → 设置页，填表模板 → 模板管理页（2.4 §3.4：全局资产无项目实体），
   *  PPT 模板任务（container='templates'）→ 模板管理页 PPT模板 Tab（2.3 §4.2），
   *  订阅期次执行（schedule，全局资产 2.11 §2）→ 订阅管理台 */
  const handleOngoingClick = (item: ActiveItem) => {
    if (item.entity_type === 'task') {
      window.dispatchEvent(new CustomEvent('app:navigate-tasks'))
    } else if (item.entity_type === 'model_download') {
      window.dispatchEvent(new CustomEvent('app:navigate-settings', { detail: { section: 'internalModel' } }))
    } else if (item.entity_type === 'schedule') {
      window.dispatchEvent(new CustomEvent('app:navigate-schedules'))
    } else if (item.entity_type === 'form_template') {
      window.dispatchEvent(new CustomEvent('app:navigate-template-management'))
    } else if (item.entity_type === 'ppt' && item.container === 'templates') {
      window.dispatchEvent(new CustomEvent('app:navigate-template-management', { detail: { section: 'ppt' } }))
    } else if (item.project_id) {
      // ledger_parse 是编译下游(台账解析),点击同样归 wiki Tab(3.0 四期);填表实例归 forms Tab(2.4);
      // 导出/导入(2.9)主体是项目数据资料,归 materials Tab
      const tab = item.entity_type === 'file'
        || item.entity_type === 'project_export' || item.entity_type === 'project_import' ? 'materials'
        : item.entity_type === 'wiki_compile' || item.entity_type === 'ledger_parse' ? 'wiki'
        : item.entity_type === 'form_fill' ? 'forms' : 'reports'
      window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
        detail: { projectId: item.project_id, tab, container: item.container ?? 'project' },
      }))
    }
  }

  /** 进行中条目文案 */
  const ongoingText = (item: ActiveItem): string => {
    switch (item.entity_type) {
      case 'file':
        return t('activity.ongoing.file', { project: item.project_name, count: item.count ?? 0 })
      case 'wiki_compile':
        return t('activity.ongoing.wiki_compile', {
          project: item.project_name,
          progress: item.progress_text ? ` ${item.progress_text}` : '',
        })
      case 'report':
        return t('activity.ongoing.report', {
          project: item.project_name,
          title: item.entity_title,
          progress: item.progress ?? 0,
        })
      case 'task':
        return t('activity.ongoing.task', { title: item.entity_title })
      case 'schedule':
        return t('activity.ongoing.schedule', { title: item.entity_title })
      case 'ledger_parse':
        return t('activity.ongoing.ledger_parse', { project: item.project_name })
      case 'form_template':
        return t('activity.ongoing.form_template', {
          title: item.entity_title,
          status: item.status === 'analyzing' ? t('activity.ongoing.formAnalyzing') : t('activity.ongoing.formPending'),
        })
      case 'form_fill':
        return t('activity.ongoing.form_fill', {
          project: item.project_name,
          title: item.entity_title,
          progress: item.progress ?? 0,
        })
      case 'ppt':
        return t('activity.ongoing.ppt', {
          project: item.project_name,
          title: item.entity_title,
        })
      case 'project_export':
        return t('activity.ongoing.project_export', { title: item.entity_title })
      case 'project_import':
        return t('activity.ongoing.project_import', {
          title: item.entity_title,
          progress: item.progress ?? 0,
        })
      default:
        return t('activity.ongoing.model_download', { title: item.entity_title, progress: item.progress ?? 0 })
    }
  }

  const ongoingItems = status?.active ?? []
  const feedItems = feed.slice(0, 8)

  return (
    <div className={s.page}>
      {/* ── Overview stat cards (0.5 §4.2 打样修订:大数字+标签,点击滚动锚点) ── */}
      <HomeOverviewBar
        projectCount={projectsTotal ?? projects.length}
        overdue={overdueCount}
        dueToday={dueTodayCount}
        activeCount={ongoingItems.length}
        onJump={handleJump}
      />

      {/* ── Dashboard grid:主列(进行中/今日待办+日历/卡片墙) + 侧列(动态流/快捷动作) ── */}
      <div className={s.grid}>
        <div className={s.main}>
          {/* Ongoing (0.4 §5.4 导航;空态保留——Ruling 4;概览「进行中」锚点) */}
          <section ref={activityRef} className={s.block}>
            <div className={s.sectionTitle}>{t('home.ongoingTitle')}</div>
            {ongoingItems.length === 0 ? (
              <div className="empty-block">{t('home.noOngoing')}</div>
            ) : (
              <div className={s.ongoingList}>
                {ongoingItems.map((item) => {
                  const Icon = ONGOING_ICONS[item.entity_type] ?? FileText
                  return (
                    <div key={`${item.entity_type}-${item.entity_id ?? item.project_id}`} className={s.ongoingItem} onClick={() => handleOngoingClick(item)}>
                      <span className={s.ongoingIcon}><Icon size={14} /></span>
                      <span className={s.ongoingText}>{ongoingText(item)}</span>
                      <span className={s.ongoingSpinner} />
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          {/* Today todo + calendar (空态保留——Ruling 4;概览「逾期/今日」锚点) */}
          <section ref={todoRef} className={s.block}>
            <TodayTodo tasks={todoTasks} onOpenProject={handleOpenProjectTab} />
            <UpcomingCalendar tasks={todoTasks} onOpenProject={handleOpenProjectTab} />
          </section>

          {/* Project card wall (概览「项目」锚点) */}
          <section className={s.block}>
            <div ref={wallRef} className={s.sectionTitle}>{t('homeTodo.cardWallTitle')}</div>
            <ProjectCardWall
              projects={recentProjects}
              healthData={healthByProject}
              nextDueByProject={nextDueByProject}
              latestEntryByProject={latestEntryByProject}
              newOutputsByProject={newOutputsByProject}
              onSelect={(id) => {
                onSelectProject(id)
                window.dispatchEvent(new CustomEvent('app:navigate-project-tab', { detail: { projectId: id, container: 'project' } }))
              }}
            />
            {hasMoreProjects && (
              <button
                className={s.loadMoreBtn}
                onClick={onLoadMoreProjects}
                disabled={loadingMoreProjects}
              >
                {loadingMoreProjects ? t('common.loading') : t('common.loadMore')}
              </button>
            )}
          </section>
        </div>

        <aside className={s.side}>
          {/* 今日情报速览（0.5 §4.2 区块⑥：侧列顶部，E3 T9；行点击 → App 切 scheduleView read） */}
          <IntelDigest
            onOpen={(runId) => window.dispatchEvent(new CustomEvent('app:navigate-schedule-read', { detail: { runId } }))}
            onOpenAdmin={() => window.dispatchEvent(new CustomEvent('app:navigate-schedules'))}
          />
          {/* Recent activity feed (Activity Hub, 0.4 §5.4);行渲染抽 NotificationRow 共享（2.11 §6.1） */}
          <section className={s.block}>
            <div className={`${s.sectionTitle} ${s.titleRow}`}>
              <span>{t('home.recentFeedTitle')}</span>
              <button
                className={s.viewAll}
                onClick={() => window.dispatchEvent(new CustomEvent('app:open-notifications'))}
              >
                {t('home.viewAllNotifications')}
              </button>
            </div>
            <div className={s.recentList}>
              {feedItems.length === 0 ? (
                <div className="empty-block">{t('home.noFeed')}</div>
              ) : (
                feedItems.map((n) => (
                  <NotificationRow
                    key={n.id}
                    item={n}
                    onOpen={(item) => {
                      void markRead([item.id])
                      navigateToNotification(item)
                    }}
                  />
                ))
              )}
            </div>
          </section>

          {/* Quick actions (0.5 §4.2:动作化——新建任务/新建项目) */}
          <section className={s.block}>
            <div className={s.sectionTitle}>{t('home.quickTitle')}</div>
            <QuickActions onCreateTask={onCreateTask} onCreateProject={onCreateProject} />
          </section>
        </aside>
      </div>
    </div>
  )
}
