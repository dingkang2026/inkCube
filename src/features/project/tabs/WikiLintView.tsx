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
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Loader2, Shield, AlertTriangle, AlertCircle,
  Info, Lightbulb, Search, RefreshCw, CheckCircle, Filter, Upload,
} from 'lucide-react'
import { useToast } from '../../knowledge/Toast'
import s from './WikiTab.module.css'

interface WikiLintViewProps {
  projectId: string
  onNavigateToPage: (pageKey: string) => void
  /** 处置动作后回调(0.4 §3.1 操作后刷新):父级同步脉搏带/健康卡计数(3.0 一期最终 review) */
  onFindingsChanged?: () => void
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

export function WikiLintView({ projectId, onNavigateToPage, onFindingsChanged }: WikiLintViewProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [findings, setFindings] = useState<LintFinding[]>([])
  const [loading, setLoading] = useState(false)
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

  // 快切项目防乱序:同类型项目切换时本视图实例存活,在途响应晚到会跨项目覆盖
  // findings 且无自愈——与 WikiTab 三个 loader 的 latestPidRef 同款守卫(re-review 建议)
  const latestPidRef = useRef(projectId)
  useEffect(() => { latestPidRef.current = projectId }, [projectId])

  // Load findings (reviews)
  const loadFindings = useCallback(async () => {
    const pid = projectId
    setLoading(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:listReviews', { projectId: pid, resolved: false })
      if (latestPidRef.current !== pid) return
      setFindings(result.reviews || [])
    } catch (err) {
      console.error('Failed to load lint findings:', err)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { loadFindings() }, [loadFindings])

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

  // Run lint scan
  const handleRunLint = async (mode?: LintMode) => {
    const m = mode || lintMode
    setScanning(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:lint', { projectId, mode: m })
      if (result.success) {
        await loadFindings()
        onFindingsChanged?.()
      }
    } catch (err) {
      console.error('Lint scan failed:', err)
      showToast(t('project.wiki.lintFailed', { defaultValue: '检查失败' }), 'error')
      // full 模式语义层失败时结构层发现项可能已落库,失败路径同样刷新列表与计数(re-review 建议)
      await loadFindings()
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
      await loadFindings()
      onFindingsChanged?.()
    } catch (err) {
      console.error('Failed to dismiss finding:', err)
    }
  }

  // 矛盾裁决（§5.5）：以选中侧为准，后端对其余各侧页面做确定性替换+快照+归档
  const handleAdjudicate = async (f: LintFinding, chosen: number) => {
    setFixingId(f.id)
    try {
      const result: any = await window.electronAPI.invoke('wiki:adjudicateReview', {
        projectId, reviewId: f.id, chosen,
      })
      if (result?.success) {
        showToast(t('project.wiki.lintAdjudicateSuccess', { defaultValue: '已按选定说法更新相关页面' }), 'success')
      } else {
        // 部分失配：发现项保留待处理，提示人工（§5.5 宁可留待人工不误归档）
        showToast(t('project.wiki.lintAdjudicatePartial', { defaultValue: '部分页面内容已变化，未能自动替换，请手动处理' }), 'warning')
      }
    } catch (err) {
      console.error('Failed to adjudicate:', err)
      const reason = err instanceof Error ? err.message : String(err)
      showToast(t('project.wiki.lintAdjudicateFailed', { reason, defaultValue: '裁决失败: {{reason}}' }), 'error')
    } finally {
      setFixingId(null)
      await loadFindings()
      onFindingsChanged?.()
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
        showToast(`${file.name}: ${result.message || result.error || 'Upload failed'}`, 'error')
      } else {
        showToast(
          t('project.wiki.lintGapUploaded', {
            defaultValue: '已上传并开始解析；请稍后触发增量编译，若新文件补上该缺口将自动消除',
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
      await loadFindings()
      onFindingsChanged?.()
    } catch (err) {
      console.error('Failed to batch dismiss:', err)
    }
  }

  // 一键修复死链（真实修复）：读源页面 → 替换死链 → 写回 → 标记发现项已修复
  const handleFixBrokenLink = async (f: LintFinding, target: string) => {
    const sourcePage = extractPages(f.description)[0]
    const deadKey = extractDeadLinkKey(f.title)
    if (!sourcePage || !deadKey) {
      showToast(t('project.wiki.lintFixSourceMissing', { defaultValue: '无法定位源页面或死链，请手动处理' }), 'warning')
      return
    }
    setFixingId(f.id)
    try {
      const detail: any = await window.electronAPI.invoke('wiki:getPage', { projectId, pageKey: sourcePage })
      const content: string | null = detail?.page?.content ?? null
      if (!content) {
        showToast(t('project.wiki.lintFixSourceMissing', { defaultValue: '无法定位源页面或死链，请手动处理' }), 'warning')
        return
      }
      // 匹配 [[dead]] 与 [[dead|别名]] 两种形式并保留别名
      const escaped = deadKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const newContent = content.replace(
        new RegExp(`\\[\\[${escaped}(\\|[^\\]]+)?\\]\\]`, 'g'),
        `[[${target}$1]]`,
      )
      if (newContent === content) {
        // 无匹配说明发现项已过时（源页面已被修改），提示后刷新列表
        showToast(t('project.wiki.lintFixNoMatch', { defaultValue: '源页面中未找到该死链，发现项可能已过时' }), 'warning')
        await loadFindings()
        onFindingsChanged?.()
        return
      }
      await window.electronAPI.invoke('wiki:editPage', {
        projectId, pageKey: sourcePage, content: newContent, title: detail.page.title,
      })
      await window.electronAPI.invoke('wiki:resolveReview', { projectId, reviewId: f.id, action: 'fixed' })
      showToast(t('project.wiki.lintFixSuccess', { defaultValue: '死链已修复' }), 'success')
      await loadFindings()
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
          {t('project.wiki.lintView', { defaultValue: 'Lint 检查' })}
        </span>
        <span className={s.lintCount}>({filtered.length})</span>

        {/* Mode selector */}
        <select
          className={s.lintModeSelect}
          value={lintMode}
          onChange={(e) => setLintMode(e.target.value as LintMode)}
        >
          <option value="full">{t('project.wiki.lintFull', { defaultValue: '完整检查' })}</option>
          <option value="structural">{t('project.wiki.lintStructural', { defaultValue: '结构层' })}</option>
          <option value="semantic">{t('project.wiki.lintSemantic', { defaultValue: '语义层' })}</option>
        </select>

        {/* Re-scan */}
        <button
          className={s.lintScanBtn}
          onClick={() => handleRunLint()}
          disabled={scanning}
        >
          {scanning ? <Loader2 size={13} className={s.spin} /> : <RefreshCw size={13} />}
          {t('project.wiki.rescan', { defaultValue: '重新扫描' })}
        </button>
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
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
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

      {/* Findings list */}
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

        {filtered.map((f) => {
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
                          ? t('project.wiki.lintBlSubtypeSchema', { defaultValue: '结构页缺失' })
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
                            disabled={fixingId === f.id}
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

                {/* 存量矛盾（无结构化证据）：降级提示手动核对（§5.5） */}
                {f.type === 'contradiction' && !evidenceSides && (
                  <p className={s.lintHintLine}>
                    {t('project.wiki.lintContradictionLegacy', { defaultValue: '此发现项产生于结构化证据之前，请点开相关页面手动核对' })}
                  </p>
                )}

                {/* 死链分型提示（§5.5） */}
                {blSubtype === 'missing-source' && (
                  <p className={s.lintHintLine}>
                    {t('project.wiki.lintBlSourceHint', { defaultValue: '链接目标是一个来源摘要页（src_），来源文件可能未摄入或已被删除——可触发增量编译或忽略' })}
                  </p>
                )}
                {blSubtype === 'missing-schema' && (
                  <p className={s.lintHintLine}>
                    {t('project.wiki.lintBlSchemaHint', { defaultValue: '链接目标是结构页，尚未编译生成——触发增量编译后会自动创建' })}
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
      </div>
    </div>
  )
}
