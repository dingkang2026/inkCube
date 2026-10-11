/** src/features/project/affairs/AffairsTab.tsx — 事务 Tab 容器(3.0 §6.1.3-6.1.5)
 *  三视图切换:时期看板(默认)/ 周期节奏 / 日历;数据 affairs:listByProject 全量拉取,
 *  刷新时机 = 挂载 + refreshSignal 边沿 + 操作后回调(0.4 §3.1 零新增轮询);
 *  快切项目守卫 latestPidRef(同 WikiTab 模式,3.0 一期 T3+4 裁决复用)。
 *  focusTask(首页待办直达,§6.1.8):任务列表就绪后自动打开执行抽屉;
 *  找不到(已删)或拉取失败则放弃并回报 onFocusHandled,信号不悬挂。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CalendarDays, LayoutGrid, Repeat } from 'lucide-react'
import type { ProjectStage, ProjectType } from '../types'
import type { AffairsView, PhaseTask } from './types'
import { PhaseBoard } from './PhaseBoard'
import { RhythmCards } from './RhythmCards'
import { CalendarView } from './CalendarView'
import { SuggestionCard } from './SuggestionCard'
import { TaskDrawer } from './TaskDrawer'
import { TaskFormModal } from './TaskFormModal'
import s from './AffairsTab.module.css'

export interface AffairsTabProps {
  projectId: string
  projectType: ProjectType
  stage: ProjectStage                 // 当前时期(阶段 pill 切换后父级下发)
  /** 父级边沿信号(解析/编译终态 useEdgeRefetch 时 +1):变化即重拉,零新增轮询 */
  refreshSignal?: number
  /** 首页待办直达(§6.1.8):任务列表就绪后自动打开该任务执行抽屉 */
  focusTask?: { taskId: string; ts: number } | null
  /** 焦点已消费(命中开抽屉/未命中放弃),父级清除信号 */
  onFocusHandled?: () => void
  /** 执行抽屉打开 → 父级关 Chat(互斥,§6.4-7) */
  onPanelOpenChange?: (open: boolean) => void
  /** 抽屉「发起报告」→ 父级切 reports Tab(裁决 7) */
  onOpenReports?: () => void
}

