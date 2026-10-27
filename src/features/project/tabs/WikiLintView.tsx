/** src/features/project/tabs/WikiLintView.tsx — Wiki Lint view (structural + semantic).
 *
 * Per §12.3.5 of 2.6wiki-design.md.
 * Displays lint findings (stored as reviews), supports:
 *   - Structural + semantic lint modes
 *   - Filter by type / severity
 *   - One-click fix for broken-links
 *   - Re-scan trigger
 *   - Click to navigate to affected page
 * 治理工作台（§5.5，E2-2026-10）：分型统计 chips + 矛盾两栏对比裁决卡
 * （adjudicateReview）+ 缺口补料上传卡（knowledge:uploadFile）+ 死链分型
 */
import { useState, useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Loader2, Shield, AlertTriangle, AlertCircle,
  Info, Lightbulb, Search, RefreshCw, CheckCircle, Filter, Upload,
  Copy,
} from 'lucide-react'
import { useToast } from '../../knowledge/Toast'
import { usePagedList } from '../../../hooks/usePagedList'
import { apiErrorText } from '../../../i18n/errorCodes'
import s from './WikiTab.module.css'

interface WikiLintViewProps {
  projectId: string
  onNavigateToPage: (pageKey: string) => void
  /** 处置动作后回调(0.4 §3.1 操作后刷新):父级同步脉搏带计数(3.0 一期最终 review) */
  onFindingsChanged?: () => void
  /** 汇编进行中（父级 compileActive，§5.5 裁决前置禁用——409 拒绝的前置防线） */
  compiling?: boolean
}

interface LintFinding {
  id: string
  type: string
  title: string
  description: string
  resolved: number
  created_at: string
  /** 矛盾证据结构化（§3.5/§5.5）：摄入产出的 contradiction 携带，存量与 Lint 产物为空 */
  evidence?: { page_key: string; quote: string }[] | null
}

/** 裁决逐侧明细（§5.5）：status 由后端 adjudicate 逐侧给出 */
interface AdjudicateDetail {
  page_key: string
  success: boolean
  /** replaced=直接替换 / relocated=LLM 重定位后替换 / vanished=原文已消失 / manual_needed=留人工 */
  status?: string
}

/** 重复分组（§12.2.10）：编译终态自动检测落库 wiki_dedup_groups → wiki:listDedupGroups 行 */
interface DedupGroup {
  id: string
  slugs: string[]
  reason: string
  confidence: string
}

type LintMode = 'structural' | 'semantic' | 'full'
type SeverityFilter = 'all' | 'high' | 'medium' | 'low'

// Map lint types to icons
const TYPE_ICONS: Record<string, typeof AlertCircle> = {
  'orphan': Info,
  'broken-link': AlertTriangle,
  'no-outlinks': Info,
  'empty-page': AlertCircle,
  'contradiction': AlertTriangle,
  'stale': AlertTriangle,
  'missing-page': Search,
  'suggestion': Lightbulb,
  'explore-next': Lightbulb,
}

// Map lint types to severity (structural types have implicit severity;
// 摄入管线产出的类型无 [severity] 前缀，按类型回落默认级别——§5.4，
// 否则整列落到 info 导致级别筛选失效)
const TYPE_SEVERITY: Record<string, string> = {
  'orphan': 'info',
  'broken-link': 'warning',
  'no-outlinks': 'info',
  'empty-page': 'warning',
  'missing-source': 'medium',
  'orphan-review': 'low',
  'contradiction': 'high',
  'stale': 'medium',
  'stale-claim': 'medium',
  'missing-page': 'medium',
  'data-gap': 'medium',
  'suggestion': 'low',
  'explore-next': 'low',
}

// Lint type label i18n keys（经 t() 翻译，缺失时回退原始 type）
const TYPE_LABEL_KEYS: Record<string, string> = {
  'orphan': 'project.wiki.lintTypeOrphan',
  'broken-link': 'project.wiki.lintTypeBrokenLink',
  'no-outlinks': 'project.wiki.lintTypeNoOutlinks',
  'empty-page': 'project.wiki.lintTypeEmptyPage',
  'missing-source': 'project.wiki.lintTypeMissingSource',
  'orphan-review': 'project.wiki.lintTypeOrphanReview',
  'contradiction': 'project.wiki.lintTypeContradiction',
  'stale': 'project.wiki.lintTypeStale',
  'stale-claim': 'project.wiki.lintTypeStaleClaim',
  'missing-page': 'project.wiki.lintTypeMissingPage',
  'data-gap': 'project.wiki.lintTypeDataGap',
  'suggestion': 'project.wiki.lintTypeSuggestion',
  'explore-next': 'project.wiki.lintTypeExploreNext',
}

// Severity label i18n keys（经 t() 翻译，缺失时回退原始 severity）
const SEVERITY_LABEL_KEYS: Record<string, string> = {
  'high': 'project.wiki.lintSeverityHigh',
  'medium': 'project.wiki.lintSeverityMedium',
  'warning': 'project.wiki.lintSeverityWarning',
  'low': 'project.wiki.lintSeverityLow',
  'info': 'project.wiki.lintSeverityInfo',
}

// Extract severity from description (semantic lint prepends [severity])
function extractSeverity(desc: string, type: string): string {
  const m = desc.match(/^\[(high|medium|low|info|warning)\]/i)
  if (m) return m[1].toLowerCase()
  return TYPE_SEVERITY[type] || 'info'
}

// Extract PAGES list from description
function extractPages(desc: string): string[] {
  const m = desc.match(/PAGES:\s*(.+)$/m)
  if (!m) return []
  return m[1].split(',').map((item) => item.trim()).filter(Boolean)
}

// Extract suggestion target from description（检测正则与后端 routers/wiki.py 落库文案契约，label 由 i18n 渲染）
function extractSuggestion(desc: string): { kind: 'link' | 'related'; target: string } | null {
  const m = desc.match(/建议链接到: \[\[(.+?)\]\]/)
  if (m) return { kind: 'link', target: m[1] }
  const m2 = desc.match(/建议关联页面: \[\[(.+?)\]\]/)
  if (m2) return { kind: 'related', target: m2[1] }
  return null
}

// Extract dead-link page key from finding title（lint.py 死链标题形如 "死链: [[key]] (来自 xxx)"）
function extractDeadLinkKey(title: string): string | null {
  const m = title.match(/\[\[(.+?)(?:\|[^\]]*)?\]\]/)
  return m ? m[1] : null
}

