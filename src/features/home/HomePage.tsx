import { useState, useEffect, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, X, BookOpen, Folder, FileText, Wrench, Download } from 'lucide-react'
import type { HealthStatus } from '../../types/error'
import type { Project } from '../knowledge/types'
import { useActivity } from '../activity/ActivityContext'
import { navigateToNotification } from '../activity/ActivityMonitor'
import type { ActiveItem } from '../activity/types'
import { TodayTodo, type HomeTab } from './TodayTodo'
import { UpcomingCalendar } from './UpcomingCalendar'
import { ProjectCardWall } from './ProjectCardWall'
import type { DueTask, ProjectHealthSummary } from '../project/affairs/types'
import s from './HomePage.module.css'

const SERVICE_LABELS: Record<string, string> = {
  llm: 'LLM',
  vlm: 'VLM',
  embedding: 'Embedding',
  parsing: 'parsing',
}

/** 服务 → 设置页锚点 section（0.4 §7.4：红卡可点击直达对应区块） */
const SERVICE_SECTIONS: Record<string, string> = {
  llm: 'externalModel',
  vlm: 'externalModel',
  embedding: 'internalModel',
  parsing: 'internalModel',
}

/** 进行中条目图标 */
const ONGOING_ICONS = {
  file: FileText,
  wiki_compile: BookOpen,
  report: FileText,
  task: Wrench,
  model_download: Download,
} as const

interface HomePageProps {
  projects: Project[]
  onNavigate: (nav: string) => void
  onOpenSettings: (section?: string) => void
  onSelectProject: (id: string) => void
  /** 3.0 三期:今日工作台单一数据快照(App 下发;null/undefined = 未就绪或降级,区块隐藏) */
  dueData?: { tasks: DueTask[]; reviews_counts: Record<string, number> } | null
  healthByProject?: Record<string, ProjectHealthSummary>
}

