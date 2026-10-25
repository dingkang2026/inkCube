/** src/features/project/tabs/TemplateManagement.tsx — Full-width template list with right-side overlay drawer for view/edit */
import { useState, useEffect, useCallback, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, Eye, Trash2, Upload, Plus, FileText, Loader2, AlertCircle, Edit3, Search, RefreshCw } from 'lucide-react'
import { useToast } from '../../knowledge/Toast'
import { TemplateEditor } from './TemplateEditor'
import s from './TemplateManagement.module.css'
import { useConfirm } from '../../../components/ConfirmDialog'
import { FormTemplatePanel } from './FormTemplatePanel'
import { PptTemplatePanel } from './PptTemplatePanel'
import { useActivity } from '../../activity/ActivityContext'

interface TemplateItem {
  template_id: string
  template_name: string
  version: string
  project_types: string[]
  stage: string | null
  output_format: string
  role_hint?: string
  audience?: string
  report_depth?: string
  chapter_count: number
  is_custom: boolean
  updated_at?: string | null
  upgrade_needed?: boolean
  upgrade_error?: string | null
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
  variables?: any[]
  outline: any[]
  chapter_count?: number
  is_custom?: boolean
  [key: string]: unknown
}

// ── View normalization: present EVERY schema key even when empty/absent ──
function normalizeNodeForView(n: any): any {
  return {
    section_id: n.section_id ?? n.id ?? null,
    title: n.title ?? null,
    description: n.description ?? null,
    compose_mode: n.compose_mode ?? 'standard',
    prompt_hint: n.prompt_hint ?? null,
    optional: n.optional ?? false,
    position: n.position ?? null,
    children: Array.isArray(n.children) ? n.children.map(normalizeNodeForView) : [],
  }
}
function normalizeForView(tpl: any): any {
  return {
    schema_version: tpl.schema_version ?? null,
    template_id: tpl.template_id ?? null,
    template_name: tpl.template_name ?? null,
    version: tpl.version ?? null,
    project_types: tpl.project_types ?? [],
    stage: tpl.stage ?? null,
    output_format: tpl.output_format ?? 'docx',
    role_hint: tpl.role_hint ?? null,
    audience: tpl.audience ?? null,
    report_depth: tpl.report_depth ?? 'full',
    global_requirements: tpl.global_requirements ?? [],
    variables: tpl.variables ?? [],
    outline: (tpl.outline || []).map(normalizeNodeForView),
  }
}

/** Editor mode: null = not editing */
type EditorMode = 'new-blank' | 'new-from-sample' | 'new-from-copy' | 'edit'

interface TemplateManagementProps {
  projectType?: string  // Optional: global mode doesn't filter by project type
  /** 外部导航初始分区（2.3 §4.2）：通知/首页点击 PPT 模板任务 → 直落 PPT模板 Tab */
  initialSection?: 'reports' | 'forms' | 'ppt'
}

