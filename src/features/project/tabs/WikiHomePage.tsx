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
  onNavigate, onNavigateToPage, onCompile, onMetaSaved, reviewCounts, onOpenLint, schemas,
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

  // 结构页完成度：schemas 中 page_key 已存在于已编译 pages 的比例（3.0 一期 Task 6）；total=0 整块隐藏
  const completion = useMemo(() => {
    if (!schemas || schemas.length === 0) return null
    const pageKeys = new Set(pages.map((p) => p.page_key))
    const generated = schemas.filter((sc) => pageKeys.has(sc.page_key)).length
    return { total: schemas.length, generated, pageKeys }
  }, [schemas, pages])

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
            <button className={s.onboardingAction} onClick={onCompile}>
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
                completion.pageKeys.has(sc.page_key) ? (
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
            <button className={s.editBtn} onClick={() => setEditingPurpose(true)}>
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
            <button className={s.editBtn} onClick={() => overviewExpanded ? setEditingOverview(true) : setOverviewExpanded(true)}>
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
            {pages.length > 0 && (
              <span className={s.homeIndexCount}>{t('project.wiki.homePage.pagesCount', { count: pages.length })}</span>
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
          </div>
        ) : null}
      </section>

      {/* ── Stats bar ── */}
      {(pages.length > 0 || sourcePageCount > 0) && (
        <div className={s.homeStats}>
          <div className={s.homeStatItem} title={t('project.wiki.homePage.statPagesTip')}>
            <span className={s.homeStatValue}>{pages.length}</span>
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
