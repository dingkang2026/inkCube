/** src/features/project/tabs/WikiHomePage.tsx — Wiki 首页（2026-10-26 二次收敛：「页面」视图并入）：
 *  目录态 = 引导卡（首次使用）/ 全局摘要 / 成册进度轻量条 / 目录分组 / 统计（置尾）；
 *  搜索态 = 防抖全文搜索 + 图扩展结果 + 故障错误态。
 *  收敛谱系：目录分组自旧 WikiHomePage「内容索引」→ WikiPagesView → 本组件；
 *  搜索态自 WikiSearchView → WikiPagesView → 本组件——逻辑迁移非重写，不存两份。
 *  「Wiki 目标」区块迁知识框架视图（WikiSchemaManager）——目标属于框架定义层 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  FileText, Edit3, Check, X, Loader2, Search, Link2, Play,
  HelpCircle, ListChecks, ChevronRight,
  BookOpen, Users, Lightbulb, Layers, FolderOpen, Clock,
} from 'lucide-react'
import { Markdown } from '../../../components/Markdown'
import { useToast } from '../../knowledge/Toast'
import s from './WikiTab.module.css'

interface WikiPage {
  page_key: string
  title: string
  content: string | null
  page_type: string
  status: string
}

interface SearchResult {
  id: string
  page_key: string
  title: string
  content: string | null
  page_type: string
  _score: number
  _source: string
  graph_related_to?: string[]
}

interface WikiHomePageProps {
  projectId: string
  overview: string
  /** 来源文件数（isFirstTime 判定 + 统计条） */
  sourcePageCount: number
  /** overview 保存成功后回调父级刷新（避免保存后界面回显旧值） */
  onMetaSaved?: () => void
  /** 已加载页面（目录分组数据源）+ 待汇编计数（空态「开始汇编」入口） */
  pages: WikiPage[]
  pendingCount: number
  /** 结构页定义清单（wiki:getSchemas）+ 缺失清单（listPages.missing_pages，全量口径）：
   *  成册进度轻量条 + 目录台账组置顶判定数据源；空/缺省整条隐藏 */
  schemas?: Array<{ page_key: string; title: string; is_ledger?: boolean }>
  missingPages?: Array<{ page_key: string; title: string }>
  /** 页面总数（wiki:listPages total）：isFirstTime 判定 + 统计条 + 目录计数（全量口径优先于已加载数） */
  pagesTotal?: number | null
  /** 目录分页「加载更多」契约（0.1dev-guide §16） */
  hasMorePages?: boolean
  pagesLoadingMore?: boolean
  onLoadMorePages?: () => void
  onNavigateToPage: (pageKey: string) => void
  /** 空态「开始汇编(N)」入口（经父级 startCompileMaybe，空闲即启动、活跃忽略） */
  onCompile: () => void
  compiling: boolean
  /** 成册进度轻量条点击 → 知识框架视图（框架页定义管理） */
  onOpenSchema?: () => void
}

/** Page type config: icon, label/desc i18n keys, order */
const PAGE_TYPE_CONFIG: Record<string, { Icon: typeof FileText; labelKey: string; descKey: string; order: number }> = {
  topic:     { Icon: Layers,     labelKey: 'project.wiki.homePage.typeTopic', descKey: 'project.wiki.homePage.typeTopicDesc', order: 0 },
  entity:    { Icon: Users,      labelKey: 'project.wiki.homePage.typeEntity', descKey: 'project.wiki.homePage.typeEntityDesc', order: 1 },
  concept:   { Icon: Lightbulb,  labelKey: 'project.wiki.homePage.typeConcept', descKey: 'project.wiki.homePage.typeConceptDesc', order: 2 },
  source:    { Icon: FolderOpen, labelKey: 'project.wiki.homePage.typeSource', descKey: 'project.wiki.homePage.typeSourceDesc', order: 3 },
  structure: { Icon: BookOpen,   labelKey: 'project.wiki.homePage.typeStructure', descKey: 'project.wiki.homePage.typeStructureDesc', order: 4 },
}

