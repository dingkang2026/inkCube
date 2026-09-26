/** src/features/project/tabs/ReportWizard.tsx — 3-step report creation wizard (real templates) */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Search, ChevronRight, CheckCircle, Clock, Circle, GripVertical, Trash2, Plus, Loader2, Globe } from 'lucide-react'
import type { ProjectType, ProjectStage } from '../types'
import { STAGE_MAP } from '../types'
import s from './ReportWizard.module.css'

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
  variables: string[]
  chapter_count: number
  is_custom: boolean
}

/** Full template with outline (from getTemplate) */
interface FullTemplate extends BackendTemplate {
  outline: OutlineNode[]
  global_requirements?: string[]
  terminology?: { canonical: string; aliases?: string[] }[]
  [key: string]: unknown
}

interface OutlineNode {
  id: string
  title: string
  description?: string
  compose_mode?: string
  word_count?: number
  prompt_hint?: string
  children?: OutlineNode[]
}

/** Static knowledge source entries (project KB + preset libs) */
const STATIC_SOURCE_ENTRIES = [
  { id: 'ks-project', nameKey: 'project.report.wizard.projectKb', alwaysChecked: true, autoMatched: false },
  { id: 'ks-recruitment', nameKey: 'project.report.wizard.recruitmentLib', alwaysChecked: false, autoMatched: true, matchType: 'recruitment' as ProjectType, baseLibId: 'bl-recruitment' },
  { id: 'ks-investment', nameKey: 'project.report.wizard.investmentLib', alwaysChecked: false, autoMatched: true, matchType: 'investment' as ProjectType, baseLibId: 'bl-investment' },
]

interface ReportWizardProps {
  projectId: string
  projectType: ProjectType
  currentStage: ProjectStage
  onComplete: () => void
  onCancel: () => void
}

