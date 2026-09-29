/** src/features/project/tabs/WikiLintView.tsx — Wiki Lint view (structural + semantic).
 *
 * Per §12.3.5 of 2.6wiki-design.md.
 * Displays lint findings (stored as reviews), supports:
 *   - Structural + semantic lint modes
 *   - Filter by type / severity
 *   - One-click fix for broken-links
 *   - Re-scan trigger
 *   - Click to navigate to affected page
 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Loader2, Shield, AlertTriangle, AlertCircle,
  Info, Lightbulb, Search, RefreshCw, CheckCircle, Filter,
} from 'lucide-react'
import s from './WikiTab.module.css'

interface WikiLintViewProps {
  projectId: string
  onNavigateToPage: (pageKey: string) => void
}

interface LintFinding {
  id: string
  type: string
  title: string
  description: string
  resolved: number
  created_at: string
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

// Map lint types to severity (structural types have implicit severity)
const TYPE_SEVERITY: Record<string, string> = {
  'orphan': 'info',
  'broken-link': 'warning',
  'no-outlinks': 'info',
  'empty-page': 'warning',
}

// Chinese labels for lint types
const TYPE_LABELS: Record<string, string> = {
  'orphan': '孤立页',
  'broken-link': '死链',
  'no-outlinks': '无出链',
  'empty-page': '空页面',
  'contradiction': '矛盾',
  'stale': '过时',
  'missing-page': '缺页面',
  'suggestion': '建议',
  'explore-next': '探索建议',
}

// Chinese labels for severity
const SEVERITY_LABELS: Record<string, string> = {
  'high': '高',
  'medium': '中',
  'warning': '警告',
  'low': '低',
  'info': '信息',
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

// Extract suggestion target from description
function extractSuggestion(desc: string): { label: string; target: string } | null {
  const m = desc.match(/建议链接到: \[\[(.+?)\]\]/)
  if (m) return { label: '链接到', target: m[1] }
  const m2 = desc.match(/建议关联页面: \[\[(.+?)\]\]/)
  if (m2) return { label: '关联', target: m2[1] }
  return null
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

export function WikiLintView({ projectId, onNavigateToPage }: WikiLintViewProps) {
  const { t } = useTranslation()
  const [findings, setFindings] = useState<LintFinding[]>([])
  const [loading, setLoading] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [lintMode, setLintMode] = useState<LintMode>('full')
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>('all')
  const [fixingId, setFixingId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  // Load findings (reviews)
  const loadFindings = useCallback(async () => {
    setLoading(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:listReviews', { projectId, resolved: false })
      setFindings(result.reviews || [])
    } catch (err) {
      console.error('Failed to load lint findings:', err)
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => { loadFindings() }, [loadFindings])

  // Run lint scan
  const handleRunLint = async (mode?: LintMode) => {
    const m = mode || lintMode
    setScanning(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:lint', { projectId, mode: m })
      if (result.success) {
        await loadFindings()
      }
    } catch (err) {
      console.error('Lint scan failed:', err)
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
    } catch (err) {
      console.error('Failed to dismiss finding:', err)
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
    } catch (err) {
      console.error('Failed to batch dismiss:', err)
    }
  }

  // Extract unique types for filter
  const allTypes = [...new Set(findings.map(f => f.type))]

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
            <option key={type} value={type}>{TYPE_LABELS[type] || type}</option>
          ))}
        </select>
        <select
          className={s.lintFilterSelect}
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value as SeverityFilter)}
        >
          <option value="all">{t('project.wiki.allSeverities', { defaultValue: '全部级别' })}</option>
          <option value="high">高</option>
          <option value="medium">中</option>
          <option value="low">低</option>
          <option value="info">信息</option>
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
          <span>全选</span>
        </label>
        {selectedIds.size > 0 && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>已选 {selectedIds.size} 项</span>
        )}
        <div className={s.lintBatchActions}>
          <button
            className={s.lintBatchBtn}
            disabled={selectedIds.size === 0}
            onClick={handleBatchDismiss}
          >
            忽略所选
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
                    {TYPE_LABELS[f.type] || f.type}
                  </span>
                  <span className={`${s.lintSeverityBadge} ${getSeverityClass(severity)}`}>
                    {SEVERITY_LABELS[severity] || severity}
                  </span>
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
                    <span className={s.lintSuggestionLabel}>{suggestion.label}:</span>
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
                        onClick={async () => {
                          setFixingId(f.id)
                          const targetPage = pages[0]
                          if (targetPage) {
                            onNavigateToPage(targetPage)
                          }
                          setFixingId(null)
                        }}
                      >
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
