/** src/features/project/tabs/ReportWizard.tsx — 2-step report creation wizard (real templates) */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Search, ChevronRight, Loader2, Globe } from 'lucide-react'
import type { ProjectType } from '../types'
import { STAGE_MAP } from '../types'
import s from './ReportWizard.module.css'
import { useSettings } from '../../settings/useSettings'
import { useFeatureGuard } from '../../readiness/useReadiness'
import { useToast } from '../../knowledge/Toast'

/** Template from backend */
interface BackendTemplate {
  template_id: string
  template_name: string
  version: string
  project_types: string[]
  stage: string | null
  output_format: string
  role_hint?: string
  audience?: string
  report_depth: string
  chapter_count: number
  is_custom: boolean
}

/** Structured template variable (schema v2, $defs/variable) */
interface TemplateVariable {
  name: string
  source: 'user' | 'derived'
  label?: string
  required?: boolean
  placeholder?: string
  depends_on?: string[]
  derive_hint?: string
  data_sources?: string[]
  cardinality?: 'scalar' | 'list'
}

/** Full template with outline (from getTemplate) */
interface FullTemplate extends BackendTemplate {
  outline: OutlineNode[]
  variables?: TemplateVariable[]
  global_requirements?: string[]
  [key: string]: unknown
}

/** 按章分组的大纲（与后端 get_execution_sections 镜像：有 children 的章 →
 *  children 为执行节；无 children 的章自身即节）。post_body 节不走检索
 *  （2.5 §前端交互：不检索、无开关），仍展示以与模板管理结构保持一致。 */
interface OutlineGroup {
  /** 有 children 的章显示组头；无 children 的章为 null（章自身即单节行） */
  chapterTitle: string | null
  sections: { section_id: string; title: string; postBody: boolean }[]
}

interface OutlineNode {
  section_id: string
  title: string
  description?: string
  compose_mode?: string
  prompt_hint?: string
  children?: OutlineNode[]
}

/** 知识源条目：静态（项目库 + 预置库，nameKey 走 i18n）+ 动态（自定义库，直用 name） */
interface KnowledgeSourceEntry {
  id: string
  /** 静态条目的 i18n 键；动态自定义库条目为 null（用 name） */
  nameKey: string | null
  /** 动态条目显示名（自定义库名快照）；静态条目无 */
  name?: string
  autoMatched: boolean
  matchType?: ProjectType
  baseLibId?: string
}

/** Static knowledge source entries (project KB + preset libs)。
 * 项目库默认勾选可取消（§1.2 include_project_kb，2026-10-27）；
 * 取消后章节检索与变量抽值均排除本项目知识库（仅勾选的基础库）。 */
const STATIC_SOURCE_ENTRIES: KnowledgeSourceEntry[] = [
  { id: 'ks-project', nameKey: 'project.report.wizard.projectKb', autoMatched: false },
  { id: 'ks-recruitment', nameKey: 'project.report.wizard.recruitmentLib', autoMatched: true, matchType: 'recruitment' as ProjectType, baseLibId: 'bl-recruitment' },
  { id: 'ks-investment', nameKey: 'project.report.wizard.investmentLib', autoMatched: true, matchType: 'investment' as ProjectType, baseLibId: 'bl-investment' },
]

/** 预置库判定（与后端 is_preset_base_lib 同口径）：lib_type 以 preset_ 开头 */
function isPresetLib(libType: string | null | undefined): boolean {
  return !!libType && libType.startsWith('preset_')
}

/** 动态自定义库条目的知识源 id 前缀 */
const CUSTOM_KS_PREFIX = 'ks-'

/**
 * Persist the wizard's checked sources back to project_base_libs (2.7 §5.5):
 * the selection becomes the project's association set, so the next wizard
 * run pre-checks the same sources. Auto-created links are refined by user
 * choices (unchecking an auto-linked lib unlinks it). Failures are
 * non-fatal — generation has already started; the wizard just falls back
 * to the current associations next time. Caller must skip this when the
 * association set failed to load (sourcesLoadedRef) — writing back a
 * degraded selection would wipe existing associations.
 */