export function AffairsTab({ projectId, projectType, stage, refreshSignal, focusTask, onFocusHandled, onPanelOpenChange, onOpenReports }: AffairsTabProps) {
  const { t } = useTranslation()
  const [tasks, setTasks] = useState<PhaseTask[]>([])
  // 首拉/刷新失败标记:失败态下显示失败文案,不得呈现假「暂无事务」空态(对齐一期 PulseBar pulseLoadFailed 原则)
  const [loadFailed, setLoadFailed] = useState(false)
  const [view, setView] = useState<AffairsView>('board')
  const [drawerTask, setDrawerTask] = useState<PhaseTask | null>(null)
  const [formTask, setFormTask] = useState<PhaseTask | null | undefined>(undefined)
  // undefined=关闭;null=新建;PhaseTask=编辑预填
  const [formPrefill, setFormPrefill] = useState<string | null>(null)  // 日历点格预填截止日(Task 9 → 表单)
  const latestPidRef = useRef(projectId)
  useEffect(() => { latestPidRef.current = projectId }, [projectId])
  // 抽屉数据同步用:load 回调内读当前抽屉任务(避免 load 依赖 drawerTask)
  const drawerTaskRef = useRef<PhaseTask | null>(null)
  useEffect(() => { drawerTaskRef.current = drawerTask }, [drawerTask])

  const load = useCallback(async () => {
    const pid = projectId
    try {
      const r: any = await window.electronAPI.invoke('affairs:listByProject', { projectId: pid })
      if (latestPidRef.current !== pid) return
      const list: PhaseTask[] = r?.tasks ?? []
      setTasks(list)
      setLoadFailed(false)
      // 抽屉数据同步:按 id 替换为最新对象(完成后状态徽章/按钮组不谎报);
      // 任务已不在列表(已删)则自动关抽屉并复位 Chat 面板
      const cur = drawerTaskRef.current
      if (cur) {
        const next = list.find((x) => x.id === cur.id) ?? null
        setDrawerTask(next)
        if (!next) onPanelOpenChange?.(false)
      }
    } catch (err) {
      console.error('Failed to load affairs:', err)
      if (latestPidRef.current === pid) setLoadFailed(true)
    }
  }, [projectId])

  // 快切项目即清残留:失败/慢响应下不得显示上一项目的任务(Task 6 评审 minor①)
  useEffect(() => { setTasks([]); setLoadFailed(false) }, [projectId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { if (refreshSignal !== undefined && refreshSignal > 0) void load() }, [refreshSignal, load])

  const handleChanged = useCallback(() => {
    window.dispatchEvent(new CustomEvent('app:tasks-changed'))  // 3.0 三期：跨页边沿（侧栏色点/首页待办同步）
    void load()
  }, [load])
  const openDrawer = useCallback((task: PhaseTask) => {
    setDrawerTask(task)
    onPanelOpenChange?.(true)
  }, [onPanelOpenChange])
  const closeDrawer = useCallback(() => {
    setDrawerTask(null)
    onPanelOpenChange?.(false)
  }, [onPanelOpenChange])

  // 首页待办直达抽屉(§6.1.8):等首拉完成再匹配;命中→开抽屉,未命中(已删)或
  // 拉取失败→放弃;无论命中与否都回报父级清除,ts 去重防重复消费
  // (置于 openDrawer 声明之后,避免渲染期 TDZ)
  const handledFocusTsRef = useRef<number | null>(null)
  useEffect(() => {
    if (!focusTask || handledFocusTsRef.current === focusTask.ts) return
    if (tasks.length === 0 && !loadFailed) return
    const target = tasks.find((x) => x.id === focusTask.taskId)
    if (target) openDrawer(target)
    handledFocusTsRef.current = focusTask.ts
    onFocusHandled?.()
  }, [focusTask, tasks, loadFailed, openDrawer, onFocusHandled])

  const VIEWS: { key: AffairsView; labelKey: string; Icon: typeof CalendarDays }[] = [
    { key: 'board', labelKey: 'affairs.viewBoard', Icon: LayoutGrid },
    { key: 'rhythm', labelKey: 'affairs.viewRhythm', Icon: Repeat },
    { key: 'calendar', labelKey: 'affairs.viewCalendar', Icon: CalendarDays },
  ]

  return (
    <div className={s.container}>
      <div className={s.viewSwitch}>
        {VIEWS.map(({ key, labelKey, Icon }) => (
          <button key={key}
            className={`${s.viewBtn} ${view === key ? s.viewBtnActive : ''}`}
            onClick={() => setView(key)}>
            <Icon size={14} /><span>{t(labelKey)}</span>
          </button>
        ))}
      </div>
      {loadFailed && <div className={s.loadFailed}>{t('affairs.loadFailed')}</div>}
      {/* AI 建议卡(3.0 四期 T8):三视图共享顶部,自包含数据域(refreshSignal 边沿重拉,零新增轮询);
          onAccepted → 重拉看板(整体 review W1:采纳后本组件 tasks state 不在事件刷新链内) */}
      <SuggestionCard projectId={projectId} refreshSignal={refreshSignal}
        onAccepted={() => void load()} />
      {view === 'board' && (
        <PhaseBoard tasks={tasks} stage={stage} projectType={projectType}
          onCreateTask={() => { setFormPrefill(null); setFormTask(null) }}
          onOpenDrawer={openDrawer} />
      )}
      {view === 'rhythm' && (
        <RhythmCards tasks={tasks} onOpenDrawer={openDrawer}
          onEditRhythm={(task) => setFormTask(task)} />
      )}
      {view === 'calendar' && (
        <CalendarView tasks={tasks} onOpenDrawer={openDrawer}
          onCreateTask={(d) => { setFormPrefill(d); setFormTask(null) }} />
      )}
      {drawerTask && (
        <TaskDrawer task={drawerTask} projectId={projectId}
          onClose={closeDrawer} onChanged={handleChanged}
          onEdit={() => setFormTask(drawerTask)}
          onOpenReports={() => { closeDrawer(); onOpenReports?.() }} />
      )}
      {formTask !== undefined && (
        <TaskFormModal projectId={projectId} stage={stage} initial={formTask}
          prefillDueDate={formPrefill}
          onClose={() => { setFormPrefill(null); setFormTask(undefined) }}
          onSaved={handleChanged} />
      )}
    </div>
  )
}
