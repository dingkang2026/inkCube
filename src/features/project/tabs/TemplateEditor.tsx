/** src/features/project/tabs/TemplateEditor.tsx — Template editor (outline + metadata + global config) */
import { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft, ChevronDown, Plus, Trash2, GripVertical,
  Save, AlertCircle, Loader2, ChevronUp, X,
} from 'lucide-react'
import s from './TemplateEditor.module.css'

/** Outline node for editing */
interface OutlineNode {
  section_id: string
  title: string
  description?: string
  prompt_hint?: string
  compose_mode?: string
  children?: OutlineNode[]
}

/** Full template data for editing */
interface TemplateData {
  template_id?: string
  template_name: string
  project_types: string[]
  stage: string | null
  output_format: string
  role_hint?: string
  audience?: string
  report_depth?: string
  global_requirements: string[]
  outline: OutlineNode[]
  version?: string
}

interface TemplateEditorProps {
  /** Initial template data (from LLM extraction, copy, or blank) */
  initialData: Partial<TemplateData>
  /** Whether this is a new template (true) or editing existing (false) */
  isNew?: boolean
  /** Embedded in right panel: compact header, no back button / bottom actions */
  embedded?: boolean
  /** Called when user saves */
  onSave: (template: TemplateData) => Promise<void>
  /** Called when user cancels */
  onCancel: () => void
}