async function syncBaseLibAssociations(projectId: string, targetLibIds: Set<string>) {
  try {
    const result: any = await window.electronAPI.invoke('project:getBaseLibs', projectId)
    const currentIds = new Set<string>(
      (result?.base_libs || []).map((lib: any) => lib.id),
    )
    const toLink = [...targetLibIds].filter(id => !currentIds.has(id))
    const toUnlink = [...currentIds].filter(id => !targetLibIds.has(id))
    await Promise.all([
      ...toLink.map(baseLibId =>
        window.electronAPI.invoke('project:linkBaseLib', { projectId, baseLibId })),
      ...toUnlink.map(baseLibId =>
        window.electronAPI.invoke('project:unlinkBaseLib', { projectId, baseLibId })),
    ])
  } catch (err) {
    console.error('Failed to sync project base-lib associations:', err)
  }
}

interface ReportWizardProps {
  projectId: string
  projectType: ProjectType
  onComplete: () => void
  onCancel: () => void
}

export function ReportWizard({ projectId, projectType, onComplete, onCancel }: ReportWizardProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const { settings } = useSettings()
  const guard = useFeatureGuard()
  // 互联网总开关可用性与后端 web_search_cfg_from_settings 同口径：
  // 已选供应商且所选 Key 非 strip 后空串才算已配置（2026-10-19 供应商化；
  // trim 对齐后端 .strip()，纯空格 Key 不放行总闸）
  const wsProvider = settings?.web_search_provider ?? ''
  const wsKey =
    wsProvider === 'zhipu' ? settings?.zhipu_search_key ?? ''
    : wsProvider === 'tavily' ? settings?.tavily_key ?? ''
    : ''
  const internetConfigured = wsKey.trim().length > 0
  const [step, setStep] = useState(1)
  const [searchQuery, setSearchQuery] = useState('')
  const [templates, setTemplates] = useState<BackendTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedTemplate, setSelectedTemplate] = useState<FullTemplate | null>(null)
  const [checkedSources, setCheckedSources] = useState<Set<string>>(new Set())
  const [supplementaryNotes, setSupplementaryNotes] = useState('')
  const [variableValues, setVariableValues] = useState<Record<string, string>>({})
  const [internetEnabled, setInternetEnabled] = useState(false)
  const [sectionInternet, setSectionInternet] = useState<Record<string, boolean>>({})
  const [generating, setGenerating] = useState(false)
  // 自定义基础库（baseLib:list 全量、滤预置）：知识源动态拼接（2.7 §3.1）
  const [customLibs, setCustomLibs] = useState<Array<{ id: string; name: string }>>([])
  // loadSources 是否成功拿到关联集：失败时生成不回写关联（fail-safe，防降级
  // 勾选集经 syncBaseLibAssociations 反向清空存量关联——CodeReview M-3 2026-10-26）
  const sourcesLoadedRef = useRef(false)

  // Load templates from backend
  useEffect(() => {
    let cancelled = false
    async function loadTemplates() {
      try {
        const res: any = await window.electronAPI.invoke('reports:listTemplates', {
          projectType,
        })
        if (!cancelled) {
          setTemplates(res.templates || [])
          setLoading(false)
        }
      } catch (err) {
        console.error('Failed to load templates:', err)
        if (!cancelled) setLoading(false)
      }
    }
    loadTemplates()
    return () => { cancelled = true }
  }, [projectType])

  // Initialize knowledge sources from the project's real base-lib
  // associations (2.7 §5.5): links may have been auto-created at project
  // creation or edited by the user since — a projectType hard-match would
  // check the wrong boxes and never pre-check linked custom libs.
  // 自定义库恢复后同 effect 顺带拉取 baseLib:list（全量、滤预置，串行 await
  // 与关联集分离失败域）：动态条目预勾同样以关联集为准（预置收敛期 2.9 D3
  // 曾仅静态三源，2.7 §7 核销）
  useEffect(() => {
    let cancelled = false
    sourcesLoadedRef.current = false
    async function loadSources() {
      const initial = new Set<string>()
      initial.add('ks-project')
      // 决策性数据（关联集）与展示性数据（全量库列表）分离拉取（M-3）：
      // baseLib:list 失败只降级自定义库条目展示，不连坐预勾；关联集失败
      // 放弃预勾（仅静态源）并保持 sourcesLoaded=false，生成时不回写关联
      let linkedIds = new Set<string>()
      let assocLoaded = false
      let customs: Array<{ id: string; name: string }> = []
      try {
        const assoc: any = await window.electronAPI.invoke('project:getBaseLibs', projectId)
        linkedIds = new Set<string>(
          ((assoc as any)?.base_libs || []).map((lib: any) => lib.id),
        )
        assocLoaded = true
      } catch (err) {
        console.error('Failed to load project base-lib associations:', err)
      }
      try {
        const libsRes: any = await window.electronAPI.invoke('baseLib:list', { limit: 0 })
        customs = (((libsRes as any)?.base_libs || []) as Array<{ id: string; name: string; lib_type: string | null }>)
          .filter(lib => !isPresetLib(lib.lib_type))
          .map(lib => ({ id: lib.id, name: lib.name }))
      } catch (err) {
        console.error('Failed to load custom base libs:', err)
      }
      if (cancelled) return
      setCustomLibs(customs)
      if (assocLoaded) {
        STATIC_SOURCE_ENTRIES.forEach(ks => {
          if (ks.baseLibId && linkedIds.has(ks.baseLibId)) {
            initial.add(ks.id)
          }
        })
        customs.forEach(lib => {
          if (linkedIds.has(lib.id)) initial.add(`${CUSTOM_KS_PREFIX}${lib.id}`)
        })
      }
      sourcesLoadedRef.current = assocLoaded
      setCheckedSources(initial)
    }
    loadSources()
    return () => { cancelled = true }
  }, [projectId])

  // 静态三源 + 动态自定义库拼接（2.7 §3.1）：自定义库 id 与预勾/回写一致
  // （`ks-${lib.id}`），targetLibIds 收集逻辑对两类条目通用
  const knowledgeSources = useMemo<KnowledgeSourceEntry[]>(() => [
    ...STATIC_SOURCE_ENTRIES,
    ...customLibs.map(lib => ({
      id: `${CUSTOM_KS_PREFIX}${lib.id}`,
      nameKey: null,
      name: lib.name,
      autoMatched: false,
      baseLibId: lib.id,
    })),
  ], [customLibs])

  // User-sourced variables that require manual input in the wizard
  const userVariables = useMemo<TemplateVariable[]>(
    () => (selectedTemplate?.variables || []).filter(v => v.source === 'user'),
    [selectedTemplate],
  )

  // 按章分组的大纲数据（渲染用；sectionInternet 开关状态按 section_id 存取）
  const outlineGroups = useMemo<OutlineGroup[]>(() => {
    const toSec = (n: OutlineNode) => ({
      section_id: n.section_id,
      title: n.title,
      postBody: (n.compose_mode || 'standard') === 'post_body',
    })
    return (selectedTemplate?.outline || []).map((ch) => {
      const children = ch.children || []
      return children.length > 0
        ? { chapterTitle: ch.title, sections: children.map(toSec) }
        : { chapterTitle: null, sections: [toSec(ch)] }
    })
  }, [selectedTemplate])

  const handleSelectTemplate = useCallback(async (tpl: BackendTemplate) => {
    // Fetch full template with outline
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      const full = res.template as FullTemplate
      setSelectedTemplate(full)

      // Initialize variable values (only user-sourced variables need manual input)
      const vars: Record<string, string> = {}
      ;(full.variables || []).filter(v => v.source === 'user').forEach(v => { vars[v.name] = '' })
      setVariableValues(vars)

      // Reset per-section internet toggles for the newly selected template
      setSectionInternet({})

      setStep(2)
    } catch (err) {
      console.error('Failed to load template details:', err)
    }
  }, [])

  const doStartGenerate = useCallback(async () => {
    if (!selectedTemplate) return

    // 必填变量非空校验（与后端 resolver 同口径：空白归一 UNRESOLVED 后
    // required fast-fail；前端拦截给出即时反馈，不进生成流程）
    const missing = userVariables
      .filter(v => v.required !== false)
      .filter(v => !(variableValues[v.name] || '').trim())
      .map(v => v.label || v.name)
    if (missing.length > 0) {
      showToast(t('project.report.wizard.missingVariables', { names: missing.join('、') }), 'error')
      return
    }

    setGenerating(true)

    // Map checked sources → the set of base-lib IDs to retrieve from; the
    // same set is persisted as project associations after generation starts.
    const targetLibIds = new Set<string>()
    for (const ks of knowledgeSources) {
      if (!checkedSources.has(ks.id)) continue
      if (ks.id === 'ks-project') continue
      if (ks.baseLibId) targetLibIds.add(ks.baseLibId)
    }
    const baseProjectIds: string[] = [...targetLibIds]
    // 项目知识库纳入开关（§1.2）：取消勾选后后端章节检索与变量抽值
    // 均排除本项目知识库；undefined 由 IPC 层归一 true（默认纳入）
    const includeProjectKb = checkedSources.has('ks-project')

    try {
      const result: any = await window.electronAPI.invoke('reports:generateReport', {
        projectId,
        templateId: selectedTemplate.template_id,
        title: selectedTemplate.template_name,
        variables: variableValues,
        baseProjectIds,
        includeProjectKb,
        supplementaryNotes,
        sectionInternet,
        internetEnabled,
      })

      if (result.report_id) {
        // Generation started in background — return to kanban
        onComplete()
        // Persist the wizard selection as project base-lib associations so
        // the next run pre-checks the same sources (2.7 §5.5)。关联集加载
        // 失败时跳过：目标集只是降级勾选，回写会反向清空存量关联
        if (sourcesLoadedRef.current) void syncBaseLibAssociations(projectId, targetLibIds)
      } else {
        setGenerating(false)
      }
    } catch (err: any) {
      console.error('Generation failed:', err)
      // 启动失败必须可见（429 并发上限 / 模板已删 / 500）：静默会让用户
      // 以为已提交，反复点击（err.message 含后端 detail）
      showToast(t('project.report.wizard.generateFailed', { error: String(err?.message || err) }), 'error')
      setGenerating(false)
    }
  }, [selectedTemplate, projectId, variableValues, supplementaryNotes, sectionInternet, internetEnabled, knowledgeSources, checkedSources, userVariables, showToast, t, onComplete])

  // Entry guard per 0.4 §7.3: report generation requires LLM + embedding.
  // 依赖必须含 doStartGenerate：勾选互联网开关后 doStartGenerate 重建
  // （捕获新的 internetEnabled/sectionInternet），若此回调保留旧闭包，
  // 会把勾选前的值提交给后端（UI 显示与提交不一致）。
  // doStartGenerate 定义必须在其之前：deps 数组求值时读取该 const。
  const handleStartGenerate = useCallback(() => {
    if (!selectedTemplate || generating) return
    void guard('report', () => {
      void doStartGenerate()
    })
  }, [selectedTemplate, generating, guard, doStartGenerate])

  const toggleSource = (id: string) => {
    setCheckedSources(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSectionInternet = (sectionId: string) => {
    setSectionInternet(prev => ({ ...prev, [sectionId]: !prev[sectionId] }))
  }

  // 逐节互联网开关（仅 standard 节；post_body 不检索无开关，2.5 §前端交互）
  const renderInternetToggle = (sectionId: string) => (
    <label
      className={`${s.internetToggle} ${(!internetConfigured || !internetEnabled) ? s.internetToggleDisabled : ''}`}
      title={t('project.report.wizard.sectionInternet')}
    >
      <input
        type="checkbox"
        checked={!!sectionInternet[sectionId]}
        onChange={() => toggleSectionInternet(sectionId)}
        disabled={!internetConfigured || !internetEnabled}
      />
      <Globe size={13} />
      <span>{t('project.report.wizard.sectionInternet')}</span>
    </label>
  )

  // 单个节行（post_body 显示标签替代开关）
  const renderSectionRow = (sec: OutlineGroup['sections'][number], num: string, key: string) => (
    <div key={key} className={s.sectionItem}>
      <span className={s.sectionNum}>{num}</span>
      <span className={s.sectionName}>{sec.title}</span>
      {sec.postBody ? (
        <span className={s.postBodyTag}>{t('project.report.wizard.postBody')}</span>
      ) : (
        renderInternetToggle(sec.section_id)
      )}
    </div>
  )

  // 报告级总开关：关闭时清空章节开关（避免 OFF 状态下残留逐节配置误导）
  const toggleMasterInternet = () => {
    setInternetEnabled(prev => {
      const next = !prev
      if (!next) setSectionInternet({})
      return next
    })
  }

  // Filter templates by search
  const filteredTemplates = searchQuery
    ? templates.filter(tpl => tpl.template_name.includes(searchQuery))
    : templates

  // Group templates by stage
  const stageGroups = (() => {
    const groups: Record<string, BackendTemplate[]> = {}
    // For general type, no stage grouping — all in one group
    if (projectType === 'general') {
      groups['all'] = filteredTemplates
    } else {
      // Group by stage
      const stageLabels = STAGE_MAP[projectType] || []
      for (const s of stageLabels) {
        const matching = filteredTemplates.filter(tpl => tpl.stage === s.key)
        if (matching.length > 0) {
          groups[s.key] = matching
        }
      }
      // Templates with null stage go to "other"
      const otherTemplates = filteredTemplates.filter(tpl => !tpl.stage)
      if (otherTemplates.length > 0) {
        groups['other'] = otherTemplates
      }
    }
    return groups
  })()

  const getStageLabel = (stageKey: string) => {
    if (stageKey === 'all') return t('project.report.wizard.customTemplates')
    if (stageKey === 'other') return t('project.stage.other')
    const stageLabels = STAGE_MAP[projectType] || []
    const found = stageLabels.find(s => s.key === stageKey)
    return found ? t(found.labelKey) : stageKey
  }

  return (
    <div className={s.wizard}>
      {/* Header */}
      <div className={s.header}>
        <button className={s.backBtn} onClick={step === 1 ? onCancel : () => setStep(step - 1)}>
          <ArrowLeft size={16} />
          {t('project.report.wizard.back')}
        </button>
        <span className={s.stepIndicator}>{t('project.report.wizard.newReport')} {step}/2</span>
      </div>

      {/* Step 1: Select template */}
      {step === 1 && (
        <div className={s.stepContent}>
          <div className={s.searchRow}>
            <Search size={16} className={s.searchIcon} />
            <input
              className={s.searchInput}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('project.report.wizard.searchTemplate')}
            />
          </div>

          {loading ? (
            <div className={s.emptyState}>
              <Loader2 size={24} className={s.spinIcon} />
              <span>{t('project.report.wizard.loadingTemplates')}</span>
            </div>
          ) : Object.keys(stageGroups).length === 0 ? (
            <div className={s.emptyState}>
              <span>{t('project.report.wizard.noTemplates')}</span>
            </div>
          ) : (
            Object.entries(stageGroups).map(([stageKey, tpls]) => (
              <div key={stageKey} className={s.categoryGroup}>
                <div className={s.categoryTitle}>{getStageLabel(stageKey)}</div>
                <div className={s.templateGrid}>
                  {tpls.map((tpl) => (
                    <div key={tpl.template_id} className={s.templateCard} onClick={() => handleSelectTemplate(tpl)}>
                      <div className={s.templateName}>{tpl.template_name}</div>
                      <div className={s.templateMeta}>
                        <span>{tpl.chapter_count} {t('project.report.chapters')}</span>
                        <span>{tpl.output_format}</span>
                        {tpl.is_custom && <span className={s.customTag}>custom</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Step 2: Variables + outline + knowledge sources */}
      {step === 2 && selectedTemplate && (
        <div className={s.stepContent}>
          <div className={s.stepTwoCols}>
            {/* Left panel: configuration */}
            <div className={s.leftPanel}>
              <div className={s.stepRow}>
                <span className={s.stepLabel}>{t('project.report.wizard.template')}</span>
                <span className={s.stepValue}>{selectedTemplate.template_name}</span>
                <span className={s.stepLabel}>{t('project.report.wizard.outputFormat')}</span>
                <span className={s.stepValue}>{selectedTemplate.output_format || 'docx'}</span>
              </div>

              {/* Template variables (user-sourced only) */}
              {userVariables.length > 0 ? (
                <div className={s.section}>
                  <div className={s.sectionTitle}>{t('project.report.wizard.variables')}</div>
                  <p className={s.sectionHint}>{t('project.report.wizard.variablesPlaceholder')}</p>
                  <div className={s.variableGrid}>
                    {userVariables.map((v) => (
                      <div key={v.name} className={s.variableField}>
                        <label className={s.variableLabel}>
                          {v.label || v.name}{v.required !== false ? ' *' : ''}
                        </label>
                        <input
                          className={s.variableInput}
                          value={variableValues[v.name] || ''}
                          onChange={(e) => setVariableValues(prev => ({ ...prev, [v.name]: e.target.value }))}
                          placeholder={v.placeholder || v.label || v.name}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className={s.section}>
                  <p className={s.sectionHint}>{t('project.report.wizard.noVariables')}</p>
                </div>
              )}

              {/* Knowledge sources */}
              <div className={s.section}>
                <div className={s.sectionTitle}>{t('project.report.wizard.knowledgeSources')}</div>
                {knowledgeSources.map((ks) => (
                  <label key={ks.id} className={s.checkboxRow}>
                    <input
                      type="checkbox"
                      checked={checkedSources.has(ks.id)}
                      onChange={() => toggleSource(ks.id)}
                    />
                    <span className={s.checkboxLabel}>
                      {ks.nameKey ? t(ks.nameKey) : ks.name}
                    </span>
                    {ks.autoMatched && ks.matchType === projectType && (
                      <span className={s.matchTag}>{t('project.report.wizard.autoMatched')}</span>
                    )}
                  </label>
                ))}
              </div>

              {/* Supplementary notes */}
              <div className={s.section}>
                <div className={s.sectionTitle}>{t('project.report.wizard.supplementary')}</div>
                <textarea
                  className={s.notesInput}
                  value={supplementaryNotes}
                  onChange={(e) => setSupplementaryNotes(e.target.value)}
                  placeholder={t('project.report.wizard.supplementaryPlaceholder')}
                  rows={3}
                />
              </div>
            </div>

            {/* Right panel: outline sections + per-section internet toggle */}
            <div className={s.rightPanel}>
              <div className={s.section}>
                <div className={s.sectionTitle}>{t('project.report.wizard.outline')}</div>
                {/* 报告级互联网总开关（总闸）：网络搜索未配置时置灰 */}
                <label
                  className={`${s.internetToggle} ${s.masterInternetToggle} ${!internetConfigured ? s.internetToggleDisabled : ''}`}
                  title={t('project.report.wizard.masterInternet')}
                >
                  <input
                    type="checkbox"
                    checked={internetEnabled && internetConfigured}
                    onChange={toggleMasterInternet}
                    disabled={!internetConfigured}
                  />
                  <Globe size={13} />
                  <span>{t('project.report.wizard.masterInternet')}</span>
                </label>
                {!internetConfigured && (
                  <p className={s.sectionHint}>{t('project.report.wizard.internetDisabledHint')}</p>
                )}
                <div className={s.sectionList}>
                  {outlineGroups.length === 0 ? (
                    <div className={s.emptyState}>
                      <span>{t('project.report.wizard.noSections')}</span>
                    </div>
                  ) : (
                    outlineGroups.map((group, gi) =>
                      group.chapterTitle === null ? (
                        // 无 children 的章：章自身即节（与模板管理同层，非缩进节）
                        renderSectionRow(group.sections[0], `${gi + 1}.`, group.sections[0].section_id)
                      ) : (
                        // 有 children 的章：组头 + 缩进节行（章内编号，层次与模板管理一致）
                        <div key={`ch-${gi}`} className={s.chapterGroup}>
                          <div className={s.chapterTitle}>{group.chapterTitle}</div>
                          {group.sections.map((sec, si) =>
                            renderSectionRow(sec, `${gi + 1}.${si + 1}`, sec.section_id),
                          )}
                        </div>
                      ),
                    )
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className={s.stepActions}>
            <button className={s.cancelBtn} onClick={onCancel}>{t('project.report.wizard.cancel')}</button>
            <button className={s.generateBtn} onClick={handleStartGenerate} disabled={generating}>
              {generating ? t('project.report.wizard.generating') : t('project.report.wizard.startGenerate')}
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