export function ReportWizard({ projectId, projectType, currentStage: _currentStage, onComplete, onCancel }: ReportWizardProps) {
  const { t } = useTranslation()
  const [step, setStep] = useState(1)
  const [searchQuery, setSearchQuery] = useState('')
  const [templates, setTemplates] = useState<BackendTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedTemplate, setSelectedTemplate] = useState<FullTemplate | null>(null)
  const [chapters, setChapters] = useState<string[]>([])
  const [checkedSources, setCheckedSources] = useState<Set<string>>(new Set())
  const [customBaseLibs, setCustomBaseLibs] = useState<{ id: string; name: string }[]>([])
  const [supplementaryNotes, setSupplementaryNotes] = useState('')
  const [outputFormat, setOutputFormat] = useState<'Word' | 'PDF'>('Word')
  const [variableValues, setVariableValues] = useState<Record<string, string>>({})

  // Step 3 state
  const [reportId, setReportId] = useState<string | null>(null)
  const [generatedChapters, setGeneratedChapters] = useState<Set<number>>(new Set())
  const [currentGenerating, setCurrentGenerating] = useState(-1)
  const [previewContent, setPreviewContent] = useState('')
  const [isComplete, setIsComplete] = useState(false)
  const [isError, setIsError] = useState(false)

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

  // Initialize knowledge sources based on project type
  useEffect(() => {
    const initial = new Set<string>()
    initial.add('ks-project')
    STATIC_SOURCE_ENTRIES.forEach(ks => {
      if (ks.autoMatched && ks.matchType === projectType) {
        initial.add(ks.id)
      }
    })
    setCheckedSources(initial)
  }, [projectType])

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
    // Add internet search option
    sources.push({
      id: 'ks-internet',
      nameKey: 'project.report.wizard.internetSearch',
      alwaysChecked: false,
      autoMatched: false,
    })
    return sources
  }, [customBaseLibs])

  const handleSelectTemplate = useCallback(async (tpl: BackendTemplate) => {
    // Fetch full template with outline
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      const full = res.template as FullTemplate
      setSelectedTemplate(full)

      // Extract chapter titles from outline
      const titles = (full.outline || []).map(ch => ch.title)
      setChapters(titles)

      // Set output format
      setOutputFormat((full.output_format as 'Word' | 'PDF') || 'Word')

      // Initialize variable values
      const vars: Record<string, string> = {}
      ;(full.variables || []).forEach(v => { vars[v] = '' })
      setVariableValues(vars)

      setStep(2)
    } catch (err) {
      console.error('Failed to load template details:', err)
    }
  }, [])

  const handleStartGenerate = useCallback(async () => {
    if (!selectedTemplate) return

    setStep(3)
    setGeneratedChapters(new Set())
    setCurrentGenerating(0)
    setPreviewContent('')
    setIsComplete(false)
    setIsError(false)

    // Call the real generation API
    try {
      // Map checked sources → base_project_ids for backend
      const baseProjectIds: string[] = []
      for (const ks of knowledgeSources) {
        if (!checkedSources.has(ks.id)) continue
        if (ks.id === 'ks-project' || ks.id === 'ks-internet') continue
        if (ks.baseLibId) {
          baseProjectIds.push(ks.baseLibId)
        }
      }

      const result: any = await window.electronAPI.invoke('reports:generateReport', {
        projectId,
        templateId: selectedTemplate.template_id,
        title: selectedTemplate.template_name,
        variables: variableValues,
        baseProjectIds,
        outputFormat,
        supplementaryNotes,
      })

      if (result.report_id) {
        // Generation started, store report_id and start polling
        setReportId(result.report_id)
        setPreviewContent(t('project.report.wizard.generationStarted'))
      } else {
        setPreviewContent(`Error: ${result.error || 'Unknown error'}`)
        setIsError(true)
      }
    } catch (err) {
      console.error('Generation failed:', err)
      setPreviewContent(`Error: ${String(err)}`)
      setIsError(true)
    }
  }, [selectedTemplate, projectId, variableValues, outputFormat, supplementaryNotes, knowledgeSources, checkedSources, t])

  // Poll for report generation progress
  useEffect(() => {
    if (step !== 3 || isComplete || isError || !reportId) return

    let cancelled = false
    let lastProgress = -1

    const pollProgress = async () => {
      try {
        const result: any = await window.electronAPI.invoke('reports:getReport', reportId)
        if (cancelled) return

        const report = result.report
        if (!report) return

        const progress = report.progress || 0
        const status = report.status

        // Update progress UI
        if (progress !== lastProgress) {
          lastProgress = progress
          const completedCount = Math.floor((progress / 100) * chapters.length)
          setGeneratedChapters(new Set(Array.from({ length: completedCount }, (_, i) => i)))
          setCurrentGenerating(completedCount < chapters.length ? completedCount : -1)
        }

        if (status === 'done') {
          setGeneratedChapters(new Set(chapters.map((_, i) => i)))
          setCurrentGenerating(-1)
          setIsComplete(true)
          setPreviewContent(t('project.report.wizard.generationComplete'))
        } else if (status === 'error') {
          setIsError(true)
          setPreviewContent(t('project.report.wizard.generationFailed'))
        }
      } catch (err) {
        console.error('Poll progress failed:', err)
      }
    }

    // Initial poll
    pollProgress()

    // Poll every 2 seconds
    const interval = setInterval(pollProgress, 2000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [step, isComplete, isError, reportId, chapters, t])

  const handleFinish = useCallback(() => {
    onComplete()
  }, [onComplete])

  const toggleSource = (id: string) => {
    setCheckedSources(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const removeChapter = (idx: number) => {
    setChapters(prev => prev.filter((_, i) => i !== idx))
  }

  const addChapter = () => {
    setChapters(prev => [...prev, t('project.report.wizard.newChapter')])
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

  const progress = chapters.length > 0 ? Math.round((generatedChapters.size / chapters.length) * 100) : 0

  return (
    <div className={s.wizard}>
      {/* Header */}
      <div className={s.header}>
        <button className={s.backBtn} onClick={step === 1 ? onCancel : () => setStep(step - 1)}>
          <ArrowLeft size={16} />
          {t('project.report.wizard.back')}
        </button>
        <span className={s.stepIndicator}>{t('project.report.wizard.newReport')} {step}/3</span>
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
          <div className={s.stepRow}>
            <span className={s.stepLabel}>{t('project.report.wizard.template')}</span>
            <span className={s.stepValue}>{selectedTemplate.template_name}</span>
            <span className={s.stepLabel}>{t('project.report.wizard.outputFormat')}</span>
            <select className={s.formatSelect} value={outputFormat} onChange={(e) => setOutputFormat(e.target.value as 'Word' | 'PDF')}>
              <option value="Word">Word</option>
              <option value="PDF">PDF</option>
            </select>
          </div>

          {/* Template variables */}
          {selectedTemplate.variables && selectedTemplate.variables.length > 0 ? (
            <div className={s.section}>
              <div className={s.sectionTitle}>{t('project.report.wizard.variables')}</div>
              <p className={s.sectionHint}>{t('project.report.wizard.variablesPlaceholder')}</p>
              <div className={s.variableGrid}>
                {selectedTemplate.variables.map((v) => (
                  <div key={v} className={s.variableField}>
                    <label className={s.variableLabel}>{v}</label>
                    <input
                      className={s.variableInput}
                      value={variableValues[v] || ''}
                      onChange={(e) => setVariableValues(prev => ({ ...prev, [v]: e.target.value }))}
                      placeholder={v}
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
                    : ks.id === 'ks-internet'
                      ? <><Globe size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />{t(ks.nameKey)}</>
                      : t(ks.nameKey)}
                </span>
                {ks.alwaysChecked && <span className={s.autoTag}>{t('project.report.wizard.autoIncluded')}</span>}
                {ks.autoMatched && ks.matchType === projectType && !ks.alwaysChecked && (
                  <span className={s.matchTag}>{t('project.report.wizard.autoMatched')}</span>
                )}
              </label>
            ))}
          </div>

          {/* Outline */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.report.wizard.outline')}</div>
            <div className={s.chapterList}>
              {chapters.map((ch, idx) => (
                <div key={idx} className={s.chapterItem}>
                  <GripVertical size={14} className={s.dragHandle} />
                  <span className={s.chapterNum}>{idx + 1}.</span>
                  <span className={s.chapterName}>{ch}</span>
                  <button className={s.removeBtn} onClick={() => removeChapter(idx)} title={t('project.report.wizard.removeChapter')}>
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
            <button className={s.addChapterBtn} onClick={addChapter}>
              <Plus size={14} />
              {t('project.report.wizard.addChapter')}
            </button>
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

          <div className={s.stepActions}>
            <button className={s.cancelBtn} onClick={onCancel}>{t('project.report.wizard.cancel')}</button>
            <button className={s.generateBtn} onClick={handleStartGenerate}>
              {t('project.report.wizard.startGenerate')}
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Generation progress */}
      {step === 3 && (
        <div className={s.stepContent}>
          {/* Progress */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.report.wizard.generationProgress')}</div>
            <div className={s.chapterProgress}>
              {chapters.map((ch, idx) => (
                <div key={idx} className={s.progressItem}>
                  {generatedChapters.has(idx) ? (
                    <CheckCircle size={14} className={s.checkIcon} />
                  ) : currentGenerating === idx ? (
                    <Clock size={14} className={s.clockIcon} />
                  ) : (
                    <Circle size={14} className={s.circleIcon} />
                  )}
                  <span className={currentGenerating === idx ? s.generatingText : ''}>
                    {idx + 1}. {ch}
                  </span>
                  {generatedChapters.has(idx) && <span className={s.doneTag}>{t('project.report.wizard.done')}</span>}
                  {currentGenerating === idx && <span className={s.genTag}>{t('project.report.wizard.generating')}</span>}
                </div>
              ))}
            </div>
            <div className={s.progressBar}>
              <div className={s.progressFill} style={{ width: `${progress}%` }} />
            </div>
            <div className={s.progressText}>{progress}%</div>
          </div>

          {/* Preview */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.report.wizard.realtimePreview')}</div>
            <div className={s.previewArea}>
              {previewContent ? (
                <pre className={s.previewContent}>{previewContent}</pre>
              ) : (
                <div className={s.previewEmpty}>{t('project.report.wizard.waitingGeneration')}</div>
              )}
            </div>
          </div>

          {isComplete && (
            <div className={s.stepActions}>
              <button className={s.cancelBtn} onClick={onCancel}>{t('project.report.wizard.cancel')}</button>
              <button className={s.generateBtn} onClick={handleFinish}>
                {t('project.report.wizard.finishAndReturn')}
                <CheckCircle size={16} />
              </button>
            </div>
          )}
          {!isComplete && (
            <div className={s.stepActions}>
              <button className={s.cancelBtn} onClick={onCancel}>{t('project.report.wizard.cancel')}</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
