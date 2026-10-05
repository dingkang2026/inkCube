/** src/features/project/tabs/ReportWizard.tsx — 2-step report creation wizard (real templates) */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Search, ChevronRight, Loader2, Globe } from 'lucide-react'
import type { ProjectType } from '../types'
import { STAGE_MAP } from '../types'
import s from './ReportWizard.module.css'
import { useSettings } from '../../settings/useSettings'
import { useFeatureGuard } from '../../readiness/useReadiness'

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

interface OutlineNode {
  section_id: string
  title: string
  description?: string
  compose_mode?: string
  prompt_hint?: string
  children?: OutlineNode[]
}

/** Static knowledge source entries (project KB + preset libs) */
const STATIC_SOURCE_ENTRIES = [
  { id: 'ks-project', nameKey: 'project.report.wizard.projectKb', alwaysChecked: true, autoMatched: false },
  { id: 'ks-recruitment', nameKey: 'project.report.wizard.recruitmentLib', alwaysChecked: false, autoMatched: true, matchType: 'recruitment' as ProjectType, baseLibId: 'bl-recruitment' },
  { id: 'ks-investment', nameKey: 'project.report.wizard.investmentLib', alwaysChecked: false, autoMatched: true, matchType: 'investment' as ProjectType, baseLibId: 'bl-investment' },
]

