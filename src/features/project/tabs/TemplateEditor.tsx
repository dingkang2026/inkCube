/** src/features/project/tabs/TemplateEditor.tsx — Full-field template editor (structured + JSON source), embedded in the edit drawer */
import { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronDown, ChevronUp, Plus, Trash2, Save, AlertCircle, Loader2, X, Settings2,
  CornerLeftUp, CornerRightDown,
} from 'lucide-react'
import s from './TemplateEditor.module.css'

/** Outline node — mirrors resources/templates/_schema.json #/$defs/outline_node */
interface OutlineNode {
  section_id: string
  title: string
  description?: string
  compose_mode?: string
  prompt_hint?: string
  optional?: boolean
  position?: string
  children?: OutlineNode[]
}

/** Template variable — mirrors resources/templates/_schema.json #/$defs/variable */
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

/** Implicit project-context variables: always available at runtime, never declared in `variables` */
const IMPLICIT_VARS = ['company_name', 'credit_code', 'project_name', 'project_type', 'stage']

/** Full template data for editing (all top-level schema fields) */
interface TemplateData {
  template_id?: string
  template_name: string
  version: string
  project_types: string[]
  stage: string | null
  output_format: string
  role_hint?: string
  audience?: string
  report_depth?: string
  global_requirements: string[]
  variables?: TemplateVariable[]
  outline: OutlineNode[]
  schema_version?: number
}

/** Current template schema version (mirrors backend CURRENT_SCHEMA_VERSION) */
const CURRENT_SCHEMA_VERSION = 2

interface TemplateEditorProps {
  /** Initial template data (from LLM extraction, copy, blank, or existing) */
  initialData: Partial<TemplateData> & { template_id?: string }
  /** Called when user saves */
  onSave: (template: TemplateData) => Promise<void>
  /** Initial active tab (repair flow opens JSON directly) */
  initialTab?: 'structured' | 'json'
  /** Initial error message (repair flow surfaces validation errors) */
  initialError?: string
}