function generateId(prefix = 'ch'): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 8)}`
}

export function TemplateEditor({ initialData, isNew, embedded, onSave, onCancel }: TemplateEditorProps) {
  const { t } = useTranslation()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Template state
  const [templateName, setTemplateName] = useState(initialData.template_name || '')
  const [roleHint, setRoleHint] = useState(initialData.role_hint || '')
  const [audience, setAudience] = useState(initialData.audience || '')
  const [projectTypes, setProjectTypes] = useState<string[]>(initialData.project_types || ['general'])
  const [stage, setStage] = useState<string | null>(initialData.stage || null)
  const [outputFormat, setOutputFormat] = useState(initialData.output_format || 'docx')
  const [outline, setOutline] = useState<OutlineNode[]>(initialData.outline || [])
  const [globalRequirements, setGlobalRequirements] = useState<string[]>(initialData.global_requirements || [])

  // UI state
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set())
  const [editingSection, setEditingSection] = useState<string | null>(null)

  const toggleProjectType = (type: string) => {
    setProjectTypes(prev =>
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    )
  }

  // ── Outline operations ──

  const addChapter = () => {
    const newCh: OutlineNode = {
      section_id: generateId('ch'),
      title: t('project.template.editor.newChapter'),
      children: [],
    }
    setOutline(prev => [...prev, newCh])
    setEditingSection(newCh.section_id)
  }

  const removeChapter = (id: string) => {
    setOutline(prev => prev.filter(ch => ch.section_id !== id))
  }

  const updateChapter = (id: string, updates: Partial<OutlineNode>) => {
    setOutline(prev => prev.map(ch => ch.section_id === id ? { ...ch, ...updates } : ch))
  }

  const moveChapter = (idx: number, dir: 'up' | 'down') => {
    setOutline(prev => {
      const next = [...prev]
      const target = dir === 'up' ? idx - 1 : idx + 1
      if (target < 0 || target >= next.length) return prev
      ;[next[idx], next[target]] = [next[target], next[idx]]
      return next
    })
  }

  const addChildSection = (parentId: string) => {
    setOutline(prev => prev.map(ch => {
      if (ch.section_id !== parentId) return ch
      const newChild: OutlineNode = {
        section_id: generateId('sec'),
        title: t('project.template.editor.newSection'),
      }
      return { ...ch, children: [...(ch.children || []), newChild] }
    }))
  }

  const removeChildSection = (parentId: string, childId: string) => {
    setOutline(prev => prev.map(ch => {
      if (ch.section_id !== parentId) return ch
      return { ...ch, children: (ch.children || []).filter(c => c.section_id !== childId) }
    }))
  }

  const updateChildSection = (parentId: string, childId: string, updates: Partial<OutlineNode>) => {
    setOutline(prev => prev.map(ch => {
      if (ch.section_id !== parentId) return ch
      return {
        ...ch,
        children: (ch.children || []).map(c => c.section_id === childId ? { ...c, ...updates } : c),
      }
    }))
  }

  const toggleExpand = (id: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  // ── List operations (global_requirements) ──

  const addListItem = (list: string[], setList: (v: string[]) => void) => {
    setList([...list, ''])
  }

  const removeListItem = (list: string[], setList: (v: string[]) => void, idx: number) => {
    setList(list.filter((_, i) => i !== idx))
  }

  const updateListItem = (list: string[], setList: (v: string[]) => void, idx: number, value: string) => {
    setList(list.map((item, i) => i === idx ? value : item))
  }

  // ── Save ──

  const handleSave = useCallback(async () => {
    if (!templateName.trim()) {
      setError(t('project.template.editor.nameRequired'))
      return
    }
    setSaving(true)
    setError('')
    try {
      const template: TemplateData = {
        template_id: initialData.template_id,
        template_name: templateName.trim(),
        project_types: projectTypes,
        stage,
        output_format: outputFormat,
        role_hint: roleHint.trim(),
        audience: audience.trim(),
        report_depth: outline.length >= 5 ? 'full' : 'brief',
        global_requirements: globalRequirements.filter(r => r.trim()),
        outline,
      }
      await onSave(template)
    } catch (err: any) {
      setError(err.message || String(err))
    } finally {
      setSaving(false)
    }
  }, [templateName, projectTypes, stage, outputFormat, roleHint, audience, outline, globalRequirements, initialData.template_id, onSave, t])

  return (
    <div className={embedded ? `${s.container} ${s.embedded}` : s.container}>
      {/* Header */}
      <div className={s.header}>
        {!embedded && (
          <button className={s.backBtn} onClick={onCancel}>
            <ArrowLeft size={16} />
            {t('project.template.editor.back')}
          </button>
        )}
        <h3 className={s.title}>
          {isNew ? t('project.template.editor.newTemplate') : t('project.template.editor.editTemplate')}
        </h3>
        <button className={s.saveBtn} onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 size={14} className={s.spinIcon} /> : <Save size={14} />}
          {t('project.template.editor.save')}
        </button>
      </div>

      {error && (
        <div className={s.errorBar}>
          <AlertCircle size={14} />
          <span>{error}</span>
          <button className={s.errorClose} onClick={() => setError('')}><X size={12} /></button>
        </div>
      )}

      {/* Scrollable content area */}
      <div className={s.contentArea}>
        {/* Basic Info */}
        <div className={s.section}>
        <div className={s.sectionTitle}>{t('project.template.editor.basicInfo')}</div>
        <div className={s.fieldGrid}>
          <div className={s.field}>
            <label>{t('project.template.editor.templateName')}</label>
            <input value={templateName} onChange={e => setTemplateName(e.target.value)} placeholder={t('project.template.editor.templateNamePlaceholder')} />
          </div>
          <div className={s.field}>
            <label>{t('project.template.editor.roleHint')}</label>
            <input value={roleHint} onChange={e => setRoleHint(e.target.value)} placeholder={t('project.template.editor.roleHintPlaceholder')} />
          </div>
          <div className={s.field}>
            <label>{t('project.template.editor.audience')}</label>
            <input value={audience} onChange={e => setAudience(e.target.value)} placeholder={t('project.template.editor.audiencePlaceholder')} />
          </div>
          <div className={s.field}>
            <label>{t('project.template.editor.outputFormat')}</label>
            <select value={outputFormat} onChange={e => setOutputFormat(e.target.value)}>
              <option value="docx">Word (.docx)</option>
            </select>
          </div>
        </div>
        <div className={s.fieldRow}>
          <label>{t('project.template.editor.projectTypes')}</label>
          <div className={s.checkboxGroup}>
            <label><input type="checkbox" checked={projectTypes.includes('recruitment')} onChange={() => toggleProjectType('recruitment')} /> {t('project.type.recruitment')}</label>
            <label><input type="checkbox" checked={projectTypes.includes('investment')} onChange={() => toggleProjectType('investment')} /> {t('project.type.investment')}</label>
            <label><input type="checkbox" checked={projectTypes.includes('general')} onChange={() => toggleProjectType('general')} /> {t('project.type.general')}</label>
          </div>
        </div>
        <div className={s.fieldRow}>
          <label>{t('project.template.editor.stage')}</label>
          <select value={stage || ''} onChange={e => setStage(e.target.value || null)}>
            <option value="">{t('project.template.editor.stageNone')}</option>
            <option value="before">{t('project.stage.before')}</option>
            <option value="during">{t('project.stage.during')}</option>
            <option value="after">{t('project.stage.after')}</option>
          </select>
        </div>
      </div>

      {/* Outline */}
      <div className={s.section}>
        <div className={s.sectionTitle}>{t('project.template.editor.outline')}</div>
        {outline.map((ch, idx) => (
          <div key={ch.section_id} className={s.chapterItem}>
            <div className={s.chapterHeader}>
              <GripVertical size={14} className={s.dragHandle} />
              <span className={s.chapterNum}>{idx + 1}.</span>
              <input
                className={s.chapterTitleInput}
                value={ch.title}
                onChange={e => updateChapter(ch.section_id, { title: e.target.value })}
              />
              <div className={s.chapterActions}>
                <button className={s.iconBtn} onClick={() => toggleExpand(ch.section_id)} title={t('project.template.editor.toggleExpand')}>
                  {expandedSections.has(ch.section_id) ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </button>
                <button className={s.iconBtn} onClick={() => moveChapter(idx, 'up')} disabled={idx === 0}>
                  <ChevronUp size={14} />
                </button>
                <button className={s.iconBtn} onClick={() => moveChapter(idx, 'down')} disabled={idx === outline.length - 1}>
                  <ChevronDown size={14} />
                </button>
                <button className={s.iconBtn} onClick={() => addChildSection(ch.section_id)} title={t('project.template.editor.addSubSection')}>
                  <Plus size={14} />
                </button>
                <button className={s.dangerBtn} onClick={() => removeChapter(ch.section_id)}>
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
            {expandedSections.has(ch.section_id) && ch.children && ch.children.length > 0 && (
              <div className={s.childList}>
                {ch.children.map((sec) => (
                  <div key={sec.section_id} className={s.childItem}>
                    <span className={s.childTitle} onClick={() => setEditingSection(editingSection === sec.section_id ? null : sec.section_id)}>
                      {sec.title}
                    </span>
                    <div className={s.childActions}>
                      <button className={s.dangerBtnSmall} onClick={() => removeChildSection(ch.section_id, sec.section_id)}>
                        <Trash2 size={11} />
                      </button>
                    </div>
                    {editingSection === sec.section_id && (
                      <div className={s.sectionEditor}>
                        <div className={s.field}>
                          <label>{t('project.template.editor.sectionTitle')}</label>
                          <input value={sec.title} onChange={e => updateChildSection(ch.section_id, sec.section_id, { title: e.target.value })} />
                        </div>
                        <div className={s.field}>
                          <label>{t('project.template.editor.description')}</label>
                          <textarea value={sec.description || ''} onChange={e => updateChildSection(ch.section_id, sec.section_id, { description: e.target.value })} rows={2} />
                        </div>
                        <div className={s.field}>
                          <label>{t('project.template.editor.promptHint')}</label>
                          <textarea value={sec.prompt_hint || ''} onChange={e => updateChildSection(ch.section_id, sec.section_id, { prompt_hint: e.target.value })} rows={2} />
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        <button className={s.addBtn} onClick={addChapter}>
          <Plus size={14} />
          {t('project.template.editor.addChapter')}
        </button>
      </div>

      {/* Global Config */}
      <div className={s.section}>
        <div className={s.sectionTitle}>{t('project.template.editor.globalConfig')}</div>

        {/* Global Requirements */}
        <div className={s.subSection}>
          <label className={s.subTitle}>{t('project.template.editor.globalRequirements')}</label>
          {globalRequirements.map((req, idx) => (
            <div key={idx} className={s.listRow}>
              <input value={req} onChange={e => updateListItem(globalRequirements, setGlobalRequirements, idx, e.target.value)} placeholder={t('project.template.editor.requirementPlaceholder')} />
              <button className={s.dangerBtnSmall} onClick={() => removeListItem(globalRequirements, setGlobalRequirements, idx)}><Trash2 size={11} /></button>
            </div>
          ))}
          <button className={s.addBtnSmall} onClick={() => addListItem(globalRequirements, setGlobalRequirements)}><Plus size={12} /> {t('project.template.editor.addRequirement')}</button>
        </div>
      </div>
      </div>

      {/* Bottom actions (fixed, page mode only) */}
      {!embedded && (
        <div className={s.bottomActions}>
          <button className={s.cancelBtn} onClick={onCancel}>{t('project.template.editor.cancel')}</button>
          <button className={s.saveBtn} onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 size={14} className={s.spinIcon} /> : <Save size={14} />}
            {t('project.template.editor.save')}
          </button>
        </div>
      )}
    </div>
  )
}