// 死链分型（§5.5）：前端实时判定兼容存量——来源页缺失 / 结构页缺失 / 疑似异名
// schema 匹配做宽松 slug 归一（与后端 _normalize_slug 的常用形态对齐：小写+空白转下划线）
type BrokenLinkSubtype = 'missing-source' | 'missing-schema' | 'unknown'

function normalizeSlug(key: string): string {
  return key.trim().toLowerCase().replace(/\s+/g, '_')
}

function classifyBrokenLink(deadKey: string | null, schemaKeys: Set<string>): BrokenLinkSubtype | null {
  if (!deadKey) return null
  if (deadKey.startsWith('src_')) return 'missing-source'
  if (schemaKeys.has(normalizeSlug(deadKey))) return 'missing-schema'
  return 'unknown'
}

// read file as base64 data URL（缺口补料上传，与 KnowledgePage 同款通道格式）
function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

// Strip metadata from description for display
function cleanDescription(desc: string): string {
  return desc
    .replace(/^\[(high|medium|low|info|warning)\]\s*/i, '')
    .replace(/\nPAGES:.*$/m, '')
    .replace(/\n建议链接到:.*$/m, '')
    .replace(/\n建议关联页面:.*$/m, '')
    .trim()
}

export function WikiLintView({ projectId, onNavigateToPage, onFindingsChanged, compiling = false }: WikiLintViewProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [scanning, setScanning] = useState(false)
  const [lintMode, setLintMode] = useState<LintMode>('full')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>('all')
  const [fixingId, setFixingId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [schemaKeys, setSchemaKeys] = useState<Set<string>>(new Set())
  const [uploadingGapId, setUploadingGapId] = useState<string | null>(null)
  const gapFileInputRef = useRef<HTMLInputElement>(null)
  const gapUploadTargetRef = useRef<string | null>(null)
  // 重复检测与合并（§12.2.10）：检测已自动化（编译终态落库），本视图只消费结果
  const [dedupGroups, setDedupGroups] = useState<DedupGroup[] | null>(null)
  const [canonicalChoices, setCanonicalChoices] = useState<Record<string, string>>({})
  const [mergingIds, setMergingIds] = useState<Set<string>>(new Set())
  const [pendingMergeIds, setPendingMergeIds] = useState<Set<string> | null>(null)
  const [dedupSelected, setDedupSelected] = useState<Set<string>>(new Set())
  // 矛盾裁决终态（§5.5）：逐侧明细 + vanished 终态卡（响应驱动不持久化，
  // 刷新/重进后随列表态复位——再裁决会重新走一遍后端链路得出一致结论）
  const [adjudicateOutcome, setAdjudicateOutcome] = useState<{
    findingId: string
    details: AdjudicateDetail[]
  } | null>(null)
  // 合并确认：Esc = 取消（合并请求在途不可关，与点蒙板同语义）
  useEffect(() => {
    if (pendingMergeIds === null) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && mergingIds.size === 0) setPendingMergeIds(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pendingMergeIds, mergingIds])

  // 快切项目防乱序:同类型项目切换时本视图实例存活,在途响应晚到会跨项目覆盖
  // findings 且无自愈——与 WikiTab 三个 loader 的 latestPidRef 同款守卫(re-review 建议)
  const latestPidRef = useRef(projectId)
  useEffect(() => { latestPidRef.current = projectId }, [projectId])

  // 项目切换清空旧项目数据（review M-1）：dedup 分组/选择集/裁决终态。
  // findings 由 usePagedList 按 projectId 自动重拉；旧项目 selectedIds 残留窗口期内
  // 「批量忽略」会把新项目同名 id 误归档（resolveReview 才 403，页面已被改写且静默），必须清掉
  useEffect(() => {
    setDedupGroups(null)
    setCanonicalChoices({})
    setPendingMergeIds(null)
    setMergingIds(new Set())
    setDedupSelected(new Set())
    setAdjudicateOutcome(null)
    setSelectedIds(new Set())
  }, [projectId])

  // 发现项列表分页（0.1dev-guide 统一约定）：50/页「加载更多」；处置/扫描后 reload
  // 按已加载量静默重拉（不置 loading，旧列表保留防闪烁）；筛选 chips 基于已加载数组，
  // 加载更多后趋于全量
  const {
    items: findings,
    hasMore,
    loadingMore,
    loading,
    error: findingsError,
    reload: reloadFindings,
    loadMore: loadMoreFindings,
  } = usePagedList<LintFinding>({
    channel: 'wiki:listReviews', itemsKey: 'reviews',
    params: { projectId, resolved: false },
  })
  // 列表加载失败提示（对齐 FormsTab m-3：静默吞掉会让列表空白且无任何提示）
  useEffect(() => {
    if (findingsError) showToast(`${t('common.listLoadFailed')}: ${findingsError}`, 'error')
  }, [findingsError, showToast, t])

  // 结构页集合（死链分型用，§5.5）：getSchemas 返回项目类型的 schema 定义
  useEffect(() => {
    let cancelled = false
    window.electronAPI.invoke('wiki:getSchemas', { projectId })
      .then((result: any) => {
        if (cancelled) return
        const list: any[] = result?.schemas || []
        setSchemaKeys(new Set(list.map((x) => normalizeSlug(String(x.page_key || '')))))
      })
      .catch((err: unknown) => console.error('Failed to load wiki schemas:', err))
    return () => { cancelled = true }
  }, [projectId])

  // 重复分组挂载加载（§12.2.10）：检测结果由编译终态自动治理链落库，此处只读；
  // 合并/忽略后由对应 handler 本地移除（后端行已置终态，重拉结果一致）
  useEffect(() => {
    let cancelled = false
    window.electronAPI.invoke('wiki:listDedupGroups', { projectId })
      .then((result: any) => {
        if (cancelled || latestPidRef.current !== projectId) return
        const groups: DedupGroup[] = result?.groups || []
        setDedupGroups(groups)
        // 每组默认首个 slug 为 canonical，用户可改选
        setCanonicalChoices(Object.fromEntries(groups.map((g) => [g.id, g.slugs[0]])))
      })
      .catch((err: unknown) => console.error('Failed to load dedup groups:', err))
    return () => { cancelled = true }
  }, [projectId])

  // 治理完成实时刷新（review M-2 2026-10-27）：governance 通知到达即重载
  // 发现项与重复分组——已打开的体检视图无需手动刷新（ActivityMonitor 经
  // app:governance-done 广播；通知点击导航只是兜底路径）
  useEffect(() => {
    const onGovernanceDone = (e: Event) => {
      const detail = (e as CustomEvent).detail as { projectId?: string } | undefined
      if (detail?.projectId && detail.projectId !== projectId) return
      void reloadFindings()
      window.electronAPI.invoke('wiki:listDedupGroups', { projectId })
        .then((result: any) => {
          if (latestPidRef.current !== projectId) return
          const groups: DedupGroup[] = result?.groups || []
          setDedupGroups(groups)
          setCanonicalChoices(Object.fromEntries(groups.map((g) => [g.id, g.slugs[0]])))
        })
        .catch((err: unknown) => console.error('Failed to reload dedup groups:', err))
    }
    window.addEventListener('app:governance-done', onGovernanceDone)
    return () => window.removeEventListener('app:governance-done', onGovernanceDone)
  }, [projectId, reloadFindings])

  // Run lint scan
  const handleRunLint = async (mode?: LintMode) => {
    if (compiling) {
      // review s-1 2026-10-27：重新扫描的纵深拦截（full 模式烧 LLM 且产出中间态）
      showToast(t('project.wiki.lintAdjudicateCompiling', { defaultValue: '汇编进行中，请等完成后处理' }), 'warning')
      return
    }
    const m = mode || lintMode
    setScanning(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:lint', { projectId, mode: m })
      if (result.success) {
        await reloadFindings()
        onFindingsChanged?.()
      }
    } catch (err) {
      console.error('Lint scan failed:', err)
      showToast(t('project.wiki.lintFailed', { defaultValue: '检查失败' }), 'error')
      // full 模式语义层失败时结构层发现项可能已落库,失败路径同样刷新列表与计数(re-review 建议)
      await reloadFindings()
      onFindingsChanged?.()
    } finally {
      setScanning(false)
    }
  }

  // Dismiss a finding (ignore)
  const handleDismiss = async (findingId: string) => {
    try {
      await window.electronAPI.invoke('wiki:resolveReview', { projectId, reviewId: findingId, action: 'dismiss' })
      selectedIds.delete(findingId)
      setSelectedIds(new Set(selectedIds))
      await reloadFindings()
      onFindingsChanged?.()
    } catch (err) {
      console.error('Failed to dismiss finding:', err)
      const reason = err instanceof Error ? err.message : String(err)
      showToast(t('project.wiki.lintDismissFailed', { reason, defaultValue: '忽略失败: {{reason}}' }), 'error')
    }
  }

  // 矛盾裁决（§5.5）：以选中侧为准；编译中前置禁用（409 的前置防线）；
  // 部分失配不再笼统 toast，改渲染逐侧明细 + vanished 终态卡
  const handleAdjudicate = async (f: LintFinding, chosen: number) => {
    if (compiling) {
      showToast(t('project.wiki.lintAdjudicateCompiling', { defaultValue: '汇编进行中，请等完成后处理' }), 'warning')
      return
    }
    setFixingId(f.id)
    setAdjudicateOutcome(null)
    try {
      const result: any = await window.electronAPI.invoke('wiki:adjudicateReview', {
        projectId, reviewId: f.id, chosen,
      })
      if (result?.success) {
        showToast(t('project.wiki.lintAdjudicateSuccess', { defaultValue: '已按选定说法更新相关页面' }), 'success')
      } else {
        // 逐侧明细（replaced/relocated/vanished/manual_needed）在发现卡内呈现；
        // vanished 侧出终态卡（矛盾大概率已随编辑消失，§5.5）
        setAdjudicateOutcome({ findingId: f.id, details: result?.details || [] })
      }
    } catch (err) {
      console.error('Failed to adjudicate:', err)
      const reason = err instanceof Error ? err.message : String(err)
      showToast(t('project.wiki.lintAdjudicateFailed', { reason, defaultValue: '裁决失败: {{reason}}' }), 'error')
    } finally {
      setFixingId(null)
      await reloadFindings()
      onFindingsChanged?.()
    }
  }

  // vanished 终态「标记解决」（§5.5）：用户确认矛盾已随内容消失，按忽略归档
  const handleVanishedResolve = async (f: LintFinding) => {
    try {
      await window.electronAPI.invoke('wiki:resolveReview', { projectId, reviewId: f.id, action: 'dismiss' })
      setAdjudicateOutcome(null)
      await reloadFindings()
      onFindingsChanged?.()
    } catch (err) {
      console.error('Failed to resolve vanished finding:', err)
      const reason = err instanceof Error ? err.message : String(err)
      showToast(t('project.wiki.lintDismissFailed', { reason, defaultValue: '忽略失败: {{reason}}' }), 'error')
    }
  }

  // 缺口补料（§5.5）：内嵌文件选择 → 直传本项目知识库（复用 knowledge:uploadFile）
  const openGapFilePicker = (findingId: string) => {
    gapUploadTargetRef.current = findingId
    if (gapFileInputRef.current) {
      gapFileInputRef.current.value = ''
      gapFileInputRef.current.click()
    }
  }

  const handleGapFileChosen = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const findingId = gapUploadTargetRef.current
    const file = e.target.files?.[0]
    if (!file || !findingId) return
    setUploadingGapId(findingId)
    try {
      const base64 = await readFileAsBase64(file)
      const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
      const result: any = await window.electronAPI.invoke('knowledge:uploadFile', {
        projectId,
        filename: file.name,
        fileType: ext,
        data: base64,
      })
      if (result && !result.success) {
        showToast(`${file.name}: ${apiErrorText(result.code, result.message || result.error || t('knowledge.uploadFailed'), t)}`, 'error')
      } else {
        // 自动汇编默认开（2.6 §4.1 路径 A）：不再让用户「稍后手动触发」，
        // 文案只说结果与后续（免培训 §2.10）
        showToast(
          t('project.wiki.lintGapUploaded', {
            defaultValue: '已上传并开始解析；解析完成后会自动汇编，若新文件补上该缺口，此问题将自动消除',
          }),
          'success',
        )
      }
    } catch (err) {
      console.error('Failed to upload gap material:', err)
      const reason = err instanceof Error ? err.message : String(err)
      showToast(t('project.wiki.lintGapUploadFailed', { reason, defaultValue: '上传失败: {{reason}}' }), 'error')
    } finally {
      setUploadingGapId(null)
      gapUploadTargetRef.current = null
    }
  }

  // 合并重复页（§12.2.10）：所选各组顺序执行 canonical 保留 + 其余删除 + 全库
  // 链接重写；单组失败不中断其余组（失败组保留供重试/忽略），完成刷新发现项；
  // 编译中前置拦截（review s-2：后端 409 的前置防线）
  const handleDedupMerge = async (groupIds: string[]) => {
    if (compiling) {
      showToast(t('project.wiki.lintAdjudicateCompiling', { defaultValue: '汇编进行中，请等完成后处理' }), 'warning')
      setPendingMergeIds(null)
      return
    }
    const groups = (dedupGroups || []).filter((g) => groupIds.includes(g.id))
    const planned = groups
      .map((g) => ({ group: g, canonical: canonicalChoices[g.id] || g.slugs[0] }))
      .filter(({ group, canonical }) => group.slugs.some((k) => k !== canonical))
    if (planned.length === 0) {
      setPendingMergeIds(null)
      return
    }
    setMergingIds(new Set(planned.map((p) => p.group.id)))
    let okGroups = 0
    let rewritten = 0
    let truncatedGroups = 0
    const failedGroupIds: string[] = []
    try {
      for (const { group, canonical } of planned) {
        const duplicates = group.slugs.filter((k) => k !== canonical)
        try {
          const result: any = await window.electronAPI.invoke('wiki:dedupMerge', {
            projectId, canonicalKey: canonical, duplicateKeys: duplicates, groupId: group.id,
          })
          const failed: unknown[] = result?.failed || []
          const truncated: string[] = result?.truncated || []
          rewritten += result?.rewritten_links ?? 0
          // review m-3 2026-10-27：truncated 页合并本身已成功（检测时仅见摘要），
          // 后端组状态已置 merged——不再计入失败组伪装「可重试」，只记提示数
          if (truncated.length > 0) truncatedGroups += 1
          if (failed.length === 0) {
            okGroups += 1
          } else {
            failedGroupIds.push(group.id)
          }
        } catch (err) {
          console.error('Failed to merge dedup group:', err)
          failedGroupIds.push(group.id)
        }
      }
      if (failedGroupIds.length === 0) {
        showToast(t('project.wiki.dedupMergeDone', {
          groups: okGroups, rewritten,
          defaultValue: '已合并 {{groups}} 组重复页面，重写 {{rewritten}} 处链接',
        }), 'success')
        if (truncatedGroups > 0) {
          showToast(t('project.wiki.dedupMergeTruncated', {
            groups: truncatedGroups,
            defaultValue: '其中 {{groups}} 组含内容超长页面，仅部分正文并入，建议打开保留页核对',
          }), 'info')
        }
      } else {
        showToast(t('project.wiki.dedupMergePartial', {
          ok: okGroups, failedGroups: failedGroupIds.length,
          defaultValue: '已合并 {{ok}} 组；{{failedGroups}} 组存在失败页面，已保留可重试或忽略',
        }), 'warning')
      }
      // 成功组从面板移除（后端已置 merged），失败组保留供重试；页面已变更，
      // 刷新发现项（合并产生 resolved 记录）
      const failedSet = new Set(failedGroupIds)
      setDedupGroups((prev) =>
        prev ? prev.filter((g) => !planned.some((p) => p.group.id === g.id) || failedSet.has(g.id)) : null)
      setDedupSelected(new Set())
      await reloadFindings()
      onFindingsChanged?.()
    } finally {
      setMergingIds(new Set())
      setPendingMergeIds(null)
    }
  }

  // 组忽略：该组重复疑点不处理（后端置 dismissed，重新检测不复活）
  const handleDedupDismiss = async (groupId: string) => {
    try {
      await window.electronAPI.invoke('wiki:dismissDedupGroup', { projectId, groupId })
      setDedupGroups((prev) => (prev ? prev.filter((g) => g.id !== groupId) : null))
      setDedupSelected((prev) => {
        const next = new Set(prev)
        next.delete(groupId)
        return next
      })
    } catch (err) {
      console.error('Failed to dismiss dedup group:', err)
      const reason = err instanceof Error ? err.message : String(err)
      showToast(t('project.wiki.dedupDismissFailed', { reason, defaultValue: '忽略失败: {{reason}}' }), 'error')
    }
  }

  const toggleDedupSelect = (groupId: string) => {
    setDedupSelected((prev) => {
      const next = new Set(prev)
      if (next.has(groupId)) next.delete(groupId); else next.add(groupId)
      return next
    })
  }

  // Toggle selection
  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  // Toggle select all (filtered)
  const toggleSelectAll = () => {
    if (selectedIds.size === filtered.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filtered.map(f => f.id)))
    }
  }

  // Batch dismiss
  const handleBatchDismiss = async () => {
    if (selectedIds.size === 0) return
    try {
      await window.electronAPI.invoke('wiki:batchResolveReviews', {
        projectId,
        reviewIds: Array.from(selectedIds),
        action: 'dismiss',
      })
      setSelectedIds(new Set())
      await reloadFindings()
      onFindingsChanged?.()
    } catch (err) {
      console.error('Failed to batch dismiss:', err)
    }
  }

  // 一键修复死链（真实修复）：服务端正则替换（不冻结页面，留快照）→ 标记发现项已修复
  const handleFixBrokenLink = async (f: LintFinding, target: string) => {
    const sourcePage = extractPages(f.description)[0]
    const deadKey = extractDeadLinkKey(f.title)
    if (!sourcePage || !deadKey) {
      showToast(t('project.wiki.lintFixSourceMissing', { defaultValue: '无法定位源页面或死链，请手动处理' }), 'warning')
      return
    }
    setFixingId(f.id)
    try {
      const result: any = await window.electronAPI.invoke('wiki:fixWikilink', {
        projectId, pageKey: sourcePage, deadKey, targetKey: target,
      })
      if (!result?.success) {
        if (result?.reason === 'content_changed') {
          // review M-1 2026-10-27：读-改-写间页面被并发修改（DB 层 CAS 拒绝，
          // 未产生写操作）——与「发现项过时」区分，提示刷新重试
          showToast(t('project.wiki.lintFixContentChanged', { defaultValue: '页面内容刚被其他操作修改，请刷新列表后重试' }), 'warning')
          await reloadFindings()
          onFindingsChanged?.()
          return
        }
        // 无匹配说明发现项已过时（源页面已被修改），提示后刷新列表
        showToast(t('project.wiki.lintFixNoMatch', { defaultValue: '源页面中未找到该死链，发现项可能已过时' }), 'warning')
        await reloadFindings()
        onFindingsChanged?.()
        return
      }
      await window.electronAPI.invoke('wiki:resolveReview', { projectId, reviewId: f.id, action: 'fixed' })
      showToast(t('project.wiki.lintFixSuccess', { defaultValue: '死链已修复' }), 'success')
      await reloadFindings()
      onFindingsChanged?.()
    } catch (err) {
      console.error('Failed to fix broken link:', err)
      const reason = err instanceof Error ? err.message : String(err)
      showToast(t('project.wiki.lintFixFailed', { reason, defaultValue: '修复失败: {{reason}}' }), 'error')
    } finally {
      setFixingId(null)
    }
  }

  // Extract unique types for filter
  const allTypes = [...new Set(findings.map(f => f.type))]

  // 治理面板分型统计（§5.5）：按类型计数，点击 chip 即筛选
  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const f of findings) counts[f.type] = (counts[f.type] || 0) + 1
    return counts
  }, [findings])

  // Filter findings
  const filtered = findings.filter(f => {
    if (typeFilter !== 'all' && f.type !== typeFilter) return false
    if (severityFilter !== 'all') {
      const sev = extractSeverity(f.description, f.type)
      if (sev !== severityFilter) return false
    }
    return true
  })

  // Sort: high > medium > low > info
  const severityOrder: Record<string, number> = { high: 0, medium: 1, warning: 2, low: 3, info: 4 }
  filtered.sort((a, b) => {
    const sa = severityOrder[extractSeverity(a.description, a.type)] ?? 5
    const sb = severityOrder[extractSeverity(b.description, b.type)] ?? 5
    return sa - sb
  })

  const getIcon = (type: string) => {
    const Icon = TYPE_ICONS[type] || AlertCircle
    return <Icon size={14} />
  }

  const getSeverityClass = (severity: string) => {
    switch (severity) {
      case 'high': return s.lintSeverityHigh
      case 'medium': case 'warning': return s.lintSeverityMedium
      case 'low': return s.lintSeverityLow
      default: return s.lintSeverityInfo
    }
  }

  return (
    <div className={s.lintView}>
      {/* 缺口补料隐藏文件选择（§5.5）：复用 knowledge:uploadFile 通道 */}
      <input
        ref={gapFileInputRef}
        type="file"
        style={{ display: 'none' }}
        onChange={(e) => void handleGapFileChosen(e)}
      />

      {/* Header */}
      <div className={s.lintHeader}>
        <Shield size={18} className={s.lintTitleIcon} />
        <span className={s.lintTitle}>
          {t('project.wiki.lintView', { defaultValue: '知识体检' })}
        </span>
        <span className={s.lintCount}>({filtered.length})</span>

        {/* Mode selector（免培训 §2.10）：术语入 title，选项词说人话 */}
        <select
          className={s.lintModeSelect}
          value={lintMode}
          onChange={(e) => setLintMode(e.target.value as LintMode)}
          title={t('project.wiki.lintModeHint', {
            defaultValue: '全部问题 / 只查链接是否失效 / 只查内容矛盾与过时',
          })}
        >
          <option value="full">{t('project.wiki.lintFull', { defaultValue: '全部问题' })}</option>
          <option value="structural">{t('project.wiki.lintStructural', { defaultValue: '只查链接' })}</option>
          <option value="semantic">{t('project.wiki.lintSemantic', { defaultValue: '只查内容' })}</option>
        </select>

        {/* Re-scan（review s-1：编译中禁用——full 模式烧 LLM 且产出中间态假死链） */}
        <button
          className={s.lintScanBtn}
          onClick={() => handleRunLint()}
          disabled={scanning || compiling}
          title={compiling
            ? t('project.wiki.lintAdjudicateCompiling', { defaultValue: '汇编进行中，请等完成后处理' })
            : undefined}
        >
          {scanning ? <Loader2 size={13} className={s.spin} /> : <RefreshCw size={13} />}
          {t('project.wiki.rescan', { defaultValue: '重新扫描' })}
        </button>

        {/* 清扫审查/重复检测按钮已删（E3-2026-10）：两动作改由编译终态自动治理
            链执行（auto_governance），结果经通知 + 时间线 + 下方面板/列表呈现；
            手动扫描入口保留（重新扫描） */}
      </div>

      {/* 治理面板：分型统计 chips（§5.5）——点击筛选，再点取消 */}
      {allTypes.length > 0 && (
        <div className={s.lintStatsBar}>
          {allTypes.map((type) => (
            <button
              key={type}
              className={`${s.lintStatChip} ${typeFilter === type ? s.lintStatChipActive : ''}`}
              onClick={() => setTypeFilter(typeFilter === type ? 'all' : type)}
            >
              {getIcon(type)}
              <span>{TYPE_LABEL_KEYS[type] ? t(TYPE_LABEL_KEYS[type], { defaultValue: type }) : type}</span>
              <span className={s.lintStatChipCount}>{typeCounts[type]}</span>
            </button>
          ))}
        </div>
      )}

      {/* 重复检测面板（§12.2.10）：检测结果由自动治理链落库，此处只消费；
          组卡 = 多选框 + 置信徽章 + 理由 + slug 单选（canonical）+ 合并/忽略；
          批量栏支持多组一次确认合并 */}
      {dedupGroups !== null && dedupGroups.length > 0 && (
        <div className={s.dedupPanel}>
          <div className={s.dedupPanelHeader}>
            <Copy size={12} />
            <span className={s.dedupPanelTitle}>
              {t('project.wiki.dedupPanelTitle', { count: dedupGroups.length, defaultValue: '疑似重复页面（{{count}} 组）' })}
            </span>
            <span className={s.dedupPanelHint}>
              {t('project.wiki.dedupPanelHint', { defaultValue: '为每组选一页保留，其余页会并入后删除' })}
            </span>
            <button
              className={s.lintDismissBtn}
              onClick={() => setDedupGroups(null)}
              title={t('common.close', { defaultValue: '关闭' })}
            >
              ×
            </button>
          </div>
          {dedupGroups.map((group) => {
            const canonical = canonicalChoices[group.id] || group.slugs[0]
            return (
              <div key={group.id} className={s.dedupGroup}>
                <div className={s.dedupGroupHead}>
                  <input
                    type="checkbox"
                    checked={dedupSelected.has(group.id)}
                    onChange={() => toggleDedupSelect(group.id)}
                  />
                  <span className={`${s.dedupConfidence} ${group.confidence === 'high' ? s.dedupConfidenceHigh : group.confidence === 'medium' ? s.dedupConfidenceMedium : ''}`}>
                    {group.confidence === 'high'
                      ? t('project.wiki.dedupConfidenceHigh', { defaultValue: '高' })
                      : group.confidence === 'medium'
                        ? t('project.wiki.dedupConfidenceMedium', { defaultValue: '中' })
                        : t('project.wiki.dedupConfidenceLow', { defaultValue: '低' })}
                  </span>
                  <span className={s.dedupReason}>{group.reason}</span>
                </div>
                <div className={s.dedupSlugList}>
                  {group.slugs.map((slug) => (
                    <label key={slug} className={s.dedupSlug}>
                      <input
                        type="radio"
                        name={`dedup-group-${group.id}`}
                        checked={canonical === slug}
                        onChange={() => setCanonicalChoices((prev) => ({ ...prev, [group.id]: slug }))}
                      />
                      <span className={s.dedupSlugKey}>[[{slug}]]</span>
                      {canonical === slug && (
                        <span className={s.dedupSlugCanonical}>
                          {t('project.wiki.dedupCanonical', { defaultValue: '保留此页，其余并入' })}
                        </span>
                      )}
                    </label>
                  ))}
                </div>
                <div className={s.dedupGroupActions}>
                  <button
                    className={s.lintScanBtn}
                    disabled={mergingIds.size > 0 || compiling}
                    title={compiling
                      ? t('project.wiki.lintAdjudicateCompiling', { defaultValue: '汇编进行中，请等完成后处理' })
                      : undefined}
                    onClick={() => setPendingMergeIds(new Set([group.id]))}
                  >
                    {mergingIds.has(group.id) && <Loader2 size={12} className={s.spin} />}
                    {t('project.wiki.dedupMergeBtn', {
                      count: group.slugs.length,
                      defaultValue: `合并（${group.slugs.length} → 1）`,
                    })}
                  </button>
                  <button
                    className={s.lintDismissBtn}
                    disabled={mergingIds.has(group.id)}
                    onClick={() => void handleDedupDismiss(group.id)}
                    title={t('project.wiki.dedupDismissHint', { defaultValue: '这不是重复内容，不再提示' })}
                  >
                    {t('project.wiki.dedupDismiss', { defaultValue: '忽略' })}
                  </button>
                </div>
              </div>
            )
          })}
          {/* 批量合并栏：多组一次确认（减点击）；单组合并走组卡按钮同确认框 */}
          {dedupSelected.size > 0 && (
            <div className={s.dedupBatchBar}>
              <span>
                {t('project.wiki.dedupSelectedCount', { count: dedupSelected.size, defaultValue: '已选 {{count}} 组' })}
              </span>
              <button
                className={s.lintScanBtn}
                disabled={mergingIds.size > 0 || compiling}
                onClick={() => setPendingMergeIds(new Set(dedupSelected))}
              >
                {t('project.wiki.dedupMergeSelected', {
                  count: dedupSelected.size,
                  defaultValue: `合并所选（${dedupSelected.size} 组）`,
                })}
              </button>
              <button
                className={s.lintDismissBtn}
                onClick={() => setDedupSelected(new Set())}
              >
                {t('common.cancel', { defaultValue: '取消' })}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Filters */}
      <div className={s.lintFilters}>
        <Filter size={12} className={s.lintFilterIcon} />
        <select
          className={s.lintFilterSelect}
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="all">{t('project.wiki.allTypes', { defaultValue: '全部类型' })}</option>
          {allTypes.map(type => (
            <option key={type} value={type}>
              {TYPE_LABEL_KEYS[type]
                ? t(TYPE_LABEL_KEYS[type], { defaultValue: type })
                : type}
            </option>
          ))}
        </select>
        <select
          className={s.lintFilterSelect}
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value as SeverityFilter)}
        >
          <option value="all">{t('project.wiki.allSeverities', { defaultValue: '全部级别' })}</option>
          <option value="high">{t('project.wiki.lintSeverityHigh', { defaultValue: '高' })}</option>
          <option value="medium">{t('project.wiki.lintSeverityMedium', { defaultValue: '中' })}</option>
          <option value="low">{t('project.wiki.lintSeverityLow', { defaultValue: '低' })}</option>
          <option value="info">{t('project.wiki.lintSeverityInfo', { defaultValue: '信息' })}</option>
        </select>
      </div>

      {/* Batch action bar */}
      <div className={s.lintBatchBar}>
        <label className={s.lintSelectAll}>
          <input
            type="checkbox"
            checked={filtered.length > 0 && selectedIds.size === filtered.length}
            onChange={toggleSelectAll}
          />
          <span>{t('project.wiki.selectAll', { defaultValue: '全选' })}</span>
        </label>
        {selectedIds.size > 0 && (
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            {t('project.wiki.lintSelectedCount', { count: selectedIds.size, defaultValue: '已选 {{count}} 项' })}
          </span>
        )}
        <div className={s.lintBatchActions}>
          <button
            className={s.lintBatchBtn}
            disabled={selectedIds.size === 0}
            onClick={handleBatchDismiss}
          >
            {t('project.wiki.batchDismiss', { count: selectedIds.size, defaultValue: '批量忽略 ({{count}})' })}
          </button>
        </div>
      </div>

      {/* Findings list：loading 时只显示 spinner 不渲染旧列表（防窗口期误点，review M-1） */}
      <div className={s.lintResults}>
        {loading && (
          <div className={s.lintEmpty}>
            <Loader2 size={20} className={s.spin} />
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className={s.lintEmpty}>
            <CheckCircle size={24} className={s.lintEmptyIcon} />
            <p>{t('project.wiki.lintClean', { defaultValue: '未发现任何问题' })}</p>
          </div>
        )}

        {!loading && filtered.map((f) => {
          const severity = extractSeverity(f.description, f.type)
          const pages = extractPages(f.description)
          const suggestion = extractSuggestion(f.description)
          const cleanDesc = cleanDescription(f.description)
          // 死链分型（§5.5）与矛盾证据卡（§5.5）
          const deadKey = f.type === 'broken-link' ? extractDeadLinkKey(f.title) : null
          const blSubtype = f.type === 'broken-link' ? classifyBrokenLink(deadKey, schemaKeys) : null
          const evidenceSides = f.type === 'contradiction' && Array.isArray(f.evidence) && f.evidence.length >= 2
            ? f.evidence : null

          return (
            <div key={f.id} className={`${s.lintItem} ${selectedIds.has(f.id) ? s.lintItemSelected : ''}`}>
              <div className={s.lintItemCheck}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(f.id)}
                  onChange={() => toggleSelect(f.id)}
                />
              </div>
              <div className={s.lintItemBody}>
                <div className={s.lintItemHeader}>
                  <span className={s.lintTypeBadge}>
                    {getIcon(f.type)}
                    {TYPE_LABEL_KEYS[f.type]
                      ? t(TYPE_LABEL_KEYS[f.type], { defaultValue: f.type })
                      : f.type}
                  </span>
                  <span className={`${s.lintSeverityBadge} ${getSeverityClass(severity)}`}>
                    {SEVERITY_LABEL_KEYS[severity]
                      ? t(SEVERITY_LABEL_KEYS[severity], { defaultValue: severity })
                      : severity}
                  </span>
                  {/* 死链分型徽章（§5.5） */}
                  {blSubtype && (
                    <span className={s.lintSubtypeBadge}>
                      {blSubtype === 'missing-source'
                        ? t('project.wiki.lintBlSubtypeSource', { defaultValue: '来源页缺失' })
                        : blSubtype === 'missing-schema'
                          ? t('project.wiki.lintBlSubtypeSchema', { defaultValue: '框架页缺失' })
                          : t('project.wiki.lintBlSubtypeUnknown', { defaultValue: '疑似异名' })}
                    </span>
                  )}
                  <button
                    className={s.lintDismissBtn}
                    onClick={() => handleDismiss(f.id)}
                    title={t('project.wiki.dismiss', { defaultValue: '忽略' })}
                  >
                    ×
                  </button>
                </div>
                <div className={s.lintItemTitle}>{f.title}</div>
                <p className={s.lintItemDesc}>{cleanDesc}</p>

                {/* 矛盾两栏对比裁决卡（§5.5）：evidence 两侧并排，点选一侧自动替换被否侧 */}
                {evidenceSides && (
                  <div className={s.lintEvidenceRow}>
                    {evidenceSides.map((ev, i) => (
                      <div key={`${ev.page_key}-${i}`} className={s.lintEvidenceCard}>
                        <button
                          className={s.lintEvidencePage}
                          onClick={() => onNavigateToPage(ev.page_key)}
                          title={ev.page_key}
                        >
                          [[{ev.page_key}]]
                        </button>
                        <blockquote className={s.lintEvidenceQuote}>「{ev.quote}」</blockquote>
                        <div className={s.lintEvidenceActions}>
                          <button
                            className={s.lintFixBtn}
                            disabled={fixingId === f.id || compiling}
                            title={compiling
                              ? t('project.wiki.lintAdjudicateCompiling', { defaultValue: '汇编进行中，请等完成后处理' })
                              : undefined}
                            onClick={() => void handleAdjudicate(f, i)}
                          >
                            {fixingId === f.id && <Loader2 size={12} className={s.spin} />}
                            {t('project.wiki.lintAdjudicate', { defaultValue: '以此为准' })}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* 裁决终态（§5.5 E3-2026-10）：逐侧明细替代笼统 toast；
                    vanished 侧出终态卡——原文已消失 = 矛盾大概率已随编辑消除，
                    给一次点击决策（标记解决/打开核对）而非让用户自己翻页 */}
                {adjudicateOutcome?.findingId === f.id && (() => {
                  const details = adjudicateOutcome.details
                  const vanishedPage = details.find((d) => d.status === 'vanished')?.page_key
                  // review m-4 2026-10-27：仍有 manual_needed 侧时「矛盾已消除」
                  // 不成立——一键标记解决会连人工侧一起归档；改出「未更新」引导，
                  // 按钮指向未更新页而非 vanished 页
                  const manualPage = details.find((d) => d.status === 'manual_needed')?.page_key
                  return (
                    <div>
                      {details.map((d, di) => (
                        <p key={`${d.page_key}-${di}`} className={s.lintHintLine}>
                          {d.status === 'replaced'
                            ? t('project.wiki.lintAdjDetailReplaced', { page: d.page_key, defaultValue: '[[{{page}}]] 已更新为选定说法' })
                            : d.status === 'relocated'
                              ? t('project.wiki.lintAdjDetailRelocated', { page: d.page_key, defaultValue: '[[{{page}}]] 原文已改写，已在新位置更新为选定说法' })
                              : d.status === 'vanished'
                                ? t('project.wiki.lintAdjDetailVanished', { page: d.page_key, defaultValue: '[[{{page}}]] 中已找不到原来的说法' })
                                : t('project.wiki.lintAdjDetailManual', { page: d.page_key, defaultValue: '[[{{page}}]] 未能自动更新，请打开页面手动处理' })}
                        </p>
                      ))}
                      {vanishedPage !== undefined && !manualPage && (
                        <div className={s.lintGapActions}>
                          <span className={s.lintHintLine}>
                            {t('project.wiki.lintAdjudicateVanished', { defaultValue: '部分原说法已消失，矛盾很可能已随编辑消除' })}
                          </span>
                          <button
                            className={s.lintFixBtn}
                            onClick={() => void handleVanishedResolve(f)}
                          >
                            {t('project.wiki.lintVanishedResolve', { defaultValue: '矛盾已消除，标记解决' })}
                          </button>
                          <button
                            className={s.lintFixBtn}
                            onClick={() => onNavigateToPage(vanishedPage)}
                          >
                            {t('project.wiki.lintVanishedCheck', { defaultValue: '打开页面核对' })}
                          </button>
                        </div>
                      )}
                      {manualPage !== undefined && (
                        <div className={s.lintGapActions}>
                          <span className={s.lintHintLine}>
                            {t('project.wiki.lintAdjudicateVanishedManual', { defaultValue: '部分原说法已消失，但仍有页面未能自动更新——请打开处理，完成后可忽略本条' })}
                          </span>
                          <button
                            className={s.lintFixBtn}
                            onClick={() => onNavigateToPage(manualPage)}
                          >
                            {t('project.wiki.lintAdjManualCheck', { defaultValue: '打开未更新页面' })}
                          </button>
                        </div>
                      )}
                    </div>
                  )
                })()}

                {/* 存量矛盾（无结构化证据）：降级提示手动核对（§5.5） */}
                {f.type === 'contradiction' && !evidenceSides && (
                  <p className={s.lintHintLine}>
                    {t('project.wiki.lintContradictionLegacy', { defaultValue: '此发现项产生于结构化证据之前，请点开相关页面手动核对' })}
                  </p>
                )}

                {/* 死链分型提示（§5.5） */}
                {blSubtype === 'missing-source' && (
                  <p className={s.lintHintLine}>
                    {t('project.wiki.lintBlSourceHint', { defaultValue: '链接目标是一个来源摘要页（src_），来源文件可能未摄入或已被删除——可触发增量汇编或忽略' })}
                  </p>
                )}
                {blSubtype === 'missing-schema' && (
                  <p className={s.lintHintLine}>
                    {t('project.wiki.lintBlSchemaHint', { defaultValue: '链接目标是框架页，尚未汇编生成——触发增量汇编后会自动创建' })}
                  </p>
                )}
                {blSubtype === 'unknown' && !suggestion && (
                  <p className={s.lintHintLine}>
                    {t('project.wiki.lintBlUnknownHint', { defaultValue: '疑似异名或无效链接——无可靠建议目标，请点开源页面手动处理或忽略' })}
                  </p>
                )}

                {/* 缺口补料卡（§5.5）：直传本项目知识库，下轮增量编译 RESOLVES 自动核销 */}
                {(f.type === 'data-gap' || f.type === 'missing-page') && (
                  <div className={s.lintGapActions}>
                    <button
                      className={s.lintGapUploadBtn}
                      disabled={uploadingGapId === f.id}
                      onClick={() => openGapFilePicker(f.id)}
                    >
                      {uploadingGapId === f.id ? <Loader2 size={12} className={s.spin} /> : <Upload size={12} />}
                      {t('project.wiki.lintGapUpload', { defaultValue: '上传补充材料' })}
                    </button>
                  </div>
                )}

                {/* Affected pages */}
                {pages.length > 0 && (
                  <div className={s.lintItemPages}>
                    {pages.map(pk => (
                      <button
                        key={pk}
                        className={s.lintPageLink}
                        onClick={() => onNavigateToPage(pk)}
                      >
                        {pk}
                      </button>
                    ))}
                  </div>
                )}

                {/* Suggestion with one-click fix */}
                {suggestion && (
                  <div className={s.lintSuggestion}>
                    <span className={s.lintSuggestionLabel}>
                      {suggestion.kind === 'related'
                        ? t('project.wiki.lintSuggestRelated', { defaultValue: '关联' })
                        : t('project.wiki.lintSuggestLinkTo', { defaultValue: '链接到' })}:
                    </span>
                    <button
                      className={s.lintPageLink}
                      onClick={() => onNavigateToPage(suggestion.target)}
                    >
                      {suggestion.target}
                    </button>
                    {f.type === 'broken-link' && (
                      <button
                        className={s.lintFixBtn}
                        disabled={fixingId === f.id}
                        onClick={() => void handleFixBrokenLink(f, suggestion.target)}
                      >
                        {fixingId === f.id && <Loader2 size={12} className={s.spin} />}
                        {t('project.wiki.oneClickFix', { defaultValue: '一键修复' })}
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {hasMore && (
          <button
            className={s.loadMoreBtn}
            onClick={() => { void loadMoreFindings() }}
            disabled={loadingMore}
          >
            {loadingMore ? t('common.loading') : t('common.loadMore')}
          </button>
        )}
      </div>

      {/* 合并确认（§12.2.10：dup 页删除 + 全库链接重写不可逆，recompile 确认框同款；
          单组/多组共用一个确认框，body 按组数出文案） */}
      {pendingMergeIds !== null && (() => {
        const mergeGroups = (dedupGroups || []).filter((g) => pendingMergeIds.has(g.id))
        if (mergeGroups.length === 0) return null
        const dupCount = mergeGroups.reduce((n, g) => {
          const canonical = canonicalChoices[g.id] || g.slugs[0]
          return n + g.slugs.filter((k) => k !== canonical).length
        }, 0)
        const merging = mergingIds.size > 0
        return (
          <div className={s.confirmOverlay} onClick={() => !merging && setPendingMergeIds(null)}>
            <div className={s.confirmDialog} onClick={(e) => e.stopPropagation()}>
              <div className={s.confirmHeader}>
                <AlertTriangle size={16} />
                <span>{t('project.wiki.dedupMergeConfirmTitle', { defaultValue: '合并重复页面' })}</span>
              </div>
              <div className={s.confirmBody}>
                {mergeGroups.length === 1
                  ? t('project.wiki.dedupMergeConfirmBody', {
                      count: dupCount,
                      canonical: canonicalChoices[mergeGroups[0].id] || mergeGroups[0].slugs[0],
                      defaultValue: `将把其余 ${dupCount} 个重复页面合并进「${canonicalChoices[mergeGroups[0].id] || mergeGroups[0].slugs[0]}」后删除，全库正文中的链接将自动重写。此操作不可撤销，确定合并？`,
                    })
                  : t('project.wiki.dedupMergeConfirmBodyMulti', {
                      groups: mergeGroups.length, count: dupCount,
                      defaultValue: `将把所选 ${mergeGroups.length} 组重复页面各合并为 1 页（共移除 ${dupCount} 个重复页），全库正文中的链接将自动重写。此操作不可撤销，确定合并？`,
                    })}
              </div>
              <div className={s.confirmActions}>
                <button className={s.confirmCancelBtn} onClick={() => setPendingMergeIds(null)} disabled={merging}>
                  {t('common.cancel', { defaultValue: '取消' })}
                </button>
                <button
                  className={s.confirmDangerBtn}
                  disabled={merging || compiling}
                  onClick={() => void handleDedupMerge(Array.from(pendingMergeIds))}
                >
                  {merging && <Loader2 size={13} className={s.spin} />}
                  {t('project.wiki.dedupMergeConfirmBtn', { count: dupCount, defaultValue: '合并' })}
                </button>
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
