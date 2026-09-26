/** src/features/project/tabs/TemplateManagement.tsx — Template management with editor integration */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Copy, Eye, Trash2, Upload, Plus, FileText, Loader2, AlertCircle, Edit3 } from 'lucide-react'
import { TemplateEditor } from './TemplateEditor'
import s from './TemplateManagement.module.css'

interface TemplateItem {
  template_id: string
  template_name: string
  version: string
  project_types: string[]
  stage: string | null
  output_format: string
  chapter_count: number
  is_custom: boolean
}

interface FullTemplate {
  template_id: string
  template_name: string
  version: string
  project_types: string[]
  stage: string | null
  output_format: string
  role_hint?: string
  audience?: string
  report_depth?: string
  variables: string[]
  terminology?: { canonical: string; aliases?: string[] }[]
  global_requirements?: string[]
  outline: { id: string; title: string; description?: string; prompt_hint?: string; word_count?: number; must_cite?: boolean; query_hints?: string[]; data_sources?: string[]; children?: any[] }[]
  chapter_count: number
  is_custom: boolean
  [key: string]: unknown
}

/** Editor mode: null = not editing */
type EditorMode = 'new-blank' | 'new-from-sample' | 'new-from-copy' | 'edit'

interface TemplateManagementProps {
  onBack: () => void
}