/** Extract first meaningful line from content as a one-line summary */
function extractSummary(content: string | null, maxLen = 80): string {
  if (!content) return ''
  const lines = content.replace(/^---[\s\S]*?---\n?/, '').split('\n')
  for (const line of lines) {
    const trimmed = line.replace(/^#+\s*/, '').trim()
    if (trimmed.length > 2) {
      return trimmed.length > maxLen ? trimmed.slice(0, maxLen) + '…' : trimmed
    }
  }
  return ''
}

/** Truncate content for search preview */
function truncateContent(content: string | null, maxLen = 200): string {
  if (!content) return ''
  if (content.length <= maxLen) return content
  return content.slice(0, maxLen) + '…'
}

export function WikiHomePage({
  projectId, overview, sourcePageCount, onMetaSaved,
  pages, pendingCount, schemas, missingPages, pagesTotal,
  hasMorePages, pagesLoadingMore, onLoadMorePages,
  onNavigateToPage, onCompile, compiling, onOpenSchema,
}: WikiHomePageProps) {
  const { t } = useTranslation()
  const showToast = useToast()

  // ── 概况编辑态 ──
  const [editingOverview, setEditingOverview] = useState(false)
  const [draftOverview, setDraftOverview] = useState(overview)
  const [saving, setSaving] = useState(false)
  const [overviewExpanded, setOverviewExpanded] = useState(false)

  useEffect(() => { setDraftOverview(overview) }, [overview])

  const handleSaveOverview = async () => {
    setSaving(true)
    try {
      await window.electronAPI.invoke('wiki:updateOverview', { projectId, overview: draftOverview })
      setEditingOverview(false)
      onMetaSaved?.() // 父级刷新 overview → props 回流，避免保存后仍显示旧值
    } catch (err) {
      console.error('Failed to save overview:', err)
      showToast(t('project.wiki.saveMetaFailed', {
        reason: err instanceof Error ? err.message : String(err),
        defaultValue: `保存失败: ${err instanceof Error ? err.message : String(err)}`,
      }), 'error')
    } finally {
      setSaving(false)
    }
  }

  // ── 搜索态（输入即切换，清空回目录）──
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [searchFailed, setSearchFailed] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([])
      setSearched(false)
      setSearchFailed(false)
      return
    }
    setSearching(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:search', {
        projectId,
        query: q.trim(),
        topK: 20,
      })
      setResults(result.results || [])
      setSearchFailed(false)
      setSearched(true)
    } catch (err) {
      console.error('Wiki search failed:', err)
      setResults([])
      // 故障与「无结果」是两种语义：错误态单独提示（后端 raise_on_error 透出）
      setSearchFailed(true)
      setSearched(true)
    } finally {
      setSearching(false)
    }
  }, [projectId])

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value
    setQuery(val)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => doSearch(val), 300)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      doSearch(query)
    }
  }

  // Cleanup debounce on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  // ── 目录分组 ──
  // V2 E3 目录语义分组：台账页单列置顶组（is_ledger 为唯一判定，0.6 §6.3-5——
  // 分组只改呈现不改 page_type 语义），其余仍按 page_type 分组
  const ledgerKeys = useMemo(
    () => new Set((schemas ?? []).filter((sc) => sc.is_ledger).map((sc) => sc.page_key)),
    [schemas],
  )

  const groupedPages = useMemo(() => {
    const groups: Record<string, WikiPage[]> = {}
    for (const page of pages) {
      if (ledgerKeys.has(page.page_key)) continue
      const type = page.page_type || 'structure'
      if (!groups[type]) groups[type] = []
      groups[type].push(page)
    }
    const sortedEntries = Object.entries(groups)
      .sort(([a], [b]) => {
        const orderA = PAGE_TYPE_CONFIG[a]?.order ?? 99
        const orderB = PAGE_TYPE_CONFIG[b]?.order ?? 99
        return orderA - orderB
      })
    for (const [, pagesInGroup] of sortedEntries) {
      pagesInGroup.sort((a, b) => (a.title || a.page_key).localeCompare(b.title || b.page_key))
    }
    return sortedEntries
  }, [pages, ledgerKeys])

  const ledgerPages = useMemo(
    () => pages.filter((p) => ledgerKeys.has(p.page_key)),
    [pages, ledgerKeys],
  )
  // 未汇编框架页（missing_pages 全量口径）拆分：命中台账键归台账组，其余归知识框架组
  const ledgerMissing = useMemo(
    () => (missingPages ?? []).filter((m) => ledgerKeys.has(m.page_key)),
    [missingPages, ledgerKeys],
  )
  const structureMissing = useMemo(
    () => (missingPages ?? []).filter((m) => !ledgerKeys.has(m.page_key)),
    [missingPages, ledgerKeys],
  )

  // 结构页完成度：消费后端 listPages.missing_pages（基于全量 page_key 集计算，
  // 不受 pages 分页影响——2026-10-22 修复）；total=0 整条隐藏
  const completion = useMemo(() => {
    if (!schemas || schemas.length === 0) return null
    const missingKeys = new Set((missingPages ?? []).map((m) => m.page_key))
    const generated = schemas.filter((sc) => !missingKeys.has(sc.page_key)).length
    return { total: schemas.length, generated, missingKeys }
  }, [schemas, missingPages])

  // 首次使用：无页面且无来源文件（pagesTotal 全量口径替代原已加载数判定）
  const isFirstTime = (pagesTotal ?? 0) === 0 && sourcePageCount === 0

  /** 目录组渲染：已生成页可点 + 缺失页置灰行（「待汇编」小标不可点）；
   *  台账组经 header 参数覆盖组名/图标，其余用 PAGE_TYPE_CONFIG */
  const renderIndexGroup = (
    type: string,
    typePages: WikiPage[],
    missing: Array<{ page_key: string; title: string }>,
    header?: { Icon: typeof FileText; labelKey: string; descKey: string },
  ) => {
    const cfg = header ?? PAGE_TYPE_CONFIG[type] ?? PAGE_TYPE_CONFIG.structure
    const { Icon } = cfg
    return (
      <div key={type} className={s.homeIndexGroup}>
        <div className={s.homeIndexGroupHeader}>
          <Icon size={13} className={s.homeIndexGroupIcon} />
          <span className={s.homeIndexGroupLabel}>{t(cfg.labelKey)}</span>
          <span className={s.homeIndexGroupDesc}>{t(cfg.descKey)}</span>
          <span className={s.homeIndexGroupCount}>{typePages.length}</span>
        </div>
        <div className={s.homeIndexList}>
          {typePages.map((page) => (
            <button
              key={page.page_key}
              className={s.homeIndexItem}
              onClick={() => onNavigateToPage(page.page_key)}
            >
              <span className={s.homeIndexItemTitle}>{page.title || page.page_key}</span>
              <span className={s.homeIndexItemSummary}>
                {extractSummary(page.content)}
              </span>
            </button>
          ))}
          {missing.map((m) => (
            <div key={m.page_key} className={s.homeIndexItemPending}>
              <span className={s.homeIndexItemTitle}>{m.title}</span>
              <span className={s.homeIndexItemPendingTag}>{t('project.wiki.homePage.pendingTag')}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const searching_ = query.trim().length > 0

  return (
    <div className={s.searchView}>
      {/* 搜索框：输入即切换全文搜索态，清空回概况+目录（2026-10-26 二次收敛并入首页） */}
      <div className={s.searchHeader}>
        <div className={s.searchInputWrap}>
          <Search size={16} className={s.searchIcon} />
          <input
            type="text"
            className={s.searchInput}
            placeholder={t('project.wiki.searchPlaceholder', { defaultValue: '搜索 Wiki 页面...' })}
            value={query}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
          />
          {searching && <Loader2 size={14} className={s.spin} />}
        </div>
      </div>

      {searching_ ? (
        /* ── 搜索结果态 ── */
        <div className={s.searchResults}>
          {!searched && !searching && (
            <div className={s.searchHint}>
              <Search size={20} className={s.searchHintIcon} />
              <p>{t('project.wiki.searchHint', { defaultValue: '输入关键词搜索 Wiki 页面' })}</p>
            </div>
          )}

          {searched && results.length === 0 && !searching && (
            <div className={s.searchHint}>
              <p>
                {searchFailed
                  ? t('project.wiki.searchFailed', { defaultValue: '搜索失败，请稍后重试' })
                  : t('project.wiki.noResults', { defaultValue: '未找到相关页面' })}
              </p>
            </div>
          )}

          {results.map((r) => (
            <div
              key={r.page_key}
              className={s.searchResultItem}
              onClick={() => onNavigateToPage(r.page_key)}
            >
              <div className={s.searchResultHeader}>
                <FileText size={14} className={s.searchResultIcon} />
                <span className={s.searchResultTitle}>{r.title || r.page_key}</span>
                <span className={s.searchResultScore}>
                  {(r._score || 0).toFixed(1)}
                </span>
              </div>
              <p className={s.searchResultContent}>
                {truncateContent(r.content)}
              </p>
              <div className={s.searchResultMeta}>
                <span className={s.searchResultKey}>{r.page_key}</span>
                {r._source === 'graph' && r.graph_related_to && r.graph_related_to.length > 0 && (
                  <span className={s.searchResultGraph}>
                    <Link2 size={11} />
                    {t('project.wiki.graphRelated', { defaultValue: '图谱关联' })}: {r.graph_related_to.join(', ')}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* ── 目录态：概况 → 成册进度 → 目录 → 统计（2026-10-26 二次收敛裁决顺序，统计置尾） ── */
        <div className={s.homePage}>
          {/* First-time onboarding */}
          {isFirstTime && (
            <section className={s.onboardingCard}>
              <div className={s.onboardingHeader}>
                <HelpCircle size={18} className={s.onboardingIcon} />
                <h3 className={s.onboardingTitle}>{t('project.wiki.homePage.onboardTitle')}</h3>
              </div>
              <p className={s.onboardingDesc}>
                {t('project.wiki.homePage.onboardDesc')}
              </p>
              <div className={s.onboardingSteps}>
                <div className={s.onboardingStep}>
                  <span className={s.onboardingStepNum}>1</span>
                  <div className={s.onboardingStepContent}>
                    <span className={s.onboardingStepTitle}>{t('project.wiki.homePage.step1Title')}</span>
                    <span className={s.onboardingStepHint}>{t('project.wiki.homePage.step1Hint')}</span>
                  </div>
                </div>
                <div className={s.onboardingStep}>
                  <span className={s.onboardingStepNum}>2</span>
                  <div className={s.onboardingStepContent}>
                    <span className={s.onboardingStepTitle}>{t('project.wiki.homePage.step2Title')}</span>
                    <span className={s.onboardingStepHint}>{t('project.wiki.homePage.step2Hint')}</span>
                  </div>
                </div>
                <div className={s.onboardingStep}>
                  <span className={s.onboardingStepNum}>3</span>
                  <div className={s.onboardingStepContent}>
                    <span className={s.onboardingStepTitle}>{t('project.wiki.homePage.step3Title')}</span>
                    <span className={s.onboardingStepHint}>{t('project.wiki.homePage.step3Hint')}</span>
                  </div>
                </div>
              </div>
            </section>
          )}

          {/* Overview (collapsible) */}
          <section className={s.homeSection}>
            <div className={s.homeSectionHeader}>
              <div className={s.homeSectionTitle} style={{ cursor: 'pointer' }} onClick={() => setOverviewExpanded(!overviewExpanded)}>
                <FileText size={15} className={s.homeSectionIcon} />
                <h3>{t('project.wiki.overview')}</h3>
                {overview && (
                  <span className={s.homeToggleHint}>{overviewExpanded ? t('project.wiki.homePage.collapse') : t('project.wiki.homePage.expand')}</span>
                )}
              </div>
              {!editingOverview && (
                <button
                  className={s.editBtn}
                  onClick={() => {
                    if (overviewExpanded) {
                      setEditingOverview(true)
                    } else {
                      setOverviewExpanded(true)
                    }
                  }}
                >
                  <Edit3 size={12} /> {t('project.wiki.edit')}
                </button>
              )}
            </div>
            <p className={s.sectionHint}>{t('project.wiki.homePage.overviewHint')}</p>
            {editingOverview ? (
              <div className={s.editBlock}>
                <textarea
                  className={s.editTextarea}
                  value={draftOverview}
                  onChange={(e) => setDraftOverview(e.target.value)}
                  placeholder={t('project.wiki.editOverviewPlaceholder')}
                  rows={8}
                />
                <div className={s.editActions}>
                  <button className={s.editSaveBtn} onClick={handleSaveOverview} disabled={saving}>
                    {saving ? <Loader2 size={12} className={s.spin} /> : <Check size={12} />}
                    {t('project.wiki.save')}
                  </button>
                  <button className={s.editCancelBtn} onClick={() => { setEditingOverview(false); setDraftOverview(overview) }}>
                    <X size={12} /> {t('project.wiki.cancel')}
                  </button>
                </div>
              </div>
            ) : (
              <div className={s.homeSectionContent}>
                {overview ? (
                  overviewExpanded ? (
                    <Markdown content={overview} />
                  ) : (
                    <div className={s.homeOverviewPreview}>{overview.split('\n').slice(0, 3).join('\n')}</div>
                  )
                ) : (
                  <p className={s.homePlaceholder}>
                    {t('project.wiki.homePage.overviewGenerated')}
                  </p>
                )}
              </div>
            )}
          </section>

          {/* ── 成册进度轻量条：原 SVG 进度环压缩为一行——迷你进度条 + generated/total，
              整条可点直达知识框架视图（定义管理/逐页状态在那里） ── */}
          {completion && (
            <button
              className={s.progressLite}
              onClick={onOpenSchema}
              title={t('project.wiki.homePage.progressLiteTip')}
            >
              <ListChecks size={14} className={s.progressLiteIcon} />
              <span className={s.progressLiteTitle}>{t('project.wiki.homePage.completionTitle')}</span>
              <span className={s.progressLiteTrack}>
                <span
                  className={s.progressLiteFill}
                  style={{ width: `${Math.round((completion.generated / completion.total) * 100)}%` }}
                />
              </span>
              <span className={s.progressLiteCount}>
                {t('project.wiki.homePage.completionGenerated')} {completion.generated}/{completion.total}
              </span>
              <ChevronRight size={13} className={s.progressLiteGo} />
            </button>
          )}

          {/* 目录（原页面视图主体）：空态三态 / 分组 / 加载更多 / 计数行 */}
          <div className={s.homeIndex}>
            {pages.length === 0 && pendingCount === 0 ? (
              <p className={s.homePlaceholder}>
                {t('project.wiki.homePage.emptyIndex')}
              </p>
            ) : pages.length === 0 && pendingCount > 0 ? (
              /* 空态承接原汇编视图空态职责：待汇编即启动入口（IA 收敛 2026-10-26） */
              <div className={s.pagesEmptyCta}>
                <p className={s.homePlaceholder}>
                  {t('project.wiki.homePage.pendingCta', { count: pendingCount })}
                </p>
                <button
                  className={s.retryBtn}
                  onClick={onCompile}
                  disabled={compiling}
                >
                  <Play size={13} />
                  {compiling
                    ? t('project.wiki.homePage.compiling', { defaultValue: '汇编中…' })
                    : t('project.wiki.homePage.startCompile', { count: pendingCount })}
                </button>
              </div>
            ) : (
              <>
                {/* 跟踪台账组置顶（V2 E3）：is_ledger 命中页 + 缺失台账页置灰 */}
                {(ledgerPages.length > 0 || ledgerMissing.length > 0) && renderIndexGroup(
                  'ledger', ledgerPages, ledgerMissing,
                  { Icon: Clock, labelKey: 'project.wiki.homePage.indexGroupLedger', descKey: 'project.wiki.homePage.indexGroupLedgerDesc' },
                )}
                {groupedPages.map(([type, typePages]) =>
                  renderIndexGroup(type, typePages, type === 'structure' ? structureMissing : []))}
                {/* 知识框架组全缺（无已生成结构页）但仍有缺失定义：仅置灰行的组 */}
                {!groupedPages.some(([type]) => type === 'structure') && structureMissing.length > 0 &&
                  renderIndexGroup('structure', [], structureMissing)}
              </>
            )}
            {pages.length > 0 && hasMorePages && (
              <button
                className={s.loadMoreBtn}
                onClick={onLoadMorePages}
                disabled={pagesLoadingMore}
              >
                {pagesLoadingMore ? t('common.loading') : t('common.loadMore')}
              </button>
            )}
            {(pagesTotal ?? pages.length) > 0 && (
              <div className={s.pagesCountLine}>
                {t('project.wiki.homePage.pagesCount', { count: pagesTotal ?? pages.length })}
              </div>
            )}
          </div>

          {/* Stats bar（置尾，2026-10-26 二次收敛裁决） */}
          {((pagesTotal ?? 0) > 0 || sourcePageCount > 0) && (
            <div className={s.homeStats}>
              <div className={s.homeStatItem} title={t('project.wiki.homePage.statPagesTip')}>
                <span className={s.homeStatValue}>{pagesTotal ?? 0}</span>
                <span className={s.homeStatLabel}>{t('project.wiki.wikiPages')}</span>
              </div>
              <div className={s.homeStatItem} title={t('project.wiki.homePage.statFilesTip')}>
                <span className={s.homeStatValue}>{sourcePageCount}</span>
                <span className={s.homeStatLabel}>{t('project.wiki.sourceFiles')}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
