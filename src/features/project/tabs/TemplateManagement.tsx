/** src/features/project/tabs/TemplateManagement.tsx — Template management with table layout and detail drawer */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Copy, Eye, Trash2, Upload, Plus, FileText, Loader2, AlertCircle, Edit3, Search } from 'lucide-react'
import { useToast } from '../../knowledge/Toast'
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
  global_requirements?: string[]
  outline: { section_id: string; title: string; description?: string; prompt_hint?: string; children?: any[] }[]
  chapter_count: number
  is_custom: boolean
  [key: string]: unknown
}

/** Editor mode: null = not editing */
type EditorMode = 'new-blank' | 'new-from-sample' | 'new-from-copy' | 'edit'

interface TemplateManagementProps {
  onBack: () => void
  projectType?: string  // Optional: global mode doesn't filter by project type
}

export function TemplateManagement({ onBack, projectType }: TemplateManagementProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [templates, setTemplates] = useState<TemplateItem[]>([])
  const [viewingTemplate, setViewingTemplate] = useState<FullTemplate | null>(null)

  // Filter state
  const [searchQuery, setSearchQuery] = useState('')
  const [filterType, setFilterType] = useState<string>('all')
  const [filterStage, setFilterStage] = useState<string>('all')

  // Pagination state (separate for preset and custom)
  const [presetPage, setPresetPage] = useState(1)
  const [customPage, setCustomPage] = useState(1)
  const pageSize = 10

  // Right panel state: 'detail' | 'edit' | null
  const [rightPanelMode, setRightPanelMode] = useState<'detail' | 'edit' | null>(null)

  // Editor state
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null)
  const [editorInitialData, setEditorInitialData] = useState<Partial<any>>({})
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState('')

  const loadTemplates = useCallback(async () => {
    try {
      const res: any = await window.electronAPI.invoke('reports:listTemplates', {
        projectType,
      })
      setTemplates(res.templates || [])
    } catch (err) {
      console.error('Failed to load templates:', err)
    }
  }, [projectType])

  useEffect(() => { loadTemplates() }, [loadTemplates])

  // Reset pagination when filters change to avoid staying on an out-of-range page
  useEffect(() => {
    setPresetPage(1)
    setCustomPage(1)
  }, [searchQuery, filterType, filterStage])

  const presetTemplates = templates.filter(t => !t.is_custom)
  const customTemplates = templates.filter(t => t.is_custom)

  // ── Filter logic ──
  const filteredPreset = presetTemplates.filter(tpl => {
    const matchesSearch = tpl.template_name.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesType = filterType === 'all' || tpl.project_types.includes(filterType)
    const matchesStage = filterStage === 'all' || tpl.stage === filterStage
    return matchesSearch && matchesType && matchesStage
  })

  const filteredCustom = customTemplates.filter(tpl => {
    const matchesSearch = tpl.template_name.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesType = filterType === 'all' || tpl.project_types.includes(filterType)
    const matchesStage = filterStage === 'all' || tpl.stage === filterStage
    return matchesSearch && matchesType && matchesStage
  })

  // ─ Pagination ──
  const paginatedPreset = filteredPreset.slice((presetPage - 1) * pageSize, presetPage * pageSize)
  const paginatedCustom = filteredCustom.slice((customPage - 1) * pageSize, customPage * pageSize)

  const presetTotalPages = Math.ceil(filteredPreset.length / pageSize)
  const customTotalPages = Math.ceil(filteredCustom.length / pageSize)

  // ── View detail (show in right panel) ──
  const handleView = async (tpl: TemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      setViewingTemplate(res.template)
      setRightPanelMode('detail')
    } catch (err) {
      console.error('Failed to load template details:', err)
      showToast(t('project.template.viewFailed'), 'error')
    }
  }

  // ── Blank create ──
  const handleCreateBlank = () => {
    setEditorInitialData({
      project_types: ['general'],
      output_format: 'docx',
      outline: [],
      global_requirements: [],
    })
    setEditorMode('new-blank')
    setRightPanelMode('edit')
  }

  // ── Copy from preset (enter edit mode in right panel) ──
  const handleCopyAndEdit = async (tpl: TemplateItem) => {
    try {
      const newName = `${tpl.template_name} (${t('project.template.editor.copySuffix')})`
      const res: any = await window.electronAPI.invoke('reports:copyTemplate', {
        templateId: tpl.template_id,
        newName,
      })
      // Load the full copied template and open in editor
      const fullRes: any = await window.electronAPI.invoke('reports:getTemplate', res.template_id)
      const full = fullRes.template as FullTemplate
      setEditorInitialData(full)
      setEditorMode('new-from-copy')
      setRightPanelMode('edit')
      await loadTemplates()
    } catch (err) {
      console.error('Failed to copy template:', err)
      showToast(t('project.template.copyFailed') || String(err), 'error')
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
      setRightPanelMode('edit')
    } catch (err: any) {
      setExtractError(err.message || 'Unknown error')
      setExtracting(false)
    }
  }

  // ── Edit existing custom template (enter edit mode in right panel) ──
  const handleEditCustom = async (tpl: TemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      const full = res.template as FullTemplate
      setEditorInitialData(full)
      setEditorMode('edit')
      setRightPanelMode('edit')
    } catch (err) {
      console.error('Failed to load template for editing:', err)
      showToast(t('project.template.editFailed'), 'error')
    }
  }

  // ── Delete custom template ──
  const handleDeleteCustom = async (tpl: TemplateItem) => {
    if (!confirm(t('project.template.confirmDelete'))) return
    try {
      await window.electronAPI.invoke('reports:deleteTemplate', tpl.template_id)
      await loadTemplates()
      // Close right panel if the deleted template was open in it
      if (viewingTemplate?.template_id === tpl.template_id || editorInitialData.template_id === tpl.template_id) {
        setViewingTemplate(null)
        setEditorMode(null)
        setEditorInitialData({})
        setRightPanelMode(null)
      }
      showToast(t('project.template.deleteSuccess'), 'success')
    } catch (err) {
      console.error('Failed to delete template:', err)
      showToast(t('project.template.deleteFailed') || String(err), 'error')
    }
  }

  // ── Editor save ──
  const handleEditorSave = async (templateData: any) => {
    try {
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
      setViewingTemplate(null)
      setRightPanelMode(null) // Close right panel after save
      showToast(t('project.template.editor.saveSuccess'), 'success')
    } catch (err) {
      console.error('Failed to save template:', err)
      showToast(t('project.template.saveFailed') || String(err), 'error')
    }
  }

  // ── Editor cancel: existing template returns to detail, new template closes panel ──
  const handleEditorCancel = () => {
    if (editorMode === 'edit') {
      setViewingTemplate(editorInitialData as FullTemplate)
      setRightPanelMode('detail')
    } else {
      setViewingTemplate(null)
      setRightPanelMode(null)
    }
    setEditorMode(null)
    setEditorInitialData({})
  }

  // ── Close right panel (× in edit mode = cancel editing) ──
  const handleCloseRightPanel = () => {
    if (rightPanelMode === 'edit') {
      handleEditorCancel()
      return
    }
    setRightPanelMode(null)
    setViewingTemplate(null)
  }

  const getStageLabel = (stage: string | null) => {
    if (!stage) return t('project.stage.other')
    const key = stage === 'before' ? 'project.stage.before'
      : stage === 'during' ? 'project.stage.during'
      : stage === 'after' ? 'project.stage.after'
      : 'project.stage.other'
    return t(key)
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

      {/* Main layout: left list + right panel */}
      <div className={s.mainLayout}>
        {/* Left side: template list */}
        <div className={s.leftPanel}>
          {/* Filter bar */}
          <div className={s.filterBar}>
            <div className={s.searchBox}>
              <Search size={14} />
              <input
                type="text"
                placeholder={t('project.template.searchPlaceholder')}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
            <div className={s.filters}>
              <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
                <option value="all">{t('project.template.filter.allTypes')}</option>
                <option value="recruitment">{t('project.type.recruitment')}</option>
                <option value="investment">{t('project.type.investment')}</option>
                <option value="general">{t('project.type.general')}</option>
              </select>
              <select value={filterStage} onChange={(e) => setFilterStage(e.target.value)}>
                <option value="all">{t('project.template.filter.allStages')}</option>
                <option value="before">{t('project.stage.before')}</option>
                <option value="during">{t('project.stage.during')}</option>
                <option value="after">{t('project.stage.after')}</option>
                <option value="other">{t('project.stage.other')}</option>
              </select>
            </div>
          </div>

          {/* Preset templates table */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.presetTemplates')} ({filteredPreset.length})</div>
            {paginatedPreset.length === 0 ? (
              <div className={s.emptyState}>{t('project.template.noTemplates')}</div>
            ) : (
              <>
                <div className={s.table}>
                  <div className={s.tableHeader}>
                    <div className={s.colName}>{t('project.template.col.name')}</div>
                    <div className={s.colType}>{t('project.template.col.type')}</div>
                    <div className={s.colChapters}>{t('project.template.col.chapters')}</div>
                    <div className={s.colStage}>{t('project.template.col.stage')}</div>
                    <div className={s.colActions}>{t('project.template.col.actions')}</div>
                  </div>
                  {paginatedPreset.map((tpl) => (
                    <div key={tpl.template_id} className={s.tableRow} onClick={() => handleView(tpl)}>
                      <div className={s.colName}>
                        <FileText size={14} className={s.rowIcon} />
                        {tpl.template_name}
                      </div>
                      <div className={s.colType}>
                        {tpl.project_types.map(pt => (
                          <span key={pt} className={s.typeBadge} data-type={pt}>{t(`project.type.${pt}`)}</span>
                        ))}
                      </div>
                      <div className={s.colChapters}>{tpl.chapter_count} {t('project.report.chapters')}</div>
                      <div className={s.colStage}>{getStageLabel(tpl.stage)}</div>
                      <div className={s.colActions}>
                        <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleView(tpl); }} title={t('project.template.view')}>
                          <Eye size={14} />
                        </button>
                        <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleCopyAndEdit(tpl); }} title={t('project.template.copyAsCustom')}>
                          <Copy size={14} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {presetTotalPages > 1 && (
                  <div className={s.pagination}>
                    <button disabled={presetPage === 1} onClick={() => setPresetPage(p => p - 1)}>← {t('common.prev')}</button>
                    <span>{presetPage} / {presetTotalPages}</span>
                    <button disabled={presetPage === presetTotalPages} onClick={() => setPresetPage(p => p + 1)}>{t('common.next')} →</button>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Custom templates table */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.customTemplates')} ({filteredCustom.length})</div>
            {paginatedCustom.length === 0 ? (
              <div className={s.emptyState}>{t('project.template.noCustom')}</div>
            ) : (
              <>
                <div className={s.table}>
                  <div className={s.tableHeader}>
                    <div className={s.colName}>{t('project.template.col.name')}</div>
                    <div className={s.colType}>{t('project.template.col.type')}</div>
                    <div className={s.colChapters}>{t('project.template.col.chapters')}</div>
                    <div className={s.colStage}>{t('project.template.col.stage')}</div>
                    <div className={s.colActions}>{t('project.template.col.actions')}</div>
                  </div>
                  {paginatedCustom.map((tpl) => (
                    <div key={tpl.template_id} className={s.tableRow} onClick={() => handleView(tpl)}>
                      <div className={s.colName}>
                        <FileText size={14} className={s.rowIcon} />
                        {tpl.template_name}
                      </div>
                      <div className={s.colType}>
                        {tpl.project_types.map(pt => (
                          <span key={pt} className={s.typeBadge} data-type={pt}>{t(`project.type.${pt}`)}</span>
                        ))}
                      </div>
                      <div className={s.colChapters}>{tpl.chapter_count} {t('project.report.chapters')}</div>
                      <div className={s.colStage}>{getStageLabel(tpl.stage)}</div>
                      <div className={s.colActions}>
                        <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleView(tpl); }} title={t('project.template.view')}>
                          <Eye size={14} />
                        </button>
                        <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleEditCustom(tpl); }} title={t('project.template.edit')}>
                          <Edit3 size={14} />
                        </button>
                        <button className={s.actionBtnDanger} onClick={(e) => { e.stopPropagation(); handleDeleteCustom(tpl); }} title={t('project.template.delete')}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {customTotalPages > 1 && (
                  <div className={s.pagination}>
                    <button disabled={customPage === 1} onClick={() => setCustomPage(p => p - 1)}>← {t('common.prev')}</button>
                    <span>{customPage} / {customTotalPages}</span>
                    <button disabled={customPage === customTotalPages} onClick={() => setCustomPage(p => p + 1)}>{t('common.next')} →</button>
                  </div>
                )}
              </>
            )}
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

        {/* Right panel: detail or editor (inside split layout) */}
        {rightPanelMode && (
        <div className={rightPanelMode === 'edit' ? `${s.rightPanel} ${s.editMode}` : s.rightPanel}>
          <div className={s.panelHeader}>
            <h4>{rightPanelMode === 'detail' ? viewingTemplate?.template_name : (editorMode === 'edit' ? t('project.template.editor.editTemplate') : t('project.template.editor.newTemplate'))}</h4>
            <button className={s.closeBtn} onClick={handleCloseRightPanel} title={t('common.close')}>
              ×
            </button>
          </div>
          
          {rightPanelMode === 'detail' && viewingTemplate && (
            <div className={s.detailContent}>
              <p className={s.detailMeta}>
                {getStageLabel(viewingTemplate.stage)} | {viewingTemplate.chapter_count} {t('project.report.chapters')} | {viewingTemplate.output_format}
                {viewingTemplate.is_custom && ` | ${t('project.template.editor.custom')}`}
              </p>

              {/* Basic info fields */}
              <div className={s.detailFields}>
                <div className={s.detailField}>
                  <span className={s.fieldLabel}>{t('project.template.editor.roleHint')}</span>
                  <span>{viewingTemplate.role_hint || '-'}</span>
                </div>
                <div className={s.detailField}>
                  <span className={s.fieldLabel}>{t('project.template.editor.audience')}</span>
                  <span>{viewingTemplate.audience || '-'}</span>
                </div>
                <div className={s.detailField}>
                  <span className={s.fieldLabel}>{t('project.template.detail.reportDepth')}</span>
                  <span>{viewingTemplate.report_depth === 'brief' ? t('project.template.detail.depthBrief') : t('project.template.detail.depthFull')}</span>
                </div>
                <div className={s.detailField}>
                  <span className={s.fieldLabel}>{t('project.template.editor.outputFormat')}</span>
                  <span>{viewingTemplate.output_format}</span>
                </div>
              </div>

              {/* Global requirements */}
              {(viewingTemplate.global_requirements || []).length > 0 && (
                <div className={s.detailBlock}>
                  <p className={s.detailSectionTitle}>{t('project.template.editor.globalRequirements')}</p>
                  <ul className={s.reqList}>
                    {(viewingTemplate.global_requirements || []).map((req, idx) => (
                      <li key={idx}>{req}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Chapter structure with per-section description and prompt hint */}
              <div className={s.detailBlock}>
                <p className={s.detailSectionTitle}>{t('project.template.chapterStructure')}</p>
                <div className={s.chapterPreview}>
                  {(viewingTemplate.outline || []).map((ch: any) => (
                    <div key={ch.section_id} className={s.mockChapter}>
                      <span className={s.nodeTitle}>{ch.title}</span>
                      {ch.description && <p className={s.nodeDesc}>{ch.description}</p>}
                      {ch.prompt_hint && <p className={s.nodeHint}>{ch.prompt_hint}</p>}
                      {ch.children && ch.children.length > 0 && (
                        <div className={s.subChapters}>
                          {ch.children.map((sub: any) => (
                            <div key={sub.section_id} className={s.subChapter}>
                              <span className={s.nodeTitle}>└ {sub.title}</span>
                              {sub.description && <p className={s.nodeDesc}>{sub.description}</p>}
                              {sub.prompt_hint && <p className={s.nodeHint}>{sub.prompt_hint}</p>}
                            </div>
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
              
              {/* Action buttons */}
              <div className={s.detailActions}>
                {!viewingTemplate.is_custom ? (
                  <button className={s.primaryBtn} onClick={() => handleCopyAndEdit(viewingTemplate as TemplateItem)}>
                    <Copy size={14} />
                    {t('project.template.copyAsCustom')}
                  </button>
                ) : (
                  <>
                    <button className={s.primaryBtn} onClick={() => handleEditCustom(viewingTemplate as TemplateItem)}>
                      <Edit3 size={14} />
                      {t('project.template.edit')}
                    </button>
                    <button className={s.dangerBtn} onClick={() => handleDeleteCustom(viewingTemplate as TemplateItem)}>
                      <Trash2 size={14} />
                      {t('project.template.delete')}
                    </button>
                  </>
                )}
              </div>
            </div>
          )}
          
          {rightPanelMode === 'edit' && editorMode && (
            <div className={s.editorWrapper}>
              <TemplateEditor
                initialData={editorInitialData}
                isNew={editorMode !== 'edit'}
                embedded
                onSave={handleEditorSave}
                onCancel={handleEditorCancel}
              />
            </div>
          )}
        </div>
        )}
      </div>
    </div>
  )
}
