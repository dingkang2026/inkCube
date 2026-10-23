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
  /** 三型审查计数（矛盾/缺口/死链），父级 WikiTab 从 reviews 派生；全 0 时健康卡不渲染（3.0 §6.1.2） */
  reviewCounts?: { contradiction: number; gap: number; deadLink: number }
  /** 「去处理」直达治理视图（lint） */
  onOpenLint?: () => void
  /** 结构页定义清单（父级 wiki:getSchemas 拉取），用于完成度区块；空/缺省隐藏（3.0 一期 Task 6） */
  schemas?: Array<{ page_key: string; title: string }>
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

export function WikiHomePage({
  projectId, purpose, overview, pages, sourcePageCount, pendingCount,
  onNavigate, onNavigateToPage, onCompile, onMetaSaved, reviewCounts, onOpenLint, schemas, missingPages,
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

  // Group pages by page_type
  const groupedPages = useMemo(() => {
    const groups: Record<string, WikiPage[]> = {}
    for (const page of pages) {
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
  }, [pages])

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

      {/* ── Health card (3.0 §6.1.2)：矛盾/缺口/死链三型计数，全 0 整卡不渲染 ── */}
      {reviewCounts && (reviewCounts.contradiction > 0 || reviewCounts.gap > 0 || reviewCounts.deadLink > 0) && (
        <section className={s.healthCard}>
          <div className={s.healthHeader}>
            <AlertTriangle size={15} className={s.healthIcon} />
            <h3 className={s.healthTitle}>{t('project.wiki.homePage.healthTitle')}</h3>
            <button className={s.healthGo} onClick={onOpenLint}>
              {t('project.wiki.homePage.healthGo')}
            </button>
          </div>
          <div className={s.healthChips}>
            {reviewCounts.contradiction > 0 && (
              <span className={s.healthChipErr}>{t('project.wiki.homePage.healthContradiction')} {reviewCounts.contradiction}</span>
            )}
            {reviewCounts.gap > 0 && (
              <span className={s.healthChipWarn}>{t('project.wiki.homePage.healthGap')} {reviewCounts.gap}</span>
            )}
            {reviewCounts.deadLink > 0 && (
              <span className={s.healthChipErr}>{t('project.wiki.homePage.healthDeadLink')} {reviewCounts.deadLink}</span>
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

      {/* ── Schema completion (3.0 一期 Task 6)：结构页完成度进度条 + 已生成/骨架 chips ── */}
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
            <div className={s.completionBar}>
              <div
                className={s.completionBarFill}
                style={{ width: `${Math.round((completion.generated / completion.total) * 100)}%` }}
              />
            </div>
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
            {groupedPages.map(([type, typePages]) => {
              const config = PAGE_TYPE_CONFIG[type] || PAGE_TYPE_CONFIG.structure
              const { Icon } = config
              return (
                <div key={type} className={s.homeIndexGroup}>
                  <div className={s.homeIndexGroupHeader}>
                    <Icon size={13} className={s.homeIndexGroupIcon} />
                    <span className={s.homeIndexGroupLabel}>{t(config.labelKey)}</span>
                    <span className={s.homeIndexGroupDesc}>{t(config.descKey)}</span>
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
                  </div>
                </div>
              )
            })}
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
