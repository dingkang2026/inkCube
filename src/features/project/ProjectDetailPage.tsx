/** src/features/project/ProjectDetailPage.tsx — 项目详情页：事务置首四 Tab + PulseBar（0.6 §3.4 V2 翻转决议 + 3.0 一期+二期） */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, Library, FileText, CalendarDays, Pencil } from 'lucide-react'
import { KnowledgePage } from '../knowledge/KnowledgePage'
import { useToast } from '../knowledge/Toast'
import { useActivity } from '../activity/ActivityContext'
import { WikiTab } from './tabs/WikiTab'
import type { WikiCompileData } from './tabs/WikiTab'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { OutputTab } from './tabs/OutputTab'
import { AffairsTab } from './affairs/AffairsTab'
import { summarizeDueTasks } from './affairs/types'
import type { PulseDueSummary } from './affairs/types'
import { PulseBar } from './PulseBar'
import { FirstProjectHint } from './FirstProjectHint'
import { ProjectChatPanel } from './ProjectChatPanel'
import { ProjectUsageBadge } from '../usage/ProjectUsageBadge'
import type { Project } from '../knowledge/types'
import type { ProjectType, ProjectStage } from './types'
import { PROJECT_TYPE_CONFIG, STAGE_MAP, getInitialStage } from './types'
import s from './ProjectDetailPage.module.css'

interface ProjectDetailPageProps {
  project: Project
  /** 通知导航信号（App 层 Activity Hub tab 导航）：收到后切换到目标 Tab；
   *  taskId 可选（首页待办行直达）：affairs Tab 下自动打开该任务执行抽屉（§6.1.8）；
   *  wikiFocus 可选（file 完成通知「整理进知识页」直达）：wiki Tab 下触发编译（同脉搏带 chip） */
  tabSignal?: { tab: string; ts: number; taskId?: string; wikiFocus?: 'lint' | 'compile' } | null
  onOpenSettings?: () => void
  onProjectDataChanged?: () => void
}

type TabKey = 'materials' | 'wiki' | 'affairs' | 'output'

// 事务置首（0.6 §3.4 V2 翻转决议，用户认可 2026-10-23：四支柱排序生命周期 > 知识，
// 显式翻转 3.0 一期「知识置首」；E1a plan T11 原定序，实施偏差 2026-10-24 补正）；
// 产出合并 reports/forms（0.6 §3.2 V2-E1a T11）
const TABS: { key: TabKey; labelKey: string; Icon: typeof FolderOpen }[] = [
  { key: 'affairs', labelKey: 'project.tab.affairs', Icon: CalendarDays },
  { key: 'wiki', labelKey: 'project.tab.wiki', Icon: Library },
  { key: 'output', labelKey: 'project.tab.output', Icon: FileText },
  { key: 'materials', labelKey: 'project.tab.materials', Icon: FolderOpen },
]

