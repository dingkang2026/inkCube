/** src/features/project/tabs/WikiHomePage.tsx — Wiki home = index.md: purpose + overview + categorized page index */
import { useState, useEffect, useMemo } from 'react'
import {
  Target, FileText, Clock, Edit3, Check, X, Loader2,
  BookOpen, Users, Lightbulb, Layers, FolderOpen,
  Play, HelpCircle,
} from 'lucide-react'
import { Markdown } from '../../../components/Markdown'
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
}

/** Page type config: icon, label, description, order */
const PAGE_TYPE_CONFIG: Record<string, { Icon: typeof FileText; label: string; desc: string; order: number }> = {
  topic:     { Icon: Layers,     label: '主题', desc: '跨文档的综合分析', order: 0 },
  entity:    { Icon: Users,      label: '实体', desc: '人物、组织、产品', order: 1 },
  concept:   { Icon: Lightbulb,  label: '概念', desc: '方法、理论、术语', order: 2 },
  source:    { Icon: FolderOpen, label: '来源', desc: '每份文件的摘要', order: 3 },
  structure: { Icon: BookOpen,   label: '结构', desc: '框架定义的页面', order: 4 },
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
  onNavigate, onNavigateToPage, onCompile,
}: WikiHomePageProps) {
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
    } catch (err) {
      console.error('Failed to save purpose:', err)
    } finally {
      setSaving(false)
    }
  }

  const handleSaveOverview = async () => {
    setSaving(true)
    try {
      await window.electronAPI.invoke('wiki:updateOverview', { projectId, overview: draftOverview })
      setEditingOverview(false)
    } catch (err) {
      console.error('Failed to save overview:', err)
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

  const isFirstTime = pages.length === 0 && sourcePageCount === 0

  return (
    <div className={s.homePage}>
      {/* ── First-time onboarding ── */}
      {isFirstTime && (
        <section className={s.onboardingCard}>
          <div className={s.onboardingHeader}>
            <HelpCircle size={18} className={s.onboardingIcon} />
            <h3 className={s.onboardingTitle}>开始使用 Wiki</h3>
          </div>
          <p className={s.onboardingDesc}>
            Wiki 会自动将你的文件转化为结构化知识库。只需三步：
          </p>
          <div className={s.onboardingSteps}>
            <div className={s.onboardingStep}>
              <span className={s.onboardingStepNum}>1</span>
              <div className={s.onboardingStepContent}>
                <span className={s.onboardingStepTitle}>上传文件</span>
                <span className={s.onboardingStepHint}>在「素材」标签上传 PDF、PPT、Word 等文档</span>
              </div>
            </div>
            <div className={s.onboardingStep}>
              <span className={s.onboardingStepNum}>2</span>
              <div className={s.onboardingStepContent}>
                <span className={s.onboardingStepTitle}>编译知识库</span>
                <span className={s.onboardingStepHint}>AI 读取每份文件，自动提取实体、概念、主题</span>
              </div>
            </div>
            <div className={s.onboardingStep}>
              <span className={s.onboardingStepNum}>3</span>
              <div className={s.onboardingStepContent}>
                <span className={s.onboardingStepTitle}>浏览和搜索</span>
                <span className={s.onboardingStepHint}>在首页索引中浏览，或用搜索和图谱探索知识</span>
              </div>
            </div>
          </div>
          {pendingCount > 0 && (
            <button className={s.onboardingAction} onClick={onCompile}>
              <Play size={14} />
              开始编译 ({pendingCount} 个文件待处理)
            </button>
          )}
        </section>
      )}

      {/* ── Wiki Purpose ── */}
      <section className={s.homeSection}>
        <div className={s.homeSectionHeader}>
          <div className={s.homeSectionTitle}>
            <Target size={15} className={s.homeSectionIcon} />
            <h3>Wiki 目标</h3>
          </div>
          {!editingPurpose && (
            <button className={s.editBtn} onClick={() => setEditingPurpose(true)}>
              <Edit3 size={12} /> 编辑
            </button>
          )}
        </div>
        <p className={s.sectionHint}>定义这个 Wiki 要研究什么、关注哪些问题</p>
        {editingPurpose ? (
          <div className={s.editBlock}>
            <textarea
              className={s.editTextarea}
              value={draftPurpose}
              onChange={(e) => setDraftPurpose(e.target.value)}
              placeholder="例如：跟踪 XX 公司的融资进展、竞争格局和核心团队变动..."
              rows={4}
            />
            <div className={s.editActions}>
              <button className={s.editSaveBtn} onClick={handleSavePurpose} disabled={saving}>
                {saving ? <Loader2 size={12} className={s.spin} /> : <Check size={12} />}
                保存
              </button>
              <button className={s.editCancelBtn} onClick={() => { setEditingPurpose(false); setDraftPurpose(purpose) }}>
                <X size={12} /> 取消
              </button>
            </div>
          </div>
        ) : (
          <div className={s.homeSectionContent}>
            {purpose ? (
              <Markdown content={purpose} />
            ) : (
              <p className={s.homePlaceholder}>
                还没有定义 Wiki 目标。点击「编辑」来设定研究方向。
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
            <h3>全局摘要</h3>
            {overview && (
              <span className={s.homeToggleHint}>{overviewExpanded ? '收起' : '展开'}</span>
            )}
          </div>
          {!editingOverview && (
            <button className={s.editBtn} onClick={() => overviewExpanded ? setEditingOverview(true) : setOverviewExpanded(true)}>
              <Edit3 size={12} /> 编辑
            </button>
          )}
        </div>
        <p className={s.sectionHint}>AI 自动维护的知识快照，每次编译后更新。也可手动编辑修正。</p>
        {editingOverview ? (
          <div className={s.editBlock}>
            <textarea
              className={s.editTextarea}
              value={draftOverview}
              onChange={(e) => setDraftOverview(e.target.value)}
              placeholder="Wiki 全局摘要..."
              rows={8}
            />
            <div className={s.editActions}>
              <button className={s.editSaveBtn} onClick={handleSaveOverview} disabled={saving}>
                {saving ? <Loader2 size={12} className={s.spin} /> : <Check size={12} />}
                保存
              </button>
              <button className={s.editCancelBtn} onClick={() => { setEditingOverview(false); setDraftOverview(overview) }}>
                <X size={12} /> 取消
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
                编译文件后，AI 会自动生成知识摘要。
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
            <h3>内容索引</h3>
            {pages.length > 0 && (
              <span className={s.homeIndexCount}>{pages.length} 页</span>
            )}
          </div>
          {pages.length > 0 && (
            <button className={s.editBtn} onClick={() => onNavigate('changelog')}>
              <Clock size={12} /> 变更日志
            </button>
          )}
        </div>
        <p className={s.sectionHint}>按类型浏览所有 Wiki 页面，点击查看详情。也可用左侧「搜索」和「图谱」探索。</p>

        {pages.length === 0 && !isFirstTime ? (
          <div className={s.homeSectionContent}>
            <p className={s.homePlaceholder}>
              {pendingCount > 0
                ? `有 ${pendingCount} 个新文件待编译，点击上方「编译」按钮生成 Wiki 页面。`
                : '暂无 Wiki 页面。在「素材」标签上传文件后，回到这里点击「编译」。'}
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
                    <span className={s.homeIndexGroupLabel}>{config.label}</span>
                    <span className={s.homeIndexGroupDesc}>{config.desc}</span>
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
          <div className={s.homeStatItem} title="AI 从文件中提取的结构化知识页面">
            <span className={s.homeStatValue}>{pages.length}</span>
            <span className={s.homeStatLabel}>Wiki 页面</span>
          </div>
          <div className={s.homeStatItem} title="你上传的原始文档（PDF、PPT、Word 等）">
            <span className={s.homeStatValue}>{sourcePageCount}</span>
            <span className={s.homeStatLabel}>来源文件</span>
          </div>
        </div>
      )}
    </div>
  )
}