function generateId(prefix = 'ch'): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 8)}`
}

// ── Legacy-shape tolerance: map old `id` → `section_id` so pre-v2 data still edits ──
function normalizeOutlineNode(node: any): OutlineNode {
  const out: any = { ...node }
  if (out.section_id === undefined || out.section_id === null || out.section_id === '') {
    out.section_id = out.id || generateId('sec')
  }
  delete out.id
  if (Array.isArray(out.children)) out.children = out.children.map(normalizeOutlineNode)
  return out as OutlineNode
}
function normalizeOutline(nodes: any[]): OutlineNode[] {
  return (nodes || []).map(normalizeOutlineNode)
}

// ── Recursive outline tree helpers (match by unique section_id) ──
function updateNode(tree: OutlineNode[], id: string, updates: Partial<OutlineNode>): OutlineNode[] {
  return tree.map(n => {
    if (n.section_id === id) return { ...n, ...updates }
    if (n.children?.length) return { ...n, children: updateNode(n.children, id, updates) }
    return n
  })
}

function removeNode(tree: OutlineNode[], id: string): OutlineNode[] {
  return tree
    .filter(n => n.section_id !== id)
    .map(n => (n.children?.length ? { ...n, children: removeNode(n.children, id) } : n))
}

function addChild(tree: OutlineNode[], parentId: string, child: OutlineNode): OutlineNode[] {
  return tree.map(n => {
    if (n.section_id === parentId) return { ...n, children: [...(n.children || []), child] }
    if (n.children?.length) return { ...n, children: addChild(n.children, parentId, child) }
    return n
  })
}

function moveNode(tree: OutlineNode[], id: string, dir: 'up' | 'down'): OutlineNode[] {
  const idx = tree.findIndex(n => n.section_id === id)
  if (idx >= 0) {
    const target = dir === 'up' ? idx - 1 : idx + 1
    if (target < 0 || target >= tree.length) return tree
    const next = [...tree]
    ;[next[idx], next[target]] = [next[target], next[idx]]
    return next
  }
  return tree.map(n => (n.children?.length ? { ...n, children: moveNode(n.children, id, dir) } : n))
}

/** Promote a node one level up: it becomes the next sibling of its parent */
function promoteNode(tree: OutlineNode[], id: string): OutlineNode[] {
  const idx = tree.findIndex(n => n.children?.some(c => c.section_id === id))
  if (idx >= 0) {
    const parent = tree[idx]
    const child = parent.children!.find(c => c.section_id === id)!
    const next = [...tree]
    next[idx] = { ...parent, children: parent.children!.filter(c => c.section_id !== id) }
    next.splice(idx + 1, 0, child)
    return next
  }
  return tree.map(n => (n.children?.length ? { ...n, children: promoteNode(n.children, id) } : n))
}

/** Demote a node one level down: it becomes the last child of its previous sibling */
function demoteNode(tree: OutlineNode[], id: string): OutlineNode[] {
  const idx = tree.findIndex(n => n.section_id === id)
  if (idx >= 0) {
    if (idx === 0) return tree // no previous sibling to nest under
    const node = tree[idx]
    const prev = tree[idx - 1]
    const next = tree.filter((_, i) => i !== idx)
    next[idx - 1] = { ...prev, children: [...(prev.children || []), node] }
    return next
  }
  return tree.map(n => (n.children?.length ? { ...n, children: demoteNode(n.children, id) } : n))
}

// ── Validation helpers (mirror _schema.json constraints) ──
function collectIds(nodes: OutlineNode[], acc: string[] = []): string[] {
  for (const n of nodes) {
    acc.push(n.section_id)
    if (n.children?.length) collectIds(n.children, acc)
  }
  return acc
}

function hasPostBodyWithChildren(nodes: OutlineNode[]): boolean {
  for (const n of nodes) {
    if (n.compose_mode === 'post_body' && n.children && n.children.length) return true
    if (n.children?.length && hasPostBodyWithChildren(n.children)) return true
  }
  return false
}

/** Strip empty/inapplicable fields so saved JSON matches _schema.json ($defs/variable) */
function cleanVariable(v: TemplateVariable): TemplateVariable | null {
  const name = (v.name || '').trim()
  if (!name) return null
  const label = (v.label || '').trim() || undefined
  if (v.source === 'derived') {
    return {
      name, source: 'derived', label,
      depends_on: (v.depends_on || []).length ? v.depends_on : undefined,
      derive_hint: (v.derive_hint || '').trim(),
      data_sources: v.data_sources || [],
      cardinality: v.cardinality || 'scalar',
    }
  }
  return {
    name, source: 'user', label,
    required: v.required !== false,
    placeholder: (v.placeholder || '').trim() || undefined,
  }
}

/** Detect a cycle in the variable dependency graph (DFS, 3-color) */
function hasVariableCycle(vars: TemplateVariable[]): boolean {
  const names = new Set(vars.map(v => v.name))
  const adj = new Map<string, string[]>()
  for (const v of vars) adj.set(v.name, (v.depends_on || []).filter(d => names.has(d)))
  const state = new Map<string, number>() // 0 unvisited, 1 visiting, 2 done
  const dfs = (n: string): boolean => {
    const st = state.get(n) ?? 0
    if (st === 1) return true
    if (st === 2) return false
    state.set(n, 1)
    for (const m of adj.get(n) || []) if (dfs(m)) return true
    state.set(n, 2)
    return false
  }
  return vars.some(v => dfs(v.name))
}

/** Validate declared variables; returns an error message or null when valid */
function validateVariables(vars: TemplateVariable[], t: (k: string) => string): string | null {
  const names = vars.map(v => (v.name || '').trim())
  if (names.some(n => !n)) return t('project.template.editor.varNameRequired')
  if (new Set(names).size !== names.length) return t('project.template.editor.duplicateVarName')
  const known = new Set([...names, ...IMPLICIT_VARS])
  for (const v of vars) {
    if (v.source === 'derived') {
      if (!(v.derive_hint || '').trim()) return t('project.template.editor.deriveHintRequired')
      if (!v.data_sources || v.data_sources.length === 0) return t('project.template.editor.dataSourcesRequired')
    }
    for (const d of v.depends_on || []) {
      if (!known.has(d)) return t('project.template.editor.dependsOnUnknown')
    }
  }
  if (hasVariableCycle(vars)) return t('project.template.editor.variableCycle')
  return null
}

/** Validate a template object; returns an error message or null when valid */
function validateTemplate(tpl: any, t: (k: string) => string): string | null {
  if (!tpl.template_name || !String(tpl.template_name).trim()) return t('project.template.editor.nameRequired')
  if (!/^\d+(\.\d+){1,2}$/.test(String(tpl.version || ''))) return t('project.template.editor.versionInvalid')
  if (!Array.isArray(tpl.project_types) || tpl.project_types.length === 0) return t('project.template.editor.typesRequired')
  if (!Array.isArray(tpl.outline) || tpl.outline.length === 0) return t('project.template.editor.outlineRequired')
  const ids = collectIds(tpl.outline)
  if (new Set(ids).size !== ids.length) return t('project.template.editor.duplicateSectionId')
  if (ids.some(id => !id || !/^[a-zA-Z0-9_.-]+$/.test(id))) return t('project.template.editor.invalidSectionId')
  if (hasPostBodyWithChildren(tpl.outline)) return t('project.template.editor.postBodyNoChildren')
  const vars: TemplateVariable[] = tpl.variables || []
  const verr = validateVariables(vars, t)
  if (verr) return verr
  return null
}

/** Recursive node editor for a single outline node and its descendants */
function NodeEditor({
  node, depth, expanded, editingId, t, canPromote, canDemote,
  onToggleExpand, onToggleEdit, onUpdate, onRemove, onMove, onAddChild, onPromote, onDemote,
}: {
  node: OutlineNode
  depth: number
  expanded: Set<string>
  editingId: string | null
  t: (k: string) => string
  canPromote: boolean
  canDemote: boolean
  onToggleExpand: (id: string) => void
  onToggleEdit: (id: string) => void
  onUpdate: (id: string, updates: Partial<OutlineNode>) => void
  onRemove: (id: string) => void
  onMove: (id: string, dir: 'up' | 'down') => void
  onAddChild: (id: string) => void
  onPromote: (id: string) => void
  onDemote: (id: string) => void
}) {
  const isPostBody = node.compose_mode === 'post_body'
  const hasChildren = !!node.children && node.children.length > 0
  const isExpanded = expanded.has(node.section_id)
  const isEditing = editingId === node.section_id

  return (
    <div className={s.nodeItem} style={{ marginLeft: depth === 0 ? 0 : 16 }}>
      <div className={s.nodeHeader}>
        {hasChildren ? (
          <button
            className={s.iconBtn}
            onClick={() => onToggleExpand(node.section_id)}
            title={t('project.template.editor.toggleExpand')}
          >
            {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        ) : (
          <span className={s.expandSpacer} aria-hidden="true" />
        )}
        <button
          className={s.iconBtn}
          onClick={() => onToggleEdit(node.section_id)}
          title={t('project.template.editor.editDetails')}
        >
          <Settings2 size={14} />
        </button>
        <input
          className={s.nodeTitleInput}
          value={node.title}
          placeholder={t('project.template.editor.sectionTitle')}
          onChange={e => onUpdate(node.section_id, { title: e.target.value })}
        />
        {isPostBody && <span className={s.modeBadge}>post_body</span>}
        {node.optional && <span className={s.optBadge}>{t('project.template.editor.optionalBadge')}</span>}
        <div className={s.nodeActions}>
          <button className={s.iconBtn} onClick={() => onMove(node.section_id, 'up')} title={t('common.prev')}>
            <ChevronUp size={14} />
          </button>
          <button className={s.iconBtn} onClick={() => onMove(node.section_id, 'down')} title={t('common.next')}>
            <ChevronDown size={14} />
          </button>
          <button className={s.iconBtn} onClick={() => onPromote(node.section_id)} disabled={!canPromote} title={t('project.template.editor.promote')}>
            <CornerLeftUp size={14} />
          </button>
          <button className={s.iconBtn} onClick={() => onDemote(node.section_id)} disabled={!canDemote} title={t('project.template.editor.demote')}>
            <CornerRightDown size={14} />
          </button>
          {!isPostBody && (
            <button className={s.iconBtn} onClick={() => onAddChild(node.section_id)} title={t('project.template.editor.addSubSection')}>
              <Plus size={14} />
            </button>
          )}
          <button className={s.dangerBtn} onClick={() => onRemove(node.section_id)} title={t('project.template.delete')}>
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {isEditing && (
        <div className={s.nodeFields}>
          <div className={s.field}>
            <label>{t('project.template.editor.description')}</label>
            <textarea rows={2} value={node.description || ''} onChange={e => onUpdate(node.section_id, { description: e.target.value })} />
          </div>
          <div className={s.field}>
            <label>{t('project.template.editor.promptHint')}</label>
            <textarea rows={2} value={node.prompt_hint || ''} onChange={e => onUpdate(node.section_id, { prompt_hint: e.target.value })} />
          </div>
          <div className={s.fieldRow}>
            <label>{t('project.template.editor.composeMode')}</label>
            <select value={node.compose_mode || 'standard'} onChange={e => onUpdate(node.section_id, { compose_mode: e.target.value })}>
              <option value="standard">{t('project.template.editor.modeStandard')}</option>
              <option value="post_body">{t('project.template.editor.modePostBody')}</option>
            </select>
            <label className={s.checkboxInline}>
              <input type="checkbox" checked={!!node.optional} onChange={e => onUpdate(node.section_id, { optional: e.target.checked })} />
              {t('project.template.editor.optional')}
            </label>
          </div>
          {isPostBody && (
            <div className={s.fieldRow}>
              <label>{t('project.template.editor.position')}</label>
              <select value={node.position || 'end'} onChange={e => onUpdate(node.section_id, { position: e.target.value })}>
                <option value="front">{t('project.template.editor.positionFront')}</option>
                <option value="end">{t('project.template.editor.positionEnd')}</option>
              </select>
            </div>
          )}
          <div className={s.fieldRow}>
            <label>{t('project.template.editor.sectionId')}</label>
            <span className={s.mono}>{node.section_id}</span>
          </div>
        </div>
      )}

      {hasChildren && isExpanded && (
        <div className={s.nodeChildren}>
          {node.children!.map((child, idx) => (
            <NodeEditor
              key={child.section_id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              editingId={editingId}
              t={t}
              canPromote={true}
              canDemote={idx > 0 && node.children![idx - 1].compose_mode !== 'post_body'}
              onToggleExpand={onToggleExpand}
              onToggleEdit={onToggleEdit}
              onUpdate={onUpdate}
              onRemove={onRemove}
              onMove={onMove}
              onAddChild={onAddChild}
              onPromote={onPromote}
              onDemote={onDemote}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/** Editor card for a single template variable (user or derived) */
function VariableCard({ v, allNames, t, onChange, onRemove }: {
  v: TemplateVariable
  allNames: string[]
  t: (k: string) => string
  onChange: (updates: Partial<TemplateVariable>) => void
  onRemove: () => void
}) {
  const isDerived = v.source === 'derived'
  const depOptions = [...allNames.filter(n => n !== v.name), ...IMPLICIT_VARS]
  const toggleDep = (name: string) => {
    const cur = v.depends_on || []
    onChange({ depends_on: cur.includes(name) ? cur.filter(x => x !== name) : [...cur, name] })
  }
  const toggleDs = (ds: string) => {
    const cur = v.data_sources || []
    onChange({ data_sources: cur.includes(ds) ? cur.filter(x => x !== ds) : [...cur, ds] })
  }
  const switchSource = (src: 'user' | 'derived') => {
    onChange(src === 'derived'
      ? { source: 'derived', cardinality: v.cardinality || 'scalar', data_sources: v.data_sources || [], depends_on: v.depends_on || [] }
      : { source: 'user', required: v.required !== false })
  }
  return (
    <div className={s.varCard}>
      <div className={s.varHeader}>
        <input
          className={s.varNameInput}
          value={v.name}
          placeholder={t('project.template.editor.varNamePlaceholder')}
          onChange={e => onChange({ name: e.target.value })}
        />
        <select className={s.varSourceSelect} value={v.source} title={t('project.template.editor.varSource')} onChange={e => switchSource(e.target.value as 'user' | 'derived')}>
          <option value="user">{t('project.template.editor.sourceUser')}</option>
          <option value="derived">{t('project.template.editor.sourceDerived')}</option>
        </select>
        <button className={s.dangerBtn} onClick={onRemove} title={t('project.template.delete')}><Trash2 size={13} /></button>
      </div>
      <div className={s.varFields}>
        <div className={s.field}>
          <label>{t('project.template.editor.varLabel')}</label>
          <input value={v.label || ''} placeholder={t('project.template.editor.varLabelPlaceholder')} onChange={e => onChange({ label: e.target.value })} />
        </div>
        {isDerived ? (
          <>
            <div className={s.field}>
              <label title={t('project.template.editor.varDependsOnHint')}>{t('project.template.editor.varDependsOn')}</label>
              <div className={s.chipGroup}>
                {depOptions.map(name => (
                  <label key={name}>
                    <input type="checkbox" checked={(v.depends_on || []).includes(name)} onChange={() => toggleDep(name)} />
                    <span className={IMPLICIT_VARS.includes(name) ? s.mono : undefined}>{name}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className={s.field}>
              <label>{t('project.template.editor.varDeriveHint')}</label>
              <textarea rows={2} value={v.derive_hint || ''} placeholder={t('project.template.editor.varDeriveHintPlaceholder')} onChange={e => onChange({ derive_hint: e.target.value })} />
            </div>
            <div className={s.field}>
              <label>{t('project.template.editor.varDataSources')}</label>
              <div className={s.chipGroup}>
                <label><input type="checkbox" checked={(v.data_sources || []).includes('knowledge')} onChange={() => toggleDs('knowledge')} /> {t('project.template.editor.dsKnowledge')}</label>
                <label><input type="checkbox" checked={(v.data_sources || []).includes('internet')} onChange={() => toggleDs('internet')} /> {t('project.template.editor.dsInternet')}</label>
              </div>
            </div>
            <div className={s.fieldRow}>
              <label>{t('project.template.editor.varCardinality')}</label>
              <select value={v.cardinality || 'scalar'} onChange={e => onChange({ cardinality: e.target.value as 'scalar' | 'list' })}>
                <option value="scalar">{t('project.template.editor.cardScalar')}</option>
                <option value="list">{t('project.template.editor.cardList')}</option>
              </select>
            </div>
          </>
        ) : (
          <>
            <div className={s.fieldRow}>
              <label className={s.checkboxInline}>
                <input type="checkbox" checked={v.required !== false} onChange={e => onChange({ required: e.target.checked })} />
                {t('project.template.editor.varRequired')}
              </label>
            </div>
            <div className={s.field}>
              <label>{t('project.template.editor.varPlaceholder')}</label>
              <input value={v.placeholder || ''} placeholder={t('project.template.editor.varPlaceholderText')} onChange={e => onChange({ placeholder: e.target.value })} />
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export function TemplateEditor({ initialData, onSave, initialTab, initialError }: TemplateEditorProps) {
  const { t } = useTranslation()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(initialError || '')

  // Top-level field state
  const [templateName, setTemplateName] = useState(initialData.template_name || '')
  const [version, setVersion] = useState(initialData.version || '1.0')
  const [roleHint, setRoleHint] = useState(initialData.role_hint || '')
  const [audience, setAudience] = useState(initialData.audience || '')
  const [reportDepth, setReportDepth] = useState(initialData.report_depth || 'full')
  const [projectTypes, setProjectTypes] = useState<string[]>(initialData.project_types || ['general'])
  const [stage, setStage] = useState<string | null>(initialData.stage || null)
  const [outputFormat, setOutputFormat] = useState(initialData.output_format || 'docx')
  const [outline, setOutline] = useState<OutlineNode[]>(normalizeOutline(initialData.outline || []))
  const [globalRequirements, setGlobalRequirements] = useState<string[]>(initialData.global_requirements || [])
  const [variables, setVariables] = useState<TemplateVariable[]>(initialData.variables || [])

  // UI state
  const [activeTab, setActiveTab] = useState<'structured' | 'json'>(initialTab || 'structured')
  const [jsonText, setJsonText] = useState(
    initialTab === 'json' ? JSON.stringify(initialData, null, 2) : ''
  )
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [editingId, setEditingId] = useState<string | null>(null)

  const toggleProjectType = (type: string) => {
    setProjectTypes(prev => (prev.includes(type) ? prev.filter(x => x !== type) : [...prev, type]))
  }

  // ── Build the full template object from structured state ──
  const buildTemplate = useCallback((): TemplateData => {
    const tpl: TemplateData = {
      template_name: templateName.trim(),
      version: version.trim() || '1.0',
      project_types: projectTypes,
      stage,
      output_format: outputFormat,
      role_hint: roleHint.trim(),
      audience: audience.trim(),
      report_depth: reportDepth,
      global_requirements: globalRequirements.filter(r => r.trim()),
      outline,
      schema_version: CURRENT_SCHEMA_VERSION,
    }
    const cleanedVars = variables.map(cleanVariable).filter((v): v is TemplateVariable => v !== null)
    if (cleanedVars.length) tpl.variables = cleanedVars
    if (initialData.template_id) tpl.template_id = initialData.template_id
    return tpl
  }, [templateName, version, projectTypes, stage, outputFormat, roleHint, audience, reportDepth, globalRequirements, variables, outline, initialData.template_id])

  // ── Apply a parsed template object back into structured state ──
  const applyTemplate = (tpl: any) => {
    setTemplateName(tpl.template_name || '')
    setVersion(tpl.version || '1.0')
    setRoleHint(tpl.role_hint || '')
    setAudience(tpl.audience || '')
    setReportDepth(tpl.report_depth || 'full')
    setProjectTypes(tpl.project_types || ['general'])
    setStage(tpl.stage ?? null)
    setOutputFormat(tpl.output_format || 'docx')
    setOutline(normalizeOutline(tpl.outline || []))
    setGlobalRequirements(tpl.global_requirements || [])
    setVariables(tpl.variables || [])
  }

  // ── Tab switching (keep JSON text in sync with structured state) ──
  const switchTab = (tab: 'structured' | 'json') => {
    if (tab === activeTab) return
    if (tab === 'json') {
      setJsonText(JSON.stringify(buildTemplate(), null, 2))
      setError('')
      setActiveTab('json')
    } else {
      // Parse + validate JSON back into structured state
      try {
        const parsed = JSON.parse(jsonText)
        const verr = validateTemplate(parsed, t)
        if (verr) { setError(verr); return }
        applyTemplate(parsed)
        setError('')
        setActiveTab('structured')
      } catch (e: any) {
        setError(t('project.template.editor.jsonInvalid') + ': ' + (e.message || String(e)))
      }
    }
  }

  // ── Outline operations ──
  const addChapter = () => {
    const node: OutlineNode = { section_id: generateId('ch'), title: t('project.template.editor.newChapter'), children: [] }
    setOutline(prev => [...prev, node])
    setEditingId(node.section_id)
  }
  const handleAddChild = (parentId: string) => {
    const child: OutlineNode = { section_id: generateId('sec'), title: t('project.template.editor.newSection') }
    setOutline(prev => addChild(prev, parentId, child))
    setExpanded(prev => new Set(prev).add(parentId))
    setEditingId(child.section_id)
  }
  const handleUpdate = (id: string, updates: Partial<OutlineNode>) => setOutline(prev => updateNode(prev, id, updates))
  const handleRemove = (id: string) => setOutline(prev => removeNode(prev, id))
  const handleMove = (id: string, dir: 'up' | 'down') => setOutline(prev => moveNode(prev, id, dir))
  const handlePromote = (id: string) => setOutline(prev => promoteNode(prev, id))
  const handleDemote = (id: string) => setOutline(prev => demoteNode(prev, id))
  const toggleExpand = (id: string) => setExpanded(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  const toggleEdit = (id: string) => setEditingId(prev => (prev === id ? null : id))

  // ── Global requirements list ──
  const addRequirement = () => setGlobalRequirements(prev => [...prev, ''])
  const updateRequirement = (idx: number, value: string) => setGlobalRequirements(prev => prev.map((r, i) => (i === idx ? value : r)))
  const removeRequirement = (idx: number) => setGlobalRequirements(prev => prev.filter((_, i) => i !== idx))

  // ── Variables list ──
  const addVariable = () => setVariables(prev => [...prev, { name: '', source: 'user', required: true }])
  const updateVariable = (idx: number, updates: Partial<TemplateVariable>) => setVariables(prev => prev.map((v, i) => (i === idx ? { ...v, ...updates } : v)))
  const removeVariable = (idx: number) => setVariables(prev => prev.filter((_, i) => i !== idx))

  // ── Save ──
  const handleSave = useCallback(async () => {
    let template: TemplateData
    if (activeTab === 'json') {
      try {
        template = JSON.parse(jsonText)
      } catch (e: any) {
        setError(t('project.template.editor.jsonInvalid') + ': ' + (e.message || String(e)))
        return
      }
    } else {
      template = buildTemplate()
    }
    // Preserve the immutable template_id when editing an existing template
    if (initialData.template_id) template.template_id = initialData.template_id
    const verr = validateTemplate(template, t)
    if (verr) { setError(verr); return }
    setSaving(true)
    setError('')
    try {
      await onSave(template)
    } catch (err: any) {
      setError(err.message || String(err))
    } finally {
      setSaving(false)
    }
  }, [activeTab, jsonText, buildTemplate, initialData.template_id, onSave, t])

  return (
    <div className={s.container}>
      {/* Toolbar: tabs + save */}
      <div className={s.toolbar}>
        <div className={s.tabBar}>
          <button
            className={activeTab === 'structured' ? `${s.tab} ${s.tabActive}` : s.tab}
            onClick={() => switchTab('structured')}
          >{t('project.template.editor.tabStructured')}</button>
          <button
            className={activeTab === 'json' ? `${s.tab} ${s.tabActive}` : s.tab}
            onClick={() => switchTab('json')}
          >{t('project.template.editor.tabJson')}</button>
        </div>
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

      {activeTab === 'json' ? (
        <div className={s.contentArea}>
          <textarea
            className={s.jsonEditor}
            value={jsonText}
            spellCheck={false}
            onChange={e => setJsonText(e.target.value)}
          />
        </div>
      ) : (
        <div className={s.contentArea}>
          {/* Basic Info */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.editor.basicInfo')}</div>
            {initialData.template_id && (
              <div className={s.fieldRow}>
                <label>{t('project.template.editor.templateId')}</label>
                <span className={s.mono}>{initialData.template_id}</span>
              </div>
            )}
            <div className={s.fieldGrid}>
              <div className={s.field}>
                <label>{t('project.template.editor.templateName')}</label>
                <input value={templateName} onChange={e => setTemplateName(e.target.value)} placeholder={t('project.template.editor.templateNamePlaceholder')} />
              </div>
              <div className={s.field}>
                <label>{t('project.template.editor.version')}</label>
                <input value={version} onChange={e => setVersion(e.target.value)} placeholder="1.0" />
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
                <label>{t('project.template.editor.reportDepth')}</label>
                <select value={reportDepth} onChange={e => setReportDepth(e.target.value)}>
                  <option value="full">{t('project.template.detail.depthFull')}</option>
                  <option value="brief">{t('project.template.detail.depthBrief')}</option>
                </select>
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

          {/* Variables (user + derived) */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.editor.variables')}</div>
            <p className={s.sectionHint}>{t('project.template.editor.variablesHint')}</p>
            {variables.map((v, idx) => (
              <VariableCard
                key={idx}
                v={v}
                allNames={variables.map(x => (x.name || '').trim()).filter(Boolean)}
                t={t}
                onChange={updates => updateVariable(idx, updates)}
                onRemove={() => removeVariable(idx)}
              />
            ))}
            <button className={s.addBtn} onClick={addVariable}>
              <Plus size={14} />
              {t('project.template.editor.addVariable')}
            </button>
          </div>

          {/* Outline (recursive) */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.editor.outline')}</div>
            {outline.map((node, idx) => (
              <NodeEditor
                key={node.section_id}
                node={node}
                depth={0}
                expanded={expanded}
                editingId={editingId}
                t={t}
                canPromote={false}
                canDemote={idx > 0 && outline[idx - 1].compose_mode !== 'post_body'}
                onToggleExpand={toggleExpand}
                onToggleEdit={toggleEdit}
                onUpdate={handleUpdate}
                onRemove={handleRemove}
                onMove={handleMove}
                onAddChild={handleAddChild}
                onPromote={handlePromote}
                onDemote={handleDemote}
              />
            ))}
            <button className={s.addBtn} onClick={addChapter}>
              <Plus size={14} />
              {t('project.template.editor.addChapter')}
            </button>
          </div>

          {/* Global requirements */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.editor.globalRequirements')}</div>
            {globalRequirements.map((req, idx) => (
              <div key={idx} className={s.listRow}>
                <input value={req} onChange={e => updateRequirement(idx, e.target.value)} placeholder={t('project.template.editor.requirementPlaceholder')} />
                <button className={s.dangerBtnSmall} onClick={() => removeRequirement(idx)}><Trash2 size={11} /></button>
              </div>
            ))}
            <button className={s.addBtnSmall} onClick={addRequirement}><Plus size={12} /> {t('project.template.editor.addRequirement')}</button>
          </div>
        </div>
      )}
    </div>
  )
}
