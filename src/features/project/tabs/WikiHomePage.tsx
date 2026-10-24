/** src/features/project/tabs/WikiHomePage.tsx — Wiki home = index.md: purpose + overview + categorized page index */
import { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Target, FileText, Clock, Edit3, Check, X, Loader2,
  BookOpen, Users, Lightbulb, Layers, FolderOpen,
  Play, HelpCircle, AlertTriangle, ListChecks,
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

/** 台账条目(wiki:listLedgerEntries 返回行,3.0 四期 T7) */
export interface LedgerEntry {
  id: string
  page_key: string
  entry_date: string | null
  entry_type: string
  content: string
}

interface WikiHomePageProps {
  projectId: string
  purpose: string
  overview: string
  pages: WikiPage[]
  sourcePageCount: number
  pendingCount: number
  onNavigate: (view: 'changelog') => void
  onNavigateToPage: (pageKey: string) => void
  onCompile: () => void
  /** purpose/overview 保存成功后回调父级刷新（避免保存后界面回显旧值） */
  onMetaSaved?: () => void
  /** 三型审查计数（矛盾/缺口/死链），父级 WikiTab 从 reviews 派生；信任信号行消费前两型
   *  +pendingCount（V2 E3 死链撤离封面，体检面板分型保留） */
  reviewCounts?: { contradiction: number; gap: number }
  /** 「去处理」直达治理视图（lint） */
  onOpenLint?: () => void
  /** 「待汇编」chip 直达汇编视图（V2 E3 信任信号行） */
  onOpenCompile?: () => void
  /** 结构页定义清单（父级 wiki:getSchemas 拉取，含 is_ledger 台账标记），用于完成度区块
   *  与目录台账组置顶判定；空/缺省隐藏（3.0 一期 Task 6；is_ledger 为 V2 E3 新增消费） */
  schemas?: Array<{ page_key: string; title: string; is_ledger?: boolean }>
  /** 结构页缺失清单（wiki:listPages.missing_pages，后端基于全量 page_key 集：
   *  完成度消费它而非分页后的 pages 复算，大项目不再低估，2026-10-22） */
  missingPages?: Array<{ page_key: string; title: string }>
  /** 台账条目时间轴（3.0 四期：父级 wiki:listLedgerEntries 拉取）；空/undefined 整卡不渲染 */
  ledgerEntries?: LedgerEntry[]
  /** 台账解析进行中（心跳 ledger_parse 含本项目）：卡头灰字提示，旧条目照常可读 */
  ledgerParsing?: boolean
  /** 页面总数（wiki:listPages 响应 total）：分页后 pages.length 只是已加载数，计数显示优先用 total */
  pagesTotal?: number | null
  /** pages 是否还有更多（has_more）+ 加载更多回调/进行中态（0.1dev-guide 统一约定） */
  hasMorePages?: boolean
  pagesLoadingMore?: boolean
  onLoadMorePages?: () => void
  /** 台账时间轴是否还有更多 + 加载更多回调/进行中态 */
  hasMoreLedger?: boolean
  ledgerLoadingMore?: boolean
  onLoadMoreLedger?: () => void
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

/** 成册进度环周长（r=20,viewBox 48,V2 E3 §3.5） */
const RING_RADIUS = 20
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS

export function WikiHomePage({
  projectId, purpose, overview, pages, sourcePageCount, pendingCount,
  onNavigate, onNavigateToPage, onCompile, onMetaSaved, reviewCounts, onOpenLint, onOpenCompile, schemas, missingPages,
  ledgerEntries, ledgerParsing, pagesTotal,
  hasMorePages, pagesLoadingMore, onLoadMorePages,
  hasMoreLedger, ledgerLoadingMore, onLoadMoreLedger,
}: WikiHomePageProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [editingPurpose, setEditingPurpose] = useState(false)
  const [editingOverview, setEditingOverview] = useState(false)
  const [draftPurpose, setDraftPurpose] = useState(purpose)
  const [draftOverview, setDraftOverview] = useState(overview)
  const [saving, setSaving] = useState(false)
  const [overviewExpanded, setOverviewExpanded] = useState(false)

  useEffect(() => { setDraftPurpose(purpose) }, [purpose])
  useEffect(() => { setDraftOverview(overview) }, [overview])

  const handleSavePurpose = async () => {
    setSaving(true)
    try {
      await window.electronAPI.invoke('wiki:updatePurpose', { projectId, purpose: draftPurpose })
      setEditingPurpose(false)
      onMetaSaved?.() // 父级刷新 purpose → props 回流，避免保存后仍显示旧值
    } catch (err) {
      console.error('Failed to save purpose:', err)
      showToast(t('project.wiki.saveMetaFailed', {
        reason: err instanceof Error ? err.message : String(err),
        defaultValue: `保存失败: ${err instanceof Error ? err.message : String(err)}`,
      }), 'error')
    } finally {
      setSaving(false)
    }
  }

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

  // V2 E3 目录语义分组：台账页单列置顶组（is_ledger 为唯一判定，0.6 §6.3-5——
  // 分组只改呈现不改 page_type 语义），其余仍按 page_type 分组
  const ledgerKeys = useMemo(
    () => new Set((schemas ?? []).filter((sc) => sc.is_ledger).map((sc) => sc.page_key)),
    [schemas],
  )

  // Group pages by page_type（台账页除外，单列置顶）
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
  // 不受 pages 分页影响——2026-10-22 修复：原用已加载分页 pages 复算 pageKeys，
  // 页面超单页量时完成度被低估）；total=0 整块隐藏
  const completion = useMemo(() => {
    if (!schemas || schemas.length === 0) return null
    const missingKeys = new Set((missingPages ?? []).map((m) => m.page_key))
    const generated = schemas.filter((sc) => !missingKeys.has(sc.page_key)).length
    return { total: schemas.length, generated, missingKeys }
  }, [schemas, missingPages])

  // 台账时间轴分组（3.0 四期 T7）：按 entry_date 归并（后端已倒序,NULL 组自然置尾）;组内保持倒序
  const ledgerGroups = useMemo(() => {
    if (!ledgerEntries || ledgerEntries.length === 0) return null
    const groups: Record<string, LedgerEntry[]> = {}
    for (const e of ledgerEntries) {
      const key = e.entry_date ?? '—'
      ;(groups[key] ??= []).push(e)
    }
    return Object.entries(groups)
  }, [ledgerEntries])
  /** 条目行标题:pages 中按 page_key 找页标题,找不到显示 page_key 本身(计划 T7 实现要点) */
  const ledgerPageTitle = (key: string) => pages.find((p) => p.page_key === key)?.title || key

  const isFirstTime = pages.length === 0 && sourcePageCount === 0

  /** 目录组渲染（V2 E3）：已生成页可点 + 缺失页置灰行（「待汇编」小标不可点）；
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

  return (
    <div className={s.homePage}>
      {/* ── First-time onboarding ── */}
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
          {pendingCount > 0 && (
            <button
              className={s.onboardingAction}
              onClick={onCompile}
            >
              <Play size={14} />
              {t('project.wiki.homePage.startCompile', { count: pendingCount })}
            </button>
          )}
        </section>
      )}

      {/* ── 信任信号行（V2 E3 §3.5）：矛盾/缺口/待汇编三项，全 0 不渲染。
          死链撤离封面（裁决：低危工程信号，体检面板分型与脉搏带合并口径保留）；
          chip 可点直达整理层；仅待汇编时头部「去处理」不渲染（chip 即入口）── */}
      {reviewCounts && (reviewCounts.contradiction > 0 || reviewCounts.gap > 0 || pendingCount > 0) && (
        <section className={s.healthCard}>
          <div className={s.healthHeader}>
            <AlertTriangle size={15} className={s.healthIcon} />
            <h3 className={s.healthTitle}>{t('project.wiki.homePage.healthTitle')}</h3>
            {(reviewCounts.contradiction > 0 || reviewCounts.gap > 0) && (
              <button className={s.healthGo} onClick={onOpenLint}>
                {t('project.wiki.homePage.healthGo')}
              </button>
            )}
          </div>
          <div className={s.healthChips}>
            {reviewCounts.contradiction > 0 && (
              <button className={s.healthChipErr} onClick={onOpenLint}>
                {t('project.wiki.homePage.healthContradiction')} {reviewCounts.contradiction}
              </button>
            )}
            {reviewCounts.gap > 0 && (
              <button className={s.healthChipWarn} onClick={onOpenLint}>
                {t('project.wiki.homePage.healthGap')} {reviewCounts.gap}
              </button>
            )}
            {pendingCount > 0 && (
              <button className={s.healthChipWarn} onClick={onOpenCompile}>
                {t('project.wiki.homePage.healthPending')} {pendingCount}
              </button>
            )}
          </div>
        </section>
      )}

      {/* ── Ledger timeline card (3.0 四期 T7)：台账条目按日期分组,点击行直达台账页;空不渲染 ── */}
      {ledgerGroups && (
        <section className={s.homeSection}>
          <div className={s.homeSectionHeader}>
            <div className={s.homeSectionTitle}>
              <Clock size={15} className={s.homeSectionIcon} />
              <h3>{t('project.wiki.timeline.title')}</h3>
              {ledgerParsing && (
                <span className={s.homeToggleHint}>{t('project.wiki.timeline.parsing')}</span>
              )}
            </div>
          </div>
          <div className={s.homeSectionContent}>
            <div className={s.timelineWrap}>
              {ledgerGroups.map(([date, items]) => (
                <div key={date} className={s.timelineGroup}>
                  <div className={s.timelineDate}>{date}</div>
                  <div className={s.timelineList}>
                    {items.map((e) => (
                      <button key={e.id} className={s.timelineItem}
                        onClick={() => onNavigateToPage(e.page_key)}>
                        <span className={s.timelinePageTitle}>{ledgerPageTitle(e.page_key)}</span>
                        <span className={s.timelineContent}>{e.content}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {hasMoreLedger && (
                <button
                  className={s.loadMoreBtn}
                  onClick={onLoadMoreLedger}
                  disabled={ledgerLoadingMore}
                >
                  {ledgerLoadingMore ? t('common.loading') : t('common.loadMore')}
                </button>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── 成册进度环（V2 E3 §3.5）：进度条升级为环形，generated/total + 已生成/骨架 chips ── */}
      {completion && (
        <section className={s.homeSection}>
          <div className={s.homeSectionHeader}>
            <div className={s.homeSectionTitle}>
              <ListChecks size={15} className={s.homeSectionIcon} />
              <h3>{t('project.wiki.homePage.completionTitle')}</h3>
              <span className={s.homeIndexCount}>
                {t('project.wiki.homePage.completionGenerated')} {completion.generated}/{completion.total}
              </span>
            </div>
          </div>
          <div className={s.completionBody}>
            <svg
              className={s.completionRing}
              viewBox="0 0 48 48"
              role="img"
              aria-label={`${t('project.wiki.homePage.completionTitle')} ${completion.generated}/${completion.total}`}
            >
              <circle className={s.completionRingTrack} cx="24" cy="24" r="20" />
              <circle
                className={s.completionRingFill}
                cx="24" cy="24" r="20"
                strokeDasharray={`${RING_CIRCUMFERENCE}`}
                strokeDashoffset={RING_CIRCUMFERENCE * (1 - completion.generated / completion.total)}
                transform="rotate(-90 24 24)"
              />
            </svg>
            <div className={s.completionChips}>
              {schemas?.map((sc) =>
                !completion.missingKeys.has(sc.page_key) ? (
                  <span key={sc.page_key} className={s.completionChipDone}>
                    <Check size={11} /> {sc.title}
                  </span>
                ) : (
                  <span key={sc.page_key} className={s.completionChipMissing}>{sc.title}</span>
                ),
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── Wiki Purpose ── */}
      <section className={s.homeSection}>
        <div className={s.homeSectionHeader}>
          <div className={s.homeSectionTitle}>
            <Target size={15} className={s.homeSectionIcon} />
            <h3>{t('project.wiki.purpose')}</h3>
          </div>
          {!editingPurpose && (
            <button
              className={s.editBtn}
              onClick={() => setEditingPurpose(true)}
            >
              <Edit3 size={12} /> {t('project.wiki.edit')}
            </button>
          )}
        </div>
        <p className={s.sectionHint}>{t('project.wiki.homePage.purposeHint')}</p>
        {editingPurpose ? (
          <div className={s.editBlock}>
            <textarea
              className={s.editTextarea}
              value={draftPurpose}
              onChange={(e) => setDraftPurpose(e.target.value)}
              placeholder={t('project.wiki.homePage.purposePlaceholder')}
              rows={4}
            />
            <div className={s.editActions}>
              <button className={s.editSaveBtn} onClick={handleSavePurpose} disabled={saving}>
                {saving ? <Loader2 size={12} className={s.spin} /> : <Check size={12} />}
                {t('project.wiki.save')}
              </button>
              <button className={s.editCancelBtn} onClick={() => { setEditingPurpose(false); setDraftPurpose(purpose) }}>
                <X size={12} /> {t('project.wiki.cancel')}
              </button>
            </div>
          </div>
        ) : (
          <div className={s.homeSectionContent}>
            {purpose ? (
              <Markdown content={purpose} />
            ) : (
              <p className={s.homePlaceholder}>
                {t('project.wiki.noPurpose')}
              </p>
            )}
          </div>
        )}
      </section>

      {/* ── Wiki Overview (collapsible) ── */}
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

      {/* ── Page Index (index.md) ── */}
      <section className={s.homeSection}>
        <div className={s.homeSectionHeader}>
          <div className={s.homeSectionTitle}>
            <BookOpen size={15} className={s.homeSectionIcon} />
            <h3>{t('project.wiki.homePage.indexTitle')}</h3>
            {(pagesTotal ?? pages.length) > 0 && (
              <span className={s.homeIndexCount}>{t('project.wiki.homePage.pagesCount', { count: pagesTotal ?? pages.length })}</span>
            )}
          </div>
          {pages.length > 0 && (
            <button className={s.editBtn} onClick={() => onNavigate('changelog')}>
              <Clock size={12} /> {t('project.wiki.changeLog')}
            </button>
          )}
        </div>
        <p className={s.sectionHint}>{t('project.wiki.homePage.indexHint')}</p>

        {pages.length === 0 && !isFirstTime ? (
          <div className={s.homeSectionContent}>
            <p className={s.homePlaceholder}>
              {pendingCount > 0
                ? t('project.wiki.homePage.pendingCta', { count: pendingCount })
                : t('project.wiki.homePage.emptyIndex')}
            </p>
          </div>
        ) : pages.length > 0 ? (
          <div className={s.homeIndex}>
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
            {hasMorePages && (
              <button
                className={s.loadMoreBtn}
                onClick={onLoadMorePages}
                disabled={pagesLoadingMore}
              >
                {pagesLoadingMore ? t('common.loading') : t('common.loadMore')}
              </button>
            )}
          </div>
        ) : null}
      </section>

      {/* ── Stats bar ── */}
      {(pages.length > 0 || sourcePageCount > 0) && (
        <div className={s.homeStats}>
          <div className={s.homeStatItem} title={t('project.wiki.homePage.statPagesTip')}>
            <span className={s.homeStatValue}>{pagesTotal ?? pages.length}</span>
            <span className={s.homeStatLabel}>{t('project.wiki.wikiPages')}</span>
          </div>
          <div className={s.homeStatItem} title={t('project.wiki.homePage.statFilesTip')}>
            <span className={s.homeStatValue}>{sourcePageCount}</span>
            <span className={s.homeStatLabel}>{t('project.wiki.sourceFiles')}</span>
          </div>
        </div>
      )}
    </div>
  )
}