export function ProjectDetailPage({ project, tabSignal, onOpenSettings, onProjectDataChanged }: ProjectDetailPageProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const { byProject } = useActivity()
  const { items: compileItems } = useActivity('wiki_compile')
  const { items: ledgerParseItems } = useActivity('ledger_parse')
  const [activeTab, setActiveTab] = useState<TabKey>('affairs')
  const [chatFileId, setChatFileId] = useState<string | null>(null)
  const [chatFilename, setChatFilename] = useState<string | null>(null)
  const [wikiExpanded, setWikiExpanded] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [closePreviewSignal, setClosePreviewSignal] = useState(0)
  // 产出 Tab 分区锚点（0.6 §3.2 V2-E1a T11）：旧 tab 值 reports/forms 并入 output
  // 的跨 Tab 直达（HomePage 进行中区/通知等发送方零改动，E3 清理）
  const [outputSectionSignal, setOutputSectionSignal] = useState<{ section: 'reports' | 'forms'; ts: number } | null>(null)

  // Notify App-level to hide sidebar when entering immersive mode
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('app:wiki-immersive', { detail: { expanded: wikiExpanded } }))
    return () => {
      // Cleanup: restore sidebar when component unmounts
      window.dispatchEvent(new CustomEvent('app:wiki-immersive', { detail: { expanded: false } }))
    }
  }, [wikiExpanded])

  // Wiki compile status polling #4（v1.7 迁移至 usePolling，0.4 §8.1；谓词收编全局心跳
  // useActivity('wiki_compile')，顺序 4）：保存完整快照（task + ingest_summaries +
  // is_resumed）下发 WikiTab（编译明细面板）；Tab 角标改从心跳 byProject 派生。
  // 谓词 = 项目存在活跃编译或明细面板展开（用户盯明细，填触发
  // 动作后心跳未发现的前几秒，§6.3 启动间隙）——空闲且面板收起时停轮，消灭旧 30s 空转。
  const [wikiCompileData, setWikiCompileData] = useState<WikiCompileData | null>(null)
  const [compileDetailOpen, setCompileDetailOpen] = useState(false)
  const wikiCompileActive = compileItems.some(a => a.project_id === project.id)

  // ── 事务 Tab 边沿刷新信号（3.0 二期）：文件解析终态（心跳 byProject.files true→false）
  // 时 +1，AffairsTab 变化即重拉，零新增轮询（0.4 §3.1）──
  const filesActive = (byProject.get(project.id)?.files ?? 0) > 0
  const [affairsRefresh, setAffairsRefresh] = useState(0)
  useEdgeRefetch(filesActive, () => { setAffairsRefresh((x) => x + 1) })

  // ── 脉搏带数据（3.0 §2 一期）：挂载 + 编译终态边沿各拉一次，零新增轮询 ──
  // pendingCompile 直读后端 listPages 的 pending_compile_count（P11：单一事实源，不前端复算）
  const [reviewsCounts, setReviewsCounts] = useState({ contradiction: 0, gap: 0 })
  const [pendingCompile, setPendingCompile] = useState(0)
  const [wikiFocus, setWikiFocus] = useState<{ view: 'lint' | 'compile'; ts: number } | null>(null)
  // 首拉/刷新失败标记:失败态下脉搏带句子显示失败文案,不得呈现假「一切正常」(最终 review minor②)
  const [pulseLoadFailed, setPulseLoadFailed] = useState(false)
  // 快切项目防乱序：in-flight 响应晚到不得覆盖新项目计数（与 checkWikiCompileStatus 同款守卫）
  const latestPidRef = useRef(project.id)
  useEffect(() => { latestPidRef.current = project.id }, [project.id])
  // 应办概要(3.0 二期 Task 12):overdue+7 日内,由 affairs:listByProject 派生;
  // 拉取失败置 null(chip 隐藏),不拖垮知识侧失败态(两数据源独立兑底)
  const [dueTasks, setDueTasks] = useState<PulseDueSummary | null>(null)
  // 待裁决建议数(3.0 四期):affairs:listSuggestions 第四路;拉取失败置 0(chip 隐藏)
  const [suggestionCount, setSuggestionCount] = useState(0)
  const loadPulseData = useCallback(async () => {
    const pid = project.id
    // 四路并行独立兑底(allSettled):知识侧失败 → 脉搏带失败态;应办失败 → chip 隐藏;
    // 建议失败 → chip 隐藏,各数据源互不拖垮;仍只挂在载 + 终态边沿各一次,零新增轮询(0.4 §3.1)
    const [rvR, pgR, afR, sgR] = await Promise.allSettled([
      // reviews limit:1：徽章计数改消费服务端 counts 分型桶（分页后数组不再完整），
      // 最小负载拉取；listPages 的 pending_compile_count 为全量 key 集元数据，不受分页影响；
      // affairs limit:0：dueTasks 概要计算需全量任务（非列表渲染，保持原行为）；
      // suggestions limit:1：建议徽章消费服务端 total（分页契约后 length 会封顶单页量，
      // 与 reviews counts 桶同款最小负载拉取）
      window.electronAPI.invoke('wiki:listReviews', { projectId: pid, resolved: false, limit: 1 }),
      window.electronAPI.invoke('wiki:listPages', { projectId: pid }),
      window.electronAPI.invoke('affairs:listByProject', { projectId: pid, limit: 0 }),
      window.electronAPI.invoke('affairs:listSuggestions', { projectId: pid, limit: 1 }),
    ])
    if (latestPidRef.current !== pid) return
    if (rvR.status === 'rejected' || pgR.status === 'rejected') {
      console.error('Failed to load pulse data:',
        rvR.status === 'rejected' ? rvR.reason
          : pgR.status === 'rejected' ? pgR.reason : null)
      setPulseLoadFailed(true)
    } else {
      setPulseLoadFailed(false)
      const rv = rvR.value as any
      const pg = pgR.value as any
      // 分型计数改消费 counts 桶（{type: {pending, resolved, total}}，全表不分页），
      // gap 桶与后端 E4 分型桶/封面信任信号行同口径：data-gap + missing-page 两型
      // （broken-link 不计入——3.0 §2.3 chip 缺口原定义即两型，三处统一，
      // V2 全量 review M-1 2026-10-24 对齐）
      const c = rv?.counts ?? {}
      setReviewsCounts({
        contradiction: c.contradiction?.pending ?? 0,
        gap: (c['data-gap']?.pending ?? 0) + (c['missing-page']?.pending ?? 0),
      })
      setPendingCompile(pg?.pending_compile_count ?? 0)
    }
    if (afR.status === 'fulfilled') {
      const af = afR.value as any
      setDueTasks(summarizeDueTasks(af?.tasks ?? []))
    } else {
      console.error('Failed to load due tasks:', afR.reason)
      setDueTasks(null)
    }
    if (sgR.status === 'fulfilled') {
      setSuggestionCount((sgR.value as any)?.total ?? 0)   // 分页契约后消费 total（非 length）
    } else {
      console.error('Failed to load suggestions:', sgR.reason)
      setSuggestionCount(0)
    }
  }, [project.id])

  // 首页待办直达抽屉（§6.1.8）：tabSignal 携带 taskId 且目标为 affairs Tab 时
  // 下发焦点信号，AffairsTab 在任务列表就绪后打开执行抽屉
  const [affairsFocus, setAffairsFocus] = useState<{ taskId: string; ts: number } | null>(null)

  // Activity Hub 通知导航信号：切到目标 Tab（wiki/affairs/materials/output；
  // 旧 reports/forms 值 → 产出 Tab 分区锚点兼容映射，0.6 §3.2 V2-E1a T11，E3 清理）
  useEffect(() => {
    if (tabSignal?.tab && ['materials', 'wiki', 'affairs', 'output'].includes(tabSignal.tab)) {
      setActiveTab(tabSignal.tab as TabKey)
    }
    if (tabSignal?.tab === 'reports' || tabSignal?.tab === 'forms') {
      setActiveTab('output')
      setOutputSectionSignal({ section: tabSignal.tab, ts: tabSignal.ts })
    }
    if (tabSignal?.taskId && tabSignal.tab === 'affairs') {
      setAffairsFocus({ taskId: tabSignal.taskId, ts: tabSignal.ts })
    }
    // file 完成通知「整理进知识页」直达（无培训整改）：wiki Tab + 触发编译，
    // 与脉搏带待编译 chip 同一 consume-once 链（WikiTab focusSignal）
    if (tabSignal?.tab === 'wiki' && tabSignal.wikiFocus) {
      setWikiFocus({ view: tabSignal.wikiFocus, ts: tabSignal.ts })
    }
  }, [tabSignal])

  const checkWikiCompileStatus = useCallback(async () => {
    const pid = project.id
    // 抛错由 usePolling 计入连续失败并指数退避（0.4 §5.8）
    const result: any = await window.electronAPI.invoke('wiki:compileStatus', { projectId: pid })
    if (latestPidRef.current !== pid) return // 快切项目：旧响应晚到丢弃
    setWikiCompileData({
      task: result?.task ?? null,
      ingest_summaries: result?.ingest_summaries ?? [],
      is_resumed: !!result?.is_resumed,
    })
  }, [project.id])

  usePolling({
    name: 'wiki-compile-status',
    enabled: wikiCompileActive || compileDetailOpen,
    task: checkWikiCompileStatus,
    // Adaptive: detail panel open=2s, compiling=5s；
    // 兕底 5s（enabled 翻真但快照尚陈旧的窄窗口，理论不可达的防御分支）
    interval: () => {
      if (compileDetailOpen) return 2000
      return 5000
    },
  })

  // 终态边沿补拉（0.4 §6.3）：心跳翻假可早于最后一个轮询 tick（活跃心跳与轮询同为 5s
  // 粒度，终态写入后 δ<5s 翻假时快照停在中间态）→ 边沿拉终态快照，驱动 WikiTab
  // 副作用链（终态刷 pages/reviews + compiling 复位）
  // 终态边沿同时补拉脉搏带数据（reviews/pending），与快照同时机，零新增轮询
  useEdgeRefetch(wikiCompileActive, () => {
    void checkWikiCompileStatus()
    void loadPulseData()
  })

  // 文件解析终态边沿（2026-10-22）：新文件解析 done → 后端待编译集合变化 →
  // 脉搏带待编译 chip 即时刷新（与上方编译终态边沿同款零轮询机制；
  // 原先数字只在挂载/编译终态拉取，中途上传文件 chip 滞留旧值）
  useEdgeRefetch(filesActive, () => {
    void loadPulseData()
  })

  // 挂载 / 项目切换时拉一次快照（0.4 §5.6 切回「一次性拉取」：项目切换后旧快照
  // 防陈旧；停轮期间终态已发生后切入本页也能立即拿到终态）
  useEffect(() => {
    void checkWikiCompileStatus()
  }, [checkWikiCompileStatus])
  useEffect(() => { void loadPulseData() }, [loadPulseData])

  // 任务写操作边沿（3.0 三期铺线）：事务 Tab 打勾/编辑后脉搏带应办 chip 即时刷新；
  // 写操作经 AffairsTab.handleChanged / 首页打勾 dispatch 全局事件（事件边沿非轮询，0.4 §3.1）；
  // loadPulseData 内部已有 latestPidRef 乱序守卫
  useEffect(() => {
    const h = () => { void loadPulseData() }
    window.addEventListener('app:tasks-changed', h)
    return () => window.removeEventListener('app:tasks-changed', h)
  }, [loadPulseData])

  // 台账解析终态边沿（3.0 四期）：ledger_parse 活动从有到无 → 通知 App 刷新 projects
  // （新 AI 状态句从 projects 行浮现，3.1 §4.3）；建议/条目随 onProjectDataChanged →
  // loadProjects → project prop 更新自然刷新
  const ledgerParsing = ledgerParseItems.some((a) => a.project_id === project.id)
  useEdgeRefetch(ledgerParsing, () => { onProjectDataChanged?.() })

  // 切到 wiki Tab 时拉一次快照（#4 停轮期间 WikiTab 挂载，防停留旧快照）。
  // 默认 Tab 为事务（0.6 §3.4 翻转决议）：挂载首跑由上方「挂载拉快照」覆盖
  // （无条件快照与 Tab 无关），此处仅响应「切到」动作，否则挂载即双重 IPC（P10 裁决）。
  const prevTabRef = useRef<TabKey>(activeTab)
  useEffect(() => {
    if (prevTabRef.current === activeTab) return
    prevTabRef.current = activeTab
    if (activeTab === 'wiki') void checkWikiCompileStatus()
  }, [activeTab])

  // Tab 角标从心跳 byProject 派生（顺序 4：不再依赖 #4 自建快照，与心跳同源无重复查表）
  const wikiActivity = byProject.get(project.id)
  const wikiCompiling = (wikiActivity?.compiles ?? 0) > 0

  // Read type/stage from project (persisted in backend)
  const [projectType, setProjectType] = useState<ProjectType>(project.type || 'general')
  const [currentStage, setCurrentStage] = useState<ProjectStage>(project.stage || 'none')

  // Re-sync local state when switching to a different project
  useEffect(() => {
    setProjectType(project.type || 'general')
    setCurrentStage(project.stage || 'none')
  }, [project.id, project.type, project.stage])

  // 切项目即关编辑弹层（review 复核发现，与上方 re-sync 同款守卫）：editXxx 基于
  // 打开时的旧 project 回填，而活动 Toast「查看」action / OS 系统通知点击可绕过
  // 全屏遮罩切换项目——弹层开着时保存会把旧项目字段串写到新项目（静默数据污染）
  useEffect(() => { setEditOpen(false) }, [project.id])

  const handleStageClick = useCallback(async (stage: ProjectStage) => {
    const prevStage = currentStage
    setCurrentStage(stage)
    try {
      await window.electronAPI.invoke('knowledge:updateProject', {
        projectId: project.id,
        stage,
      })
      onProjectDataChanged?.()
    } catch (err) {
      console.error('Failed to persist stage change:', err)
      setCurrentStage(prevStage)
      showToast(t('project.stageUpdateFailed'), 'error')
    }
  }, [project.id, currentStage, onProjectDataChanged, showToast, t])

  const handleTypeChange = useCallback(async (newType: ProjectType) => {
    const prevType = projectType
    const prevStage = currentStage
    setProjectType(newType)
    const newStage = getInitialStage(newType)
    setCurrentStage(newStage)
    try {
      await window.electronAPI.invoke('knowledge:updateProject', {
        projectId: project.id,
        type: newType,
        stage: newStage,
      })
      onProjectDataChanged?.()
    } catch (err) {
      console.error('Failed to persist type change:', err)
      setProjectType(prevType)
      setCurrentStage(prevStage)
      showToast(t('project.typeUpdateFailed'), 'error')
    }
  }, [project.id, projectType, currentStage, onProjectDataChanged, showToast, t])

  // ── 头部「编辑」弹层（todo「项目创建后不能修改」，2026-10-23）：名称/描述/
  // 企业字段全量回填、保存即所见即所得（后端 PATCH 语义不传不改，此处恒传
  // 4 字段——清空 = 显式置空；侧栏改名走 knowledge:renameProject 独立通道）。
  // type/stage 仍走头部下拉/脉搏带，不入弹层；切项目即关（上方守卫 effect）──
  const [editOpen, setEditOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [editCompanyName, setEditCompanyName] = useState('')
  const [editCreditCode, setEditCreditCode] = useState('')
  const [editSaving, setEditSaving] = useState(false)

  const openEdit = useCallback(() => {
    setEditName(project.name)
    setEditDesc(project.description ?? '')
    setEditCompanyName(project.company_name ?? '')
    setEditCreditCode(project.credit_code ?? '')
    setEditOpen(true)
  }, [project])

  const handleSaveEdit = useCallback(async () => {
    const name = editName.trim()
    if (!name || editSaving) return
    setEditSaving(true)
    try {
      await window.electronAPI.invoke('knowledge:updateProject', {
        projectId: project.id,
        name,
        description: editDesc,
        company_name: editCompanyName.trim(),
        credit_code: editCreditCode.trim(),
      })
      setEditOpen(false)
      onProjectDataChanged?.()   // App 层 loadProjects → project prop 刷新 → 头部新值
      showToast(t('project.editSaved'), 'success')
    } catch (err) {
      console.error('Failed to update project:', err)
      showToast(t('project.editProjectFailed'), 'error')
    } finally {
      setEditSaving(false)
    }
  }, [project.id, editName, editDesc, editCompanyName, editCreditCode, editSaving, onProjectDataChanged, showToast, t])

  const typeConfig = PROJECT_TYPE_CONFIG[projectType]
  // 阶段显示名（脉搏带状态句前缀）；general 无时期 → 空串，组件自行省略前缀
  const stageLabel = t(STAGE_MAP[projectType].find((x) => x.key === currentStage)?.labelKey ?? '')

  // Mutual exclusion: when preview opens, close chat; when chat opens, close preview
  const handlePreviewOpenChange = useCallback((isPreviewOpen: boolean) => {
    if (isPreviewOpen) {
      setChatOpen(false)
    }
  }, [])

  const handleChatOpen = useCallback(() => {
    setChatOpen(true)
    setClosePreviewSignal(prev => prev + 1) // Signal WikiTab to close preview
  }, [])

  return (
    <div className={`${s.page}${wikiExpanded ? ` ${s.expanded}` : ''}`}>
      {/* Project header */}
      <div className={s.header}>
        <div className={s.headerTop}>
          <div className={s.headerInfo}>
            <div className={s.typeBadge} title={t(typeConfig.labelKey)}>
              <typeConfig.Icon size={18} />
            </div>
            <div className={s.headerText}>
              <h2 className={s.projectName}>
                {project.name}
              </h2>
              {project.description && (
                <p className={s.projectDesc}>{project.description}</p>
              )}
              {(project.company_name || project.credit_code) && (
                <p className={s.companyInfo}>
                  {project.company_name && <span>{project.company_name}</span>}
                  {project.company_name && project.credit_code && <span className={s.companyDot}>·</span>}
                  {project.credit_code && <span className={s.creditCode}>{project.credit_code}</span>}
                </p>
              )}
            </div>
          </div>
          <div className={s.headerActions}>
            {/* 编辑基础信息（名称/描述/企业字段）——type 走右侧下拉，stage 走脉搏带 */}
            <button
              className={s.editBtn}
              title={t('project.editProject')}
              aria-label={t('project.editProject')}
              onClick={openEdit}
            >
              <Pencil size={15} />
            </button>
            {/* 用量徽章（2.10 §6.1/线框 5）：hover 出 by_source 分拆，点击跳统计页筛本项目 */}
            <ProjectUsageBadge projectId={project.id} />
            {/* Type selector */}
            <select
              className={s.typeSelect}
              value={projectType}
              onChange={(e) => handleTypeChange(e.target.value as ProjectType)}
            >
              {(Object.entries(PROJECT_TYPE_CONFIG) as [ProjectType, typeof typeConfig][]).map(([key, cfg]) => (
                <option key={key} value={key}>{t(cfg.labelKey)}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Stage pills — inline, clickable, bidirectional */}
        {STAGE_MAP[projectType].length > 0 && (
          <div className={s.stageRow}>
            {STAGE_MAP[projectType].map((stage, index) => {
              const isActive = stage.key === currentStage
              const isCompleted = index < STAGE_MAP[projectType].findIndex(s => s.key === currentStage)
              return (
                <button
                  key={stage.key}
                  className={`${s.stagePill} ${isActive ? s.stagePillActive : ''} ${isCompleted ? s.stagePillCompleted : ''}`}
                  onClick={() => handleStageClick(stage.key)}
                >
                  <span className={s.stageDot} />
                  <span>{t(stage.labelKey)}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* 首个项目一次性引导提示条(0.5 §4.3 ③):点击动作或关闭后写 localStorage 永不再现 */}
      <FirstProjectHint
        onUploadMaterials={() => setActiveTab('materials')}
        onStartTask={() => window.dispatchEvent(new CustomEvent('app:navigate-tasks'))}
        onAskAssistant={() => handleChatOpen()}
      />

      {/* 脉搏带（3.0 §2）：状态感知层；矛盾/缺口 chip 点击直达知识治理视图 */}
      <PulseBar
        projectId={project.id}
        stageLabel={stageLabel}
        reviews={reviewsCounts}
        pendingCompile={pendingCompile}
        loadFailed={pulseLoadFailed}
        dueTasks={dueTasks}
        aiStatus={project.pulse_ai_status ?? ''}
        suggestionCount={suggestionCount}
        onOpenAffairs={() => setActiveTab('affairs')}
        onOpenWiki={(view) => {
          setActiveTab('wiki')
          if (view) setWikiFocus({ view, ts: Date.now() })
        }}
      />

      {/* Tab bar */}
      <div className={s.tabBar}>
        {TABS.map(({ key, labelKey, Icon }) => (
          <button
            key={key}
            className={`${s.tab} ${activeTab === key ? s.tabActive : ''}`}
            onClick={() => setActiveTab(key)}
          >
            <Icon size={15} />
            <span>{t(labelKey)}</span>
            {key === 'wiki' && wikiCompiling && (
              <span className={s.tabPulse} />
            )}
            {key === 'materials' && (byProject.get(project.id)?.files ?? 0) > 0 && (
              <span
                className={s.tabPulse}
                title={t('activity.tipFiles', { count: byProject.get(project.id)?.files })}
              />
            )}
            {key === 'output' && ((byProject.get(project.id)?.reports ?? 0)
              + (byProject.get(project.id)?.ppts ?? 0) + (byProject.get(project.id)?.forms ?? 0)) > 0 && (() => {
              const act = byProject.get(project.id)
              return (
                <span
                  className={s.tabPulse}
                  title={[
                    (act?.reports ?? 0) > 0 && t('activity.tipReports', { count: act?.reports }),
                    (act?.ppts ?? 0) > 0 && t('activity.tipPpts', { count: act?.ppts }),
                    (act?.forms ?? 0) > 0 && t('activity.tipForms', { count: act?.forms }),
                  ].filter(Boolean).join(' / ')}
                />
              )
            })()}
          </button>
        ))}
      </div>

      {/* Tab content + chat panel */}
      <div className={s.contentArea}>
        <div className={s.tabContent}>
          {activeTab === 'materials' && (
            <KnowledgePage
              selectedProject={project}
              onOpenSettings={onOpenSettings}
              onProjectDataChanged={onProjectDataChanged}
              compact
              onFileSelect={(fileId, filename) => { setChatFileId(fileId); setChatFilename(filename) }}
            />
          )}
          {activeTab === 'wiki' && (
            <WikiTab
              key={projectType}
              projectId={project.id}
              projectType={projectType}
              expanded={wikiExpanded}
              onExpandChange={setWikiExpanded}
              onPreviewOpenChange={handlePreviewOpenChange}
              closePreviewSignal={closePreviewSignal}
              compileData={wikiCompileData}
              onCompileDetailChange={setCompileDetailOpen}
              focusSignal={wikiFocus}
              onFocusHandled={() => setWikiFocus(null)}
              onReviewsChanged={loadPulseData}
            />
          )}
          {activeTab === 'affairs' && (
            <AffairsTab
              key={project.id}
              projectId={project.id}
              projectType={projectType}
              stage={currentStage}
              refreshSignal={affairsRefresh}
              focusTask={affairsFocus}
              onFocusHandled={() => setAffairsFocus(null)}
              onPanelOpenChange={(open) => { if (open) setChatOpen(false) }}
              onOpenReports={() => {
                setActiveTab('output')
                setOutputSectionSignal({ section: 'reports', ts: Date.now() })
              }}
            />
          )}
          {activeTab === 'output' && (
            <OutputTab
              key={project.id}
              projectId={project.id}
              projectType={projectType}
              sectionSignal={outputSectionSignal}
              onSectionConsumed={() => setOutputSectionSignal(null)}
            />
          )}
        </div>

        {/* Chat panel — wrapper stays in flex row (toggle positioning), panel is absolute to .page (full height) */}
        <ProjectChatPanel
          projectId={project.id}
          projectName={project.name}
          selectedFileId={chatFileId || undefined}
          selectedFilename={chatFilename || undefined}
          onClearFileScope={() => { setChatFileId(null); setChatFilename(null) }}
          isOpen={chatOpen}
          onOpen={handleChatOpen}
          onClose={() => setChatOpen(false)}
        />
      </div>

      {/* 编辑项目弹层：4 字段与创建表单同构（企业字段全类型显示，2.1 决策 7）；
        * 蒙板关闭禁用在保存中（防丢确认）——同 ProjectList 表单模态口径 */}
      {editOpen && (
        <div className={s.editOverlay} onClick={() => { if (!editSaving) setEditOpen(false) }}>
          <div className={s.editModal} onClick={(e) => e.stopPropagation()}>
            <h3 className={s.editTitle}>{t('project.editProject')}</h3>
            <input
              type="text"
              className={s.editInput}
              placeholder={t('knowledge.projectName')}
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              autoFocus
            />
            <input
              type="text"
              className={s.editInput}
              placeholder={t('knowledge.projectDescription')}
              value={editDesc}
              onChange={(e) => setEditDesc(e.target.value)}
            />
            <input
              type="text"
              className={s.editInput}
              placeholder={t('knowledge.companyName')}
              value={editCompanyName}
              onChange={(e) => setEditCompanyName(e.target.value)}
            />
            <input
              type="text"
              className={s.editInput}
              placeholder={t('knowledge.creditCode')}
              value={editCreditCode}
              onChange={(e) => setEditCreditCode(e.target.value)}
            />
            <div className={s.editActions}>
              <button className={s.editCancel} onClick={() => setEditOpen(false)} disabled={editSaving}>
                {t('knowledge.cancel')}
              </button>
              <button
                className={s.editSave}
                onClick={handleSaveEdit}
                disabled={!editName.trim() || editSaving}
              >
                {editSaving ? t('project.editSaving') : t('project.save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
