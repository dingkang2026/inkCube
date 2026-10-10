/** src/features/project/ProjectDetailPage.tsx — 项目详情页：knowledge-first tabs + 事务 Tab + PulseBar（3.0 一期+二期） */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, Library, FileText, ClipboardList, CalendarDays } from 'lucide-react'
import { KnowledgePage } from '../knowledge/KnowledgePage'
import { useToast } from '../knowledge/Toast'
import { useActivity } from '../activity/ActivityContext'
import { ReportsTab } from './tabs/ReportsTab'
import { WikiTab } from './tabs/WikiTab'
import type { WikiCompileData } from './tabs/WikiTab'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { FormsTab } from './tabs/FormsTab'
import { AffairsTab } from './affairs/AffairsTab'
import { summarizeDueTasks } from './affairs/types'
import type { PulseDueSummary } from './affairs/types'
import { PulseBar } from './PulseBar'
import { ProjectChatPanel } from './ProjectChatPanel'
import type { Project } from '../knowledge/types'
import type { ProjectType, ProjectStage } from './types'
import { PROJECT_TYPE_CONFIG, STAGE_MAP, getInitialStage } from './types'
import s from './ProjectDetailPage.module.css'

interface ProjectDetailPageProps {
  project: Project
  /** 通知导航信号（App 层 Activity Hub tab 导航）：收到后切换到目标 Tab */
  tabSignal?: { tab: string; ts: number } | null
  onOpenSettings?: () => void
  onProjectDataChanged?: () => void
}

type TabKey = 'materials' | 'wiki' | 'affairs' | 'reports' | 'forms'

// 事务第二位(3.0 §2.1:知识置首、事务次之)
const TABS: { key: TabKey; labelKey: string; Icon: typeof FolderOpen }[] = [
  { key: 'wiki', labelKey: 'project.tab.wiki', Icon: Library },
  { key: 'affairs', labelKey: 'project.tab.affairs', Icon: CalendarDays },
  { key: 'materials', labelKey: 'project.tab.materials', Icon: FolderOpen },
  { key: 'reports', labelKey: 'project.tab.reports', Icon: FileText },
  { key: 'forms', labelKey: 'project.tab.forms', Icon: ClipboardList },
]

export function ProjectDetailPage({ project, tabSignal, onOpenSettings, onProjectDataChanged }: ProjectDetailPageProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const { byProject } = useActivity()
  const { items: compileItems } = useActivity('wiki_compile')
  const [activeTab, setActiveTab] = useState<TabKey>('wiki')
  const [chatFileId, setChatFileId] = useState<string | null>(null)
  const [chatFilename, setChatFilename] = useState<string | null>(null)
  const [wikiExpanded, setWikiExpanded] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [closePreviewSignal, setClosePreviewSignal] = useState(0)

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
  const loadPulseData = useCallback(async () => {
    const pid = project.id
    // 三路并行独立兑底(allSettled):知识侧失败 → 脉搏带失败态;应办失败 → chip 隐藏,
    // 两数据源互不拖垮;仍只挂在载 + 终态边沿各一次,零新增轮询(0.4 §3.1)
    const [rvR, pgR, afR] = await Promise.allSettled([
      window.electronAPI.invoke('wiki:listReviews', { projectId: pid, resolved: false }),
      window.electronAPI.invoke('wiki:listPages', { projectId: pid }),
      window.electronAPI.invoke('affairs:listByProject', { projectId: pid }),
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
      const items: Array<{ type: string }> = rv?.reviews ?? rv?.items ?? []
      setReviewsCounts({
        contradiction: items.filter((r) => r.type === 'contradiction').length,
        gap: items.filter((r) => r.type === 'data-gap' || r.type === 'missing-page' || r.type === 'broken-link').length,
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
  }, [project.id])

  // Activity Hub 通知导航信号：切到目标 Tab（wiki/affairs/materials/reports/forms）
  useEffect(() => {
    if (tabSignal?.tab && ['materials', 'wiki', 'affairs', 'reports', 'forms'].includes(tabSignal.tab)) {
      setActiveTab(tabSignal.tab as TabKey)
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

  // 挂载 / 项目切换时拉一次快照（0.4 §5.6 切回「一次性拉取」：项目切换后旧快照
  // 防陈旧；停轮期间终态已发生后切入本页也能立即拿到终态）
  useEffect(() => {
    void checkWikiCompileStatus()
  }, [checkWikiCompileStatus])
  useEffect(() => { void loadPulseData() }, [loadPulseData])

  // 切到 wiki Tab 时拉一次快照（#4 停轮期间 WikiTab 挂载，防停留旧快照）。
  // 默认 Tab 即 wiki（3.0 一期知识置首）：挂载首跑由上方「挂载拉快照」覆盖，
  // 此处仅响应「切到」动作，否则挂载即双重 IPC（P10 裁决）。
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
              <h2 className={s.projectName}>{project.name}</h2>
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

      {/* 脉搏带（3.0 §2）：状态感知层；矛盾/缺口 chip 点击直达知识治理视图 */}
      <PulseBar
        projectId={project.id}
        stageLabel={stageLabel}
        reviews={reviewsCounts}
        pendingCompile={pendingCompile}
        loadFailed={pulseLoadFailed}
        dueTasks={dueTasks}
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
            {key === 'reports' && (byProject.get(project.id)?.reports ?? 0) > 0 && (
              <span
                className={s.tabPulse}
                title={t('activity.tipReports', { count: byProject.get(project.id)?.reports })}
              />
            )}
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
              onPanelOpenChange={(open) => { if (open) setChatOpen(false) }}
              onOpenReports={() => setActiveTab('reports')}
            />
          )}
          {activeTab === 'reports' && (
            <ReportsTab key={projectType} projectId={project.id} projectType={projectType} />
          )}
          {activeTab === 'forms' && (
            <FormsTab key={projectType} projectType={projectType} />
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
    </div>
  )
}