export function HomePage({
  projects,
  onNavigate,
  onOpenSettings,
  onSelectProject,
  dueData,
  healthByProject,
}: HomePageProps) {
  const { t } = useTranslation()
  const { status, feed, markRead } = useActivity()
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [healthLoading, setHealthLoading] = useState(true)

  const fetchHealth = useCallback(async () => {
    setHealthLoading(true)
    try {
      const result = await window.electronAPI.invoke('system:getHealth') as HealthStatus
      setHealth(result)
    } catch {
      // Health check failed — treat as down
      setHealth({ status: 'down', services: {}, errorCode: 'PYTHON_SERVICE_DOWN' })
    } finally {
      setHealthLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchHealth()
  }, [fetchHealth])

  // Sort projects by created_at desc (card wall 顺序;替代旧「最近项目 top5」)
  const recentProjects = [...projects]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  // ── 3.0 三期:今日工作台(§7.1)──
  const todoTasks = dueData?.tasks ?? []

  /** 卡片墙「下一事务」:该项目 due 最早的日期(listDue 已限逾期+7 日内) */
  const nextDueByProject = useMemo(() => {
    const acc: Record<string, string | null> = {}
    for (const task of todoTasks) {
      const due = task.due_date?.slice(0, 10)
      if (!due) continue
      const cur = acc[task.project_id]
      if (!cur || due < cur) acc[task.project_id] = due
    }
    return acc
  }, [dueData])

  /** 待办/日历直达:进项目详情对应 Tab(affairs/materials/reports);
   *  taskId=待办行点击携带 → 事务 Tab + 自动打开执行抽屉(§6.1.8 直达执行动线) */
  const handleOpenProjectTab = (projectId: string, tab: HomeTab, taskId?: string) => {
    window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
      detail: { projectId, tab, container: 'project', taskId },
    }))
  }

  // 红卡可点击 → 直达设置对应区块（0.4 §7.4；绿卡不可点）
  const handleStatusClick = (serviceKey: string) => {
    onOpenSettings(SERVICE_SECTIONS[serviceKey] || 'general')
  }

  /** 进行中条目点击导航（0.4 §5.4）：项目类 → 项目 Tab / 基础库详情页（container 分流，0.4 §3.6），
   *  任务 → 任务页，模型 → 设置页 */
  const handleOngoingClick = (item: ActiveItem) => {
    if (item.entity_type === 'task') {
      window.dispatchEvent(new CustomEvent('app:navigate-tasks'))
    } else if (item.entity_type === 'model_download') {
      window.dispatchEvent(new CustomEvent('app:navigate-settings', { detail: { section: 'internalModel' } }))
    } else if (item.project_id) {
      const tab = item.entity_type === 'file' ? 'materials' : item.entity_type === 'wiki_compile' ? 'wiki' : 'reports'
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
      default:
        return t('activity.ongoing.model_download', { title: item.entity_title, progress: item.progress ?? 0 })
    }
  }

  /** 相对时间（分钟/小时/天） */
  const relativeTime = (iso: string): string => {
    const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
    if (minutes < 1) return t('activity.time.justNow')
    if (minutes < 60) return t('activity.time.minutesAgo', { count: minutes })
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return t('activity.time.hoursAgo', { count: hours })
    return t('activity.time.daysAgo', { count: Math.floor(hours / 24) })
  }

  const services = health?.services ? Object.entries(health.services) : []
  const allServiceKeys = ['llm', 'vlm', 'embedding', 'parsing']
  const ongoingItems = status?.active ?? []
  const feedItems = feed.slice(0, 8)

  return (
    <div className={s.page}>
      {/* ── Top: Service status ── */}
      <div className={s.sectionTitle}>{t('home.statusTitle')}</div>
      <div className={s.statusGrid}>
        {healthLoading ? (
          allServiceKeys.map((key) => (
            <div key={key} className={s.statusCard}>
              <div className={`${s.statusIcon} ${s.checking}`}>…</div>
              <div className={s.statusInfo}>
                <div className={s.statusName}>{SERVICE_LABELS[key] || key}</div>
                <div className={s.statusMsg}>{t('health.checking')}</div>
              </div>
            </div>
          ))
        ) : health?.status === 'down' && health.errorCode === 'PYTHON_SERVICE_DOWN' ? (
          allServiceKeys.map((key) => (
            <div key={key} className={s.statusCard}>
              <div className={`${s.statusIcon} ${s.error}`}><X size={18} /></div>
              <div className={s.statusInfo}>
                <div className={s.statusName}>{SERVICE_LABELS[key] || key}</div>
                <div className={`${s.statusMsg} ${s.errorMsg}`}>{t('health.pythonDown')}</div>
              </div>
            </div>
          ))
        ) : (
          allServiceKeys.map((key) => {
            const entry = services.find(([k]) => k === key)
            const svc = entry?.[1]
            const isOk = svc?.status === 'ok'
            return (
              <div
                key={key}
                className={`${s.statusCard} ${!isOk ? s.clickable : ''}`}
                onClick={() => !isOk && handleStatusClick(key)}
              >
                <div className={`${s.statusIcon} ${isOk ? s.ok : s.error}`}>
                  {isOk ? <Check size={18} /> : <X size={18} />}
                </div>
                <div className={s.statusInfo}>
                  <div className={s.statusName}>{SERVICE_LABELS[key] || key}</div>
                  <div className={`${s.statusMsg} ${!isOk ? s.errorMsg : ''}`}>
                    {svc?.message || (isOk ? t('health.ok') : t('health.error'))}
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* ── Middle: Ongoing activities (hidden entirely when idle, 0.4 §5.4) ── */}
      {ongoingItems.length > 0 && (
        <>
          <div className={s.sectionTitle}>{t('home.ongoingTitle')}</div>
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
        </>
      )}

      {/* ── Today todo (3.0 §7.1;空隐藏) ── */}
      <TodayTodo tasks={todoTasks} onOpenProject={handleOpenProjectTab} />

      {/* ── Upcoming calendar (§6.1.8;空隐藏) ── */}
      <UpcomingCalendar tasks={todoTasks} onOpenProject={handleOpenProjectTab} />

      {/* ── Project card wall + activity feed ── */}
      <div className={s.sectionTitle}>{t('home.recentTitle')}</div>
      <div className={s.recentGrid}>
        {/* Card wall (3.0 §7.1,替代最近项目列表) */}
        <div className={s.recentSection}>
          <div className={s.sectionTitle} style={{ fontSize: 12, marginBottom: 8 }}>
            {t('homeTodo.cardWallTitle')}
          </div>
          <ProjectCardWall
            projects={recentProjects}
            healthData={healthByProject}
            nextDueByProject={nextDueByProject}
            onSelect={(id) => {
              onSelectProject(id)
              window.dispatchEvent(new CustomEvent('app:navigate-project-tab', { detail: { projectId: id, container: 'project' } }))
            }}
          />
        </div>

        {/* Recent activity feed (Activity Hub, 0.4 §5.4) */}
        <div className={s.recentSection}>
          <div className={s.sectionTitle} style={{ fontSize: 12, marginBottom: 8 }}>
            {t('home.recentFeedTitle')}
          </div>
          <div className={s.recentList}>
            {feedItems.length === 0 ? (
              <div className={s.recentEmpty}>{t('home.noFeed')}</div>
            ) : (
              feedItems.map((n) => (
                <div
                  key={n.id}
                  className={`${s.feedItem} ${!n.read_at ? s.feedUnread : ''}`}
                  onClick={() => {
                    void markRead([n.id])
                    navigateToNotification(n)
                  }}
                >
                  <span className={`${s.feedDot} ${n.kind === 'completed' ? s.feedDotOk : n.kind === 'failed' ? s.feedDotErr : s.feedDotWarn}`} />
                  <span className={s.feedText}>{t(`activity.${n.entity_type}.${n.kind}`, { title: n.entity_title || '' })}</span>
                  <span className={s.feedTime}>{relativeTime(n.created_at)}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* ── Quick start(移至最后,§6.1.8)── */}
      <div className={s.sectionTitle}>{t('home.quickTitle')}</div>
      <div className={s.quickGrid}>
        <div className={s.quickCard} onClick={() => onNavigate('tasks')}>
          <span className={s.quickIcon}><BookOpen size={24} /></span>
          <div className={s.quickText}>
            <span className={s.quickLabel}>{t('home.quickTasks')}</span>
            <span className={s.quickDesc}>{t('home.quickTasksDesc')}</span>
          </div>
        </div>
        <div className={s.quickCard} onClick={() => onNavigate('projects')}>
          <span className={s.quickIcon}><Folder size={24} /></span>
          <div className={s.quickText}>
            <span className={s.quickLabel}>{t('home.quickProject')}</span>
            <span className={s.quickDesc}>{t('home.quickProjectDesc')}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