export function TemplateManagement({ projectType, initialSection }: TemplateManagementProps) {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
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

  // Drawer state: 'detail' | 'edit' | null (overlay from right)
  const [drawerMode, setDrawerMode] = useState<'detail' | 'edit' | null>(null)
  // Detail drawer tab: overview (structured) or json (full source)
  const [detailTab, setDetailTab] = useState<'overview' | 'json'>('overview')

  // 页内分区（2.4 §5.1 全局页扩展；PPT 分区 2.3 §4.2）：报告模板 | 填表模板 | PPT 模板；
  // initialSection 为外部落点，页面已开时再次导航（如新通知点击）也响应切换
  const [section, setSection] = useState<'reports' | 'forms' | 'ppt'>(initialSection ?? 'reports')
  useEffect(() => {
    if (initialSection) setSection(initialSection)
  }, [initialSection])

  // PPT 模板任务活跃态（2.3 §4.2）：页内「PPT模板」Tab running 呼吸点 / awaiting_input 醒目点
  const { items: pptActiveItems } = useActivity('ppt')
  const tplAwaiting = pptActiveItems.filter((a) => a.container === 'templates' && a.status === 'awaiting_input')
  const tplRunning = pptActiveItems.filter((a) => a.container === 'templates' && a.status !== 'awaiting_input')

  // Editor state
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null)
  const [editorInitialData, setEditorInitialData] = useState<Partial<any>>({})
  const [editorInitialTab, setEditorInitialTab] = useState<'structured' | 'json'>('structured')
  const [editorInitialError, setEditorInitialError] = useState('')
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  const loadTemplates = useCallback(async () => {
    try {
      const res: any = await window.electronAPI.invoke('reports:listTemplates', {
        projectType,
      })
      setTemplates(res.templates || [])
    } catch (err) {
      console.error('Failed to load templates:', err)
      // 列表拉取失败提示（页面遍历 review M3-4 2026-10-24）：不再静默空列表，
      // 用户需知道当前空是失败而非真实为空（手动刷新可恢复）
      showToast(t('common.listLoadFailed'), 'error')
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
  const matches = (tpl: TemplateItem) => {
    const matchesSearch = tpl.template_name.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesType = filterType === 'all' || tpl.project_types.includes(filterType)
    const matchesStage = filterStage === 'all' || tpl.stage === filterStage
    return matchesSearch && matchesType && matchesStage
  }
  const filteredPreset = presetTemplates.filter(matches)
  const filteredCustom = customTemplates.filter(matches)

  // ─ Pagination ──
  const paginatedPreset = filteredPreset.slice((presetPage - 1) * pageSize, presetPage * pageSize)
  const paginatedCustom = filteredCustom.slice((customPage - 1) * pageSize, customPage * pageSize)

  const presetTotalPages = Math.ceil(filteredPreset.length / pageSize)
  const customTotalPages = Math.ceil(filteredCustom.length / pageSize)

  // ── Refresh: force reload preset + custom templates server-side, then re-pull list (2.5 §2.5) ──
  const handleRefreshTemplates = async () => {
    setRefreshing(true)
    try {
      const res: any = await window.electronAPI.invoke('reports:refreshTemplates')
      await loadTemplates()
      showToast(t('project.template.refreshDone', { total: res?.total ?? 0 }), 'success')
    } catch (err) {
      console.error('Failed to refresh templates:', err)
      showToast(t('project.template.refreshFailed'), 'error')
    } finally {
      setRefreshing(false)
    }
  }

  // ── View detail (open drawer) ──
  const handleView = async (tpl: TemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      setViewingTemplate(res.template)
      setDetailTab('overview')
      setDrawerMode('detail')
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
      report_depth: 'full',
      version: '1.0',
      outline: [],
      global_requirements: [],
    })
    setEditorInitialTab('structured')
    setEditorInitialError('')
    setEditorMode('new-blank')
    setDrawerMode('edit')
  }

  // ── Copy from preset (enter edit mode in drawer) ──
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
      setEditorInitialTab('structured')
      setEditorInitialError('')
      setEditorMode('new-from-copy')
      setDrawerMode('edit')
      await loadTemplates()
    } catch (err) {
      console.error('Failed to copy template:', err)
      showToast(`${t('project.template.copyFailed')}: ${(err as any)?.message || err}`, 'error')
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
      setEditorInitialTab('structured')
      setEditorInitialError('')
      setEditorMode('new-from-sample')
      setDrawerMode('edit')
    } catch (err: any) {
      setExtractError(err.message || 'Unknown error')
      setExtracting(false)
    }
  }

  // ── Edit existing custom template (enter edit mode in drawer) ──
  const handleEditCustom = async (tpl: TemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('reports:getTemplate', tpl.template_id)
      const full = res.template as FullTemplate
      setEditorInitialData(full)
      setEditorInitialTab('structured')
      setEditorInitialError('')
      setEditorMode('edit')
      setDrawerMode('edit')
    } catch (err) {
      console.error('Failed to load template for editing:', err)
      showToast(t('project.template.editFailed'), 'error')
    }
  }

  // ── Upgrade / repair a flagged custom template (never a dead end) ──
  const handleUpgrade = async (tpl: TemplateItem) => {
    try {
      const res: any = await window.electronAPI.invoke('reports:upgradeTemplate', tpl.template_id)
      if (res.upgraded) {
        showToast(t('project.template.upgradeSuccess'), 'success')
        await loadTemplates()
      } else if (res.migrated) {
        // Guided repair: open editor on JSON tab with best-effort content + errors
        setEditorInitialData(res.migrated)
        setEditorInitialTab('json')
        setEditorInitialError((res.errors || []).join('; '))
        setEditorMode('edit')
        setDrawerMode('edit')
      } else {
        showToast((res.errors || []).join('; ') || t('project.template.upgradeFailed'), 'error')
      }
    } catch (err) {
      console.error('Failed to upgrade template:', err)
      showToast(`${t('project.template.upgradeFailed')}: ${(err as any)?.message || err}`, 'error')
    }
  }

  // ── Delete custom template ──
  const handleDeleteCustom = async (tpl: TemplateItem) => {
    if (!(await confirmDialog({ title: t('project.template.delete'), message: t('project.template.confirmDelete') }))) return
    try {
      await window.electronAPI.invoke('reports:deleteTemplate', tpl.template_id)
      await loadTemplates()
      // Close drawer if the deleted template was open in it
      if (viewingTemplate?.template_id === tpl.template_id || editorInitialData.template_id === tpl.template_id) {
        setViewingTemplate(null)
        setEditorMode(null)
        setEditorInitialData({})
        setEditorInitialTab('structured')
        setEditorInitialError('')
        setDrawerMode(null)
      }
      showToast(t('project.template.deleteSuccess'), 'success')
    } catch (err) {
      console.error('Failed to delete template:', err)
      showToast(`${t('project.template.deleteFailed')}: ${(err as any)?.message || err}`, 'error')
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
      setEditorInitialTab('structured')
      setEditorInitialError('')
      setViewingTemplate(null)
      setDrawerMode(null) // Close drawer after save
      showToast(t('project.template.editor.saveSuccess'), 'success')
    } catch (err) {
      console.error('Failed to save template:', err)
      showToast(`${t('project.template.saveFailed')}: ${(err as any)?.message || err}`, 'error')
    }
  }

  // ── Close drawer: × / backdrop always fully close (discard unsaved edits) ──
  const handleCloseDrawer = () => {
    setDrawerMode(null)
    setViewingTemplate(null)
    setEditorMode(null)
    setEditorInitialData({})
    setEditorInitialTab('structured')
    setEditorInitialError('')
  }

  const getStageLabel = (stage: string | null) => {
    if (!stage) return t('project.stage.other')
    const key = stage === 'before' ? 'project.stage.before'
      : stage === 'during' ? 'project.stage.during'
      : stage === 'after' ? 'project.stage.after'
      : 'project.stage.other'
    return t(key)
  }

  const getDepthLabel = (depth?: string) =>
    depth === 'brief' ? t('project.template.detail.depthBrief') : t('project.template.detail.depthFull')

  const formatDate = (iso?: string | null) => {
    if (!iso) return '—'
    const d = new Date(iso)
    return isNaN(d.getTime()) ? iso : d.toLocaleDateString()
  }

  // ── View node renderer: show EVERY schema field, dim when empty; recurse to any depth ──
  function renderViewNode(node: any, depth: number, keyVal: string | number): ReactElement {
    const posLabel = node.position === 'front'
      ? t('project.template.editor.positionFront')
      : node.position === 'end'
        ? t('project.template.editor.positionEnd')
        : null
    return (
      <div key={keyVal} className={s.viewNode} style={{ marginLeft: depth === 0 ? 0 : 14 }}>
        <div className={s.viewNodeHead}>
          <span className={s.nodeTitle}>{node.title || <span className={s.phDim}>—</span>}</span>
          <span className={s.mono}>{node.section_id || <span className={s.phDim}>—</span>}</span>
          {node.compose_mode === 'post_body' && <span className={s.modeBadge}>post_body</span>}
          {node.optional && <span className={s.optBadge}>{t('project.template.editor.optionalBadge')}</span>}
        </div>
        <div className={s.viewNodeFields}>
          <div className={s.viewField}>
            <span className={s.fieldLabel}>{t('project.template.editor.composeMode')}</span>
            <span>{node.compose_mode === 'post_body' ? t('project.template.editor.modePostBody') : t('project.template.editor.modeStandard')}</span>
          </div>
          <div className={s.viewField}>
            <span className={s.fieldLabel}>{t('project.template.editor.position')}</span>
            <span>{posLabel || <span className={s.phDim}>—</span>}</span>
          </div>
          <div className={s.viewField}>
            <span className={s.fieldLabel}>{t('project.template.editor.optional')}</span>
            <span>{node.optional ? t('project.template.editor.optionalBadge') : <span className={s.phDim}>—</span>}</span>
          </div>
          <div className={`${s.viewField} ${s.viewFieldWide}`}>
            <span className={s.fieldLabel}>{t('project.template.editor.description')}</span>
            <span className={s.viewFieldVal}>{node.description || <span className={s.phDim}>—</span>}</span>
          </div>
          <div className={`${s.viewField} ${s.viewFieldWide}`}>
            <span className={s.fieldLabel}>{t('project.template.editor.promptHint')}</span>
            <span className={s.viewFieldVal}>{node.prompt_hint || <span className={s.phDim}>—</span>}</span>
          </div>
        </div>
        {Array.isArray(node.children) && node.children.length > 0 && (
          <div className={s.viewNodeChildren}>
            {node.children.map((c: any, i: number) => renderViewNode(c, depth + 1, c.section_id ?? i))}
          </div>
        )}
      </div>
    )
  }

  // ── Table row renderer (shared by preset & custom) ──
  const renderRow = (tpl: TemplateItem, isCustom: boolean) => (
    <div key={tpl.template_id} className={s.tableRow} onClick={() => { tpl.upgrade_needed ? handleUpgrade(tpl) : handleView(tpl); }}>
      <div className={s.colName}>
        <FileText size={14} className={s.rowIcon} />
        <span className={s.nameText}>{tpl.template_name}</span>
        {tpl.upgrade_needed && (
          <span className={s.upgradeBadge} title={tpl.upgrade_error || ''}>
            <AlertCircle size={12} />
            {t('project.template.needsUpgrade')}
          </span>
        )}
      </div>
      <div className={s.colType}>
        {tpl.project_types.map(pt => (
          <span key={pt} className={s.typeBadge} data-type={pt}>{t(`project.type.${pt}`)}</span>
        ))}
      </div>
      <div className={s.colCell}>{getStageLabel(tpl.stage)}</div>
      <div className={s.colCell}>{getDepthLabel(tpl.report_depth)}</div>
      <div className={s.colCell}>{tpl.chapter_count}</div>
      <div className={s.colCell}>{tpl.version || '—'}</div>
      <div className={s.colCell}>{isCustom ? formatDate(tpl.updated_at) : '—'}</div>
      <div className={s.colActions}>
        {tpl.upgrade_needed ? (
          <>
            <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleUpgrade(tpl); }} title={t('project.template.upgrade')}>
              <RefreshCw size={14} />
            </button>
            <button className={`${s.actionBtn} ${s.actionBtnDanger}`} onClick={(e) => { e.stopPropagation(); handleDeleteCustom(tpl); }} title={t('project.template.delete')}>
              <Trash2 size={14} />
            </button>
          </>
        ) : (
          <>
            <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleView(tpl); }} title={t('project.template.view')}>
              <Eye size={14} />
            </button>
            {isCustom ? (
              <>
                <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleEditCustom(tpl); }} title={t('project.template.edit')}>
                  <Edit3 size={14} />
                </button>
                <button className={`${s.actionBtn} ${s.actionBtnDanger}`} onClick={(e) => { e.stopPropagation(); handleDeleteCustom(tpl); }} title={t('project.template.delete')}>
                  <Trash2 size={14} />
                </button>
              </>
            ) : (
              <button className={s.actionBtn} onClick={(e) => { e.stopPropagation(); handleCopyAndEdit(tpl); }} title={t('project.template.copyAsCustom')}>
                <Copy size={14} />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )

  const renderHeader = () => (
    <div className={s.tableHeader}>
      <div className={s.colName}>{t('project.template.col.name')}</div>
      <div className={s.colType}>{t('project.template.col.type')}</div>
      <div className={s.colCell}>{t('project.template.col.stage')}</div>
      <div className={s.colCell}>{t('project.template.col.reportDepth')}</div>
      <div className={s.colCell}>{t('project.template.col.chapters')}</div>
      <div className={s.colCell}>{t('project.template.col.version')}</div>
      <div className={s.colCell}>{t('project.template.col.updatedAt')}</div>
      <div className={s.colActions}>{t('project.template.col.actions')}</div>
    </div>
  )

  return (
    <div className={s.container}>
      {/* Header */}
      <div className={s.header}>
        <h3 className={s.title}>{t('project.template.management')}</h3>
      </div>

      {/* 页内分区 Tab：报告模板 | 填表模板（2.4 §5.1）| PPT 模板（2.3 §4.2）*/}
      <div className={s.pageTabs}>
        <button
          className={section === 'reports' ? `${s.tab} ${s.tabActive}` : s.tab}
          onClick={() => setSection('reports')}
        >{t('project.forms.tpl.reportSection')}</button>
        <button
          className={section === 'forms' ? `${s.tab} ${s.tabActive}` : s.tab}
          onClick={() => setSection('forms')}
        >{t('project.forms.tpl.section')}</button>
        <button
          className={section === 'ppt' ? `${s.tab} ${s.tabActive}` : s.tab}
          onClick={() => setSection('ppt')}
        >{t('project.pptTpl.section')}
          {tplAwaiting.length > 0 ? (
            <span
              className={`${s.pptTabDot} ${s.pptTabDotAlert}`}
              title={t('activity.tipPptTemplatesAwaiting', { count: tplAwaiting.length })}
            />
          ) : tplRunning.length > 0 ? (
            <span
              className={s.pptTabSpinnerWrap}
              title={t('activity.tipPptTemplates', { count: tplRunning.length })}
            >
              <Loader2 size={12} className={s.pptTabSpinner} />
            </span>
          ) : null}
        </button>
      </div>

      {section === 'forms' ? (
        <FormTemplatePanel />
      ) : section === 'ppt' ? (
        <PptTemplatePanel />
      ) : (
      <>
      {/* Full-width list area */}
      <div className={s.listArea}>
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
            <button
              className={s.refreshBtn}
              onClick={handleRefreshTemplates}
              disabled={refreshing}
              title={t('project.template.refreshHint')}
            >
              {refreshing ? <Loader2 size={14} className={s.spinIcon} /> : <RefreshCw size={14} />}
              {t('project.template.refresh')}
            </button>
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
                {renderHeader()}
                {paginatedPreset.map(tpl => renderRow(tpl, false))}
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
                {renderHeader()}
                {paginatedCustom.map(tpl => renderRow(tpl, true))}
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

      {/* Right-side overlay drawer: detail or editor */}
      {drawerMode && (
        <>
          <div className={s.drawerBackdrop} onClick={handleCloseDrawer} />
          <div className={drawerMode === 'edit' ? `${s.drawer} ${s.drawerEdit}` : s.drawer}>
            <div className={s.drawerHeader}>
              <h4>
                {drawerMode === 'detail'
                  ? viewingTemplate?.template_name
                  : (editorMode === 'edit' ? t('project.template.editor.editTemplate') : t('project.template.editor.newTemplate'))}
              </h4>
              <button className={s.closeBtn} onClick={handleCloseDrawer} title={t('common.close')}>
                ×
              </button>
            </div>

            {drawerMode === 'detail' && viewingTemplate && (
              <>
                {/* Detail tabs: overview / json source */}
                <div className={s.tabBar}>
                  <button
                    className={detailTab === 'overview' ? `${s.tab} ${s.tabActive}` : s.tab}
                    onClick={() => setDetailTab('overview')}
                  >{t('project.template.detail.overview')}</button>
                  <button
                    className={detailTab === 'json' ? `${s.tab} ${s.tabActive}` : s.tab}
                    onClick={() => setDetailTab('json')}
                  >{t('project.template.detail.jsonSource')}</button>
                  <div className={s.detailActions}>
                    {!viewingTemplate.is_custom ? (
                      <button className={s.primaryBtn} onClick={() => handleCopyAndEdit(viewingTemplate as unknown as TemplateItem)}>
                        <Copy size={14} />
                        {t('project.template.copyAsCustom')}
                      </button>
                    ) : (
                      <>
                        <button className={s.primaryBtn} onClick={() => handleEditCustom(viewingTemplate as unknown as TemplateItem)}>
                          <Edit3 size={14} />
                          {t('project.template.edit')}
                        </button>
                        <button className={s.dangerBtn} onClick={() => handleDeleteCustom(viewingTemplate as unknown as TemplateItem)}>
                          <Trash2 size={14} />
                          {t('project.template.delete')}
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {detailTab === 'overview' ? (
                  <div className={s.drawerBody}>
                    <p className={s.detailMeta}>
                      {getStageLabel(viewingTemplate.stage)} | {viewingTemplate.chapter_count ?? (viewingTemplate.outline || []).length} {t('project.report.chapters')} | {viewingTemplate.output_format}
                      {viewingTemplate.is_custom && ` | ${t('project.template.editor.custom')}`}
                    </p>

                    {/* Basic info fields */}
                    <div className={s.detailFields}>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.col.version')}</span>
                        <span>{viewingTemplate.version || <span className={s.phDim}>—</span>}</span>
                      </div>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.detail.schemaVersion')}</span>
                        <span className={s.mono}>{viewingTemplate.schema_version != null ? String(viewingTemplate.schema_version) : <span className={s.phDim}>—</span>}</span>
                      </div>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.editor.projectTypes')}</span>
                        <span>{(viewingTemplate.project_types || []).length
                          ? (viewingTemplate.project_types || []).map(pt => t(`project.type.${pt}`)).join('、')
                          : <span className={s.phDim}>—</span>}</span>
                      </div>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.detail.reportDepth')}</span>
                        <span>{getDepthLabel(viewingTemplate.report_depth)}</span>
                      </div>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.editor.roleHint')}</span>
                        <span>{viewingTemplate.role_hint || <span className={s.phDim}>—</span>}</span>
                      </div>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.editor.audience')}</span>
                        <span>{viewingTemplate.audience || <span className={s.phDim}>—</span>}</span>
                      </div>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.editor.outputFormat')}</span>
                        <span>{viewingTemplate.output_format || <span className={s.phDim}>—</span>}</span>
                      </div>
                      <div className={s.detailField}>
                        <span className={s.fieldLabel}>{t('project.template.editor.templateId')}</span>
                        <span className={s.mono}>{viewingTemplate.template_id || <span className={s.phDim}>—</span>}</span>
                      </div>
                    </div>

                    {/* Variables (always shown, even when empty) */}
                    <div className={s.detailBlock}>
                      <p className={s.detailSectionTitle}>{t('project.template.editor.variables')}</p>
                      {(viewingTemplate.variables || []).length > 0 ? (
                        <div className={s.chapterPreview}>
                          {(viewingTemplate.variables || []).map((v: any, idx: number) => (
                            <div key={idx} className={s.viewNode}>
                              <div className={s.viewNodeHead}>
                                <span className={s.mono}>{v.name}</span>
                                <span className={s.srcBadge} data-src={v.source}>{v.source === 'derived' ? t('project.template.editor.sourceDerived') : t('project.template.editor.sourceUser')}</span>
                                {v.label && <span className={s.nodeTitle}>{v.label}</span>}
                                {v.source !== 'derived' && v.required !== false && <span className={s.optBadge}>{t('project.template.editor.varRequired')}</span>}
                              </div>
                              <div className={s.viewNodeFields}>
                                {v.source === 'derived' ? (
                                  <>
                                    <div className={s.viewField}>
                                      <span className={s.fieldLabel}>{t('project.template.editor.varDependsOn')}</span>
                                      <span className={s.viewFieldVal}>{(v.depends_on || []).join('、') || <span className={s.phDim}>—</span>}</span>
                                    </div>
                                    <div className={s.viewField}>
                                      <span className={s.fieldLabel}>{t('project.template.editor.varDataSources')}</span>
                                      <span className={s.viewFieldVal}>{(v.data_sources || []).map((d: string) => d === 'internet' ? t('project.template.editor.dsInternet') : t('project.template.editor.dsKnowledge')).join('、') || <span className={s.phDim}>—</span>}</span>
                                    </div>
                                    <div className={s.viewField}>
                                      <span className={s.fieldLabel}>{t('project.template.editor.varCardinality')}</span>
                                      <span className={s.viewFieldVal}>{v.cardinality === 'list' ? t('project.template.editor.cardList') : t('project.template.editor.cardScalar')}</span>
                                    </div>
                                    <div className={`${s.viewField} ${s.viewFieldWide}`}>
                                      <span className={s.fieldLabel}>{t('project.template.editor.varDeriveHint')}</span>
                                      <span className={s.viewFieldVal}>{v.derive_hint || <span className={s.phDim}>—</span>}</span>
                                    </div>
                                  </>
                                ) : (
                                  <div className={`${s.viewField} ${s.viewFieldWide}`}>
                                    <span className={s.fieldLabel}>{t('project.template.editor.varPlaceholder')}</span>
                                    <span className={s.viewFieldVal}>{v.placeholder || <span className={s.phDim}>—</span>}</span>
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className={s.emptyChapters}>—</p>
                      )}
                    </div>

                    {/* Global requirements (always shown, even when empty) */}
                    <div className={s.detailBlock}>
                      <p className={s.detailSectionTitle}>{t('project.template.editor.globalRequirements')}</p>
                      {(viewingTemplate.global_requirements || []).length > 0 ? (
                        <ul className={s.reqList}>
                          {(viewingTemplate.global_requirements || []).map((req, idx) => (
                            <li key={idx}>{req}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className={s.emptyChapters}>—</p>
                      )}
                    </div>

                    {/* Chapter structure with per-section description and prompt hint */}
                    <div className={s.detailBlock}>
                      <p className={s.detailSectionTitle}>{t('project.template.chapterStructure')}</p>
                      <div className={s.chapterPreview}>
                        {normalizeForView(viewingTemplate).outline.map((ch: any, i: number) => renderViewNode(ch, 0, ch.section_id ?? i))}
                        {(!viewingTemplate.outline || viewingTemplate.outline.length === 0) && (
                          <p className={s.emptyChapters}>{t('project.template.noChapters')}</p>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className={s.drawerBody}>
                    <pre className={s.jsonView}>{JSON.stringify(normalizeForView(viewingTemplate), null, 2)}</pre>
                  </div>
                )}
              </>
            )}

            {drawerMode === 'edit' && editorMode && (
              <div className={s.editorWrapper}>
                <TemplateEditor
                  initialData={editorInitialData}
                  initialTab={editorInitialTab}
                  initialError={editorInitialError}
                  onSave={handleEditorSave}
                />
              </div>
            )}
          </div>
        </>
      )}
      </>
      )}
    </div>
  )
}