/**
 * Persist the wizard's checked sources back to project_base_libs (2.7 §5.5):
 * the selection becomes the project's association set, so the next wizard
 * run pre-checks the same sources. Auto-created links are refined by user
 * choices (unchecking an auto-linked lib unlinks it). Failures are
 * non-fatal — generation has already started; the wizard just falls back
 * to the current associations next time.
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
  const { settings } = useSettings()
  const guard = useFeatureGuard()
  const internetConfigured = Boolean(settings?.tavily_key)
  const [step, setStep] = useState(1)
  const [searchQuery, setSearchQuery] = useState('')
  const [templates, setTemplates] = useState<BackendTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedTemplate, setSelectedTemplate] = useState<FullTemplate | null>(null)
  const [checkedSources, setCheckedSources] = useState<Set<string>>(new Set())
  const [customBaseLibs, setCustomBaseLibs] = useState<{ id: string; name: string }[]>([])
  const [supplementaryNotes, setSupplementaryNotes] = useState('')
  const [variableValues, setVariableValues] = useState<Record<string, string>>({})
  const [internetEnabled, setInternetEnabled] = useState(false)
  const [sectionInternet, setSectionInternet] = useState<Record<string, boolean>>({})
  const [generating, setGenerating] = useState(false)

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

  // Load custom base libraries from backend
  useEffect(() => {
    async function loadCustomLibs() {
      try {
        const result: any = await window.electronAPI.invoke('baseLib:list')
        const libs = (result.base_libs || [])
          .filter((lib: any) => !lib.lib_type?.startsWith('preset_'))
          .map((lib: any) => ({ id: lib.id, name: lib.name }))
        setCustomBaseLibs(libs)
      } catch (err) {
        console.error('Failed to load custom base libs:', err)
      }
    }
    loadCustomLibs()
  }, [])

  // Initialize knowledge sources from the project's real base-lib
  // associations (2.7 §5.5): links may have been auto-created at project
  // creation or edited by the user since — a projectType hard-match would
  // check the wrong boxes and never pre-check linked custom libs.
  useEffect(() => {
    let cancelled = false
    async function loadAssociations() {
      const initial = new Set<string>()
      initial.add('ks-project')
      try {
        const result: any = await window.electronAPI.invoke('project:getBaseLibs', projectId)
        const linkedIds = new Set<string>(
          (result?.base_libs || []).map((lib: any) => lib.id),
        )
        STATIC_SOURCE_ENTRIES.forEach(ks => {
          if (ks.baseLibId && linkedIds.has(ks.baseLibId)) {
            initial.add(ks.id)
          }
        })
        customBaseLibs.forEach(lib => {
          if (linkedIds.has(lib.id)) initial.add(`ks-custom-${lib.id}`)
        })
      } catch (err) {
        console.error('Failed to load project base-lib associations:', err)
        // Fallback: only the project KB stays checked
      }
      if (!cancelled) setCheckedSources(initial)
    }
    loadAssociations()
    return () => { cancelled = true }
  }, [projectId, customBaseLibs])

  // Build dynamic knowledge sources list
  const knowledgeSources = useMemo(() => {
    const sources: { id: string; nameKey: string; alwaysChecked: boolean; autoMatched: boolean; matchType?: ProjectType; baseLibId?: string; isCustom?: boolean }[] = [
      ...STATIC_SOURCE_ENTRIES,
    ]
    // Add custom base libs
    for (const lib of customBaseLibs) {
      sources.push({
        id: `ks-custom-${lib.id}`,
        nameKey: '',  // will use lib.name directly
        alwaysChecked: false,
        autoMatched: false,
        baseLibId: lib.id,
        isCustom: true,
      })
    }
    return sources
  }, [customBaseLibs])

  // User-sourced variables that require manual input in the wizard
  const userVariables = useMemo<TemplateVariable[]>(
    () => (selectedTemplate?.variables || []).filter(v => v.source === 'user'),
    [selectedTemplate],
  )

  // Flatten outline → executable standard sections (mirrors backend get_execution_sections:
  // a chapter's children are its sections; a childless chapter is itself a section).
  // post_body sections don't retrieve, so the internet toggle only applies to standard ones.
  const standardSections = useMemo<{ section_id: string; title: string }[]>(() => {
    const out: { section_id: string; title: string }[] = []
    for (const ch of selectedTemplate?.outline || []) {
      const children = ch.children || []
      const nodes = children.length > 0 ? children : [ch]
      for (const n of nodes) {
        if ((n.compose_mode || 'standard') === 'standard') {
          out.push({ section_id: n.section_id, title: n.title })
        }
      }
    }
    return out
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

  // Entry guard per 0.4 §7.3: report generation requires LLM + embedding
  const handleStartGenerate = useCallback(() => {
    if (!selectedTemplate || generating) return
    void guard('report', () => {
      void doStartGenerate()
    })
  }, [selectedTemplate, generating, guard])

  const doStartGenerate = useCallback(async () => {
    if (!selectedTemplate) return
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

    try {
      const result: any = await window.electronAPI.invoke('reports:generateReport', {
        projectId,
        templateId: selectedTemplate.template_id,
        title: selectedTemplate.template_name,
        variables: variableValues,
        baseProjectIds,
        supplementaryNotes,
        sectionInternet,
        internetEnabled,
      })

      if (result.report_id) {
        // Generation started in background — return to kanban
        onComplete()
        // Persist the wizard selection as project base-lib associations so
        // the next run pre-checks the same sources (2.7 §5.5)
        void syncBaseLibAssociations(projectId, targetLibIds)
      } else {
        setGenerating(false)
      }
    } catch (err) {
      console.error('Generation failed:', err)
      setGenerating(false)
    }
  }, [selectedTemplate, projectId, variableValues, supplementaryNotes, sectionInternet, internetEnabled, knowledgeSources, checkedSources, onComplete])

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
                      disabled={ks.alwaysChecked}
                    />
                    <span className={s.checkboxLabel}>
                      {ks.isCustom
                        ? customBaseLibs.find(lib => `ks-custom-${lib.id}` === ks.id)?.name || ks.id
                        : t(ks.nameKey)}
                    </span>
                    {ks.alwaysChecked && <span className={s.autoTag}>{t('project.report.wizard.autoIncluded')}</span>}
                    {ks.autoMatched && ks.matchType === projectType && !ks.alwaysChecked && (
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
                {/* 报告级互联网总开关（总闸）：未配置 Tavily key 时置灰 */}
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
                  {standardSections.length === 0 ? (
                    <div className={s.emptyState}>
                      <span>{t('project.report.wizard.noSections')}</span>
                    </div>
                  ) : (
                    standardSections.map((sec, idx) => (
                      <div key={sec.section_id} className={s.sectionItem}>
                        <span className={s.sectionNum}>{idx + 1}.</span>
                        <span className={s.sectionName}>{sec.title}</span>
                        <label
                          className={`${s.internetToggle} ${(!internetConfigured || !internetEnabled) ? s.internetToggleDisabled : ''}`}
                          title={t('project.report.wizard.sectionInternet')}
                        >
                          <input
                            type="checkbox"
                            checked={!!sectionInternet[sec.section_id]}
                            onChange={() => toggleSectionInternet(sec.section_id)}
                            disabled={!internetConfigured || !internetEnabled}
                          />
                          <Globe size={13} />
                          <span>{t('project.report.wizard.sectionInternet')}</span>
                        </label>
                      </div>
                    ))
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