export function TemplateManagement({ onBack }: TemplateManagementProps) {
  const { t } = useTranslation()
  const [templates, setTemplates] = useState<TemplateItem[]>([])
  const [loading, setLoading] = useState(true)
  const [viewingTemplate, setViewingTemplate] = useState<FullTemplate | null>(null)

  // Editor state
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null)
  const [editorInitialData, setEditorInitialData] = useState<Partial<any>>({})
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState('')

  const loadTemplates = useCallback(async () => {
    try {
      const res: any = await window.electronAPI.invoke('reports:listTemplates', {})
      setTemplates(res.templates || [])
      setLoading(false)
    } catch (err) {
      console.error('Failed to load templates:', err)
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadTemplates() }, [loadTemplates])

  const presetTemplates = templates.filter(t => !t.is_custom)
  const customTemplates = templates.filter(t => t.is_custom)

  // ── View detail ──
  const handleView = async (tpl: TemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      setViewingTemplate(res.template)
    } catch (err) {
      console.error('Failed to load template details:', err)
    }
  }

  // ── Blank create ──
  const handleCreateBlank = () => {
    setEditorInitialData({
      project_types: ['general'],
      output_format: 'Word',
      outline: [],
      terminology: [],
      global_requirements: [],
      variables: [],
    })
    setEditorMode('new-blank')
  }

  // ── Copy from preset ──
  const handleCopyAndEdit = async (tpl: TemplateItem) => {
    try {
      const newName = `${tpl.template_name} (副本)`
      const res: any = await window.electronAPI.invoke('reports:copyTemplate', {
        templateId: tpl.template_id,
        newName,
      })
      // Load the full copied template and open in editor
      const fullRes: any = await window.electronAPI.invoke('reports:getTemplate', res.template_id)
      const full = fullRes.template as FullTemplate
      setEditorInitialData(full)
      setEditorMode('new-from-copy')
      await loadTemplates()
    } catch (err) {
      console.error('Failed to copy template:', err)
    }
  }

  // ── Upload sample → LLM extract → editor ──
  const handleExtractAndEdit = async () => {
    try {
      const result: any = await window.electronAPI.invoke('dialog:openFile', {
        filters: [
          { name: 'Documents', extensions: ['docx', 'doc', 'pdf'] },
        ],
        properties: ['openFile'],
      })
      if (!result || result.canceled || !result.filePaths?.length) return

      const filePath = result.filePaths[0]
      setExtracting(true)
      setExtractError('')

      const res: any = await window.electronAPI.invoke('reports:extractSample', { filePath })
      setExtracting(false)

      // Open editor with LLM extraction result
      const template = res.template
      setEditorInitialData(template)
      setEditorMode('new-from-sample')
    } catch (err: any) {
      setExtractError(err.message || 'Unknown error')
      setExtracting(false)
    }
  }

  // ── Edit existing custom template ──
  const handleEditCustom = async (tpl: TemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      const full = res.template as FullTemplate
      setEditorInitialData(full)
      setEditorMode('edit')
    } catch (err) {
      console.error('Failed to load template for editing:', err)
    }
  }

  // ── Delete custom template ──
  const handleDeleteCustom = async (tpl: TemplateItem) => {
    if (!confirm(t('project.template.confirmDelete'))) return
    try {
      await window.electronAPI.invoke('reports:deleteTemplate', tpl.template_id)
      await loadTemplates()
    } catch (err) {
      console.error('Failed to delete template:', err)
    }
  }

  // ── Editor save ──
  const handleEditorSave = async (templateData: any) => {
    if (editorMode === 'edit') {
      // Update existing
      await window.electronAPI.invoke('reports:updateTemplate', {
        templateId: templateData.template_id,
        templateJson: templateData,
      })
    } else {
      // Create new (from blank, copy, or sample)
      await window.electronAPI.invoke('reports:saveTemplate', {
        templateJson: templateData,
      })
    }
    await loadTemplates()
    setEditorMode(null)
    setEditorInitialData({})
  }

  // ── Editor cancel ──
  const handleEditorCancel = () => {
    setEditorMode(null)
    setEditorInitialData({})
  }

  const getStageLabel = (stage: string | null) => {
    if (!stage) return t('project.stage.other')
    const key = stage === 'before' ? 'project.stage.before'
      : stage === 'during' ? 'project.stage.during'
      : stage === 'after' ? 'project.stage.after'
      : 'project.stage.other'
    return t(key)
  }

  // ── Editor view ──
  if (editorMode) {
    return (
      <TemplateEditor
        initialData={editorInitialData}
        isNew={editorMode !== 'edit'}
        onSave={handleEditorSave}
        onCancel={handleEditorCancel}
      />
    )
  }

  return (
    <div className={s.container}>
      {/* Header */}
      <div className={s.header}>
        <button className={s.backBtn} onClick={onBack}>
          <ArrowLeft size={16} />
          {t('project.template.back')}
        </button>
        <h3 className={s.title}>{t('project.template.management')}</h3>
      </div>

      {viewingTemplate ? (
        /* Template detail view */
        <div className={s.detailView}>
          <div className={s.detailHeader}>
            <button className={s.backBtn} onClick={() => setViewingTemplate(null)}>
              <ArrowLeft size={14} />
              {t('project.template.backToList')}
            </button>
            <h4>{viewingTemplate.template_name}</h4>
          </div>
          <div className={s.detailContent}>
            <p className={s.detailMeta}>
              {t('project.template.stageLabel')}: {getStageLabel(viewingTemplate.stage)} | {viewingTemplate.chapter_count} {t('project.report.chapters')} | {viewingTemplate.output_format}
              {viewingTemplate.is_custom && ` | Custom`}
            </p>
            <div className={s.chapterPreview}>
              <p className={s.detailSectionTitle}>{t('project.template.chapterStructure')}:</p>
              {(viewingTemplate.outline || []).map((ch: any) => (
                <div key={ch.id} className={s.mockChapter}>
                  <span>{ch.title}</span>
                  {ch.children && ch.children.length > 0 && (
                    <div className={s.subChapters}>
                      {ch.children.map((sub: any) => (
                        <span key={sub.id} className={s.subChapter}>
                          └ {sub.title}
                          {sub.data_sources?.includes('internet') && (
                            <span className={s.internetTag} title={t('project.template.editor.dataSourceInternet')}> 🌐</span>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {(!viewingTemplate.outline || viewingTemplate.outline.length === 0) && (
                <p className={s.emptyChapters}>{t('project.template.noChapters')}</p>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className={s.content}>
          {/* Preset templates */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.presetTemplates')}</div>
            <p className={s.sectionHint}>{t('project.template.presetReadOnly')}</p>
            {loading ? (
              <div className={s.loadingState}>
                <Loader2 size={20} className={s.spinIcon} />
              </div>
            ) : (
              <div className={s.templateList}>
                {presetTemplates.map((tpl) => (
                  <div key={tpl.template_id} className={s.templateRow}>
                    <FileText size={16} className={s.rowIcon} />
                    <div className={s.rowInfo}>
                      <span className={s.rowName}>{tpl.template_name}</span>
                      <span className={s.rowMeta}>
                        {getStageLabel(tpl.stage)} | {tpl.chapter_count} {t('project.report.chapters')} | {tpl.output_format}
                      </span>
                    </div>
                    <div className={s.rowActions}>
                      <button className={s.actionBtn} onClick={() => handleView(tpl)} title={t('project.template.view')}>
                        <Eye size={14} />
                      </button>
                      <button className={s.actionBtn} onClick={() => handleCopyAndEdit(tpl)} title={t('project.template.copyAsCustom')}>
                        <Copy size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Custom templates */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.customTemplates')}</div>
            <div className={s.templateList}>
              {customTemplates.length === 0 ? (
                <div className={s.emptyCustom}>{t('project.template.noCustom')}</div>
              ) : (
                customTemplates.map((tpl) => (
                  <div key={tpl.template_id} className={s.templateRow}>
                    <FileText size={16} className={s.rowIcon} />
                    <div className={s.rowInfo}>
                      <span className={s.rowName}>{tpl.template_name}</span>
                      <span className={s.rowMeta}>
                        {getStageLabel(tpl.stage)} | {tpl.chapter_count} {t('project.report.chapters')} | {tpl.output_format}
                      </span>
                    </div>
                    <div className={s.rowActions}>
                      <button className={s.actionBtn} onClick={() => handleView(tpl)} title={t('project.template.view')}>
                        <Eye size={14} />
                      </button>
                      <button className={s.actionBtn} onClick={() => handleEditCustom(tpl)} title={t('project.template.edit')}>
                        <Edit3 size={14} />
                      </button>
                      <button className={s.actionBtnDanger} onClick={() => handleDeleteCustom(tpl)} title={t('project.template.delete')}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
            <div className={s.customActions}>
              <button className={s.createBtn} onClick={handleCreateBlank}>
                <Plus size={14} />
                {t('project.template.createCustom')}
              </button>
              <button className={s.importBtn} onClick={handleExtractAndEdit} disabled={extracting}>
                {extracting ? <Loader2 size={14} className={s.spinIcon} /> : <Upload size={14} />}
                {t('project.template.extractFromSample')}
              </button>
            </div>
            {extractError && (
              <div className={s.errorMsg}>
                <AlertCircle size={14} />
                {t('project.template.extractFailed', { error: extractError })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
