/** src/features/project/tabs/FormsTab.tsx — Forms tab with filling flow (UI-First, 填表) */
import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Table, Plus, FileSpreadsheet, CheckCircle, Clock, ArrowLeft, Download, Loader2 } from 'lucide-react'
import type { ProjectType, ProjectStage } from '../types'
import s from './FormsTab.module.css'

/* ── Mock data ── */
const MOCK_FORM_TEMPLATES = [
  { id: 'f1', name: '项目登记表', format: 'DOCX', type: 'recruitment' as ProjectType },
  { id: 'f2', name: '招商跟踪表', format: 'DOCX', type: 'recruitment' as ProjectType },
  { id: 'f3', name: '政策申报表', format: 'DOCX', type: 'recruitment' as ProjectType },
  { id: 'f4', name: '尽调清单', format: 'DOCX', type: 'investment' as ProjectType },
  { id: 'f5', name: '投资决策表', format: 'DOCX', type: 'investment' as ProjectType },
  { id: 'f6', name: '估值对比表', format: 'XLSX', type: 'investment' as ProjectType },
  { id: 'f7', name: '企业经营跟踪表', format: 'DOCX', type: 'recruitment' as ProjectType },
]

const MOCK_FORMS = [
  { id: 'fi1', title: 'XX公司尽调清单', templateName: '尽调清单', format: 'DOCX', createdAt: '2026-09-19', status: 'completed' as const },
  { id: 'fi2', title: 'XX项目登记表', templateName: '项目登记表', format: 'DOCX', createdAt: '2026-09-21', status: 'completed' as const },
  { id: 'fi3', title: '估值对比分析', templateName: '估值对比表', format: 'XLSX', createdAt: '2026-09-23', status: 'filling' as const },
]

/** Mock form fields per template */
const MOCK_FIELDS: Record<string, { label: string; value: string }[]> = {
  f1: [
    { label: '项目名称', value: 'XX科技有限公司投资项目' },
    { label: '企业名称', value: 'XX科技有限公司' },
    { label: '联系人', value: '张三' },
    { label: '联系电话', value: '138****5678' },
    { label: '所属行业', value: '新材料' },
    { label: '注册资本', value: '5000万元' },
    { label: '员工规模', value: '300人' },
    { label: '项目来源', value: '自主拓展' },
  ],
  f4: [
    { label: '公司基本信息', value: '已提供 — BP.pdf' },
    { label: '工商注册资料', value: '已提供 — 工商查询' },
    { label: '财务报表（近3年）', value: '已提供 — 财报.pdf' },
    { label: '专利证书', value: '已提供 — 12项专利清单' },
    { label: '重大合同', value: '待补充' },
    { label: '诉讼/仲裁信息', value: '无' },
  ],
  f6: [
    { label: '公司A 估值', value: '2.5亿元 (P/E 15x)' },
    { label: '公司B 估值', value: '1.8亿元 (P/E 12x)' },
    { label: '公司C 估值', value: '3.2亿元 (P/E 18x)' },
    { label: '行业平均', value: 'P/E 14.5x' },
    { label: '目标公司估值区间', value: '2.0-2.8亿元' },
  ],
}

type ViewMode = 'list' | 'filling' | 'preview'

interface FormsTabProps {
  projectType: ProjectType
  currentStage: ProjectStage
}

export function FormsTab({ projectType, currentStage: _currentStage }: FormsTabProps) {
  const { t } = useTranslation()
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [selectedTemplate, setSelectedTemplate] = useState<typeof MOCK_FORM_TEMPLATES[0] | null>(null)
  const [fillProgress, setFillProgress] = useState(0)
  const [fillFields, setFillFields] = useState<{ label: string; value: string }[]>([])
  const [previewForm, setPreviewForm] = useState<typeof MOCK_FORMS[0] | null>(null)
  const templatesRef = useRef<HTMLDivElement>(null)

  const availableTemplates = projectType === 'general'
    ? MOCK_FORM_TEMPLATES
    : MOCK_FORM_TEMPLATES.filter(f => f.type === projectType || f.type === 'general')

  const handleSelectTemplate = (tpl: typeof MOCK_FORM_TEMPLATES[0]) => {
    setSelectedTemplate(tpl)
    setViewMode('filling')
    setFillProgress(0)
    setFillFields([])
  }

  // Mock fill progress animation
  useEffect(() => {
    if (viewMode !== 'filling' || !selectedTemplate) return
    if (fillProgress >= 100) {
      // Fill complete — populate fields
      const fields = MOCK_FIELDS[selectedTemplate.id] || [
        { label: '字段1', value: '自动提取值1' },
        { label: '字段2', value: '自动提取值2' },
        { label: '字段3', value: '自动提取值3' },
      ]
      setFillFields(fields.map(f => ({ ...f })))
      return
    }
    const timer = setTimeout(() => {
      setFillProgress(prev => Math.min(prev + 10, 100))
    }, 200)
    return () => clearTimeout(timer)
  }, [viewMode, selectedTemplate, fillProgress])

  const handleFieldChange = (idx: number, newValue: string) => {
    setFillFields(prev => prev.map((f, i) => i === idx ? { ...f, value: newValue } : f))
  }

  const handleExport = () => {
    const name = selectedTemplate?.name || previewForm?.templateName || '表格'
    alert(t('project.forms.exportSuccess', { name }))
    if (viewMode !== 'list') setViewMode('list')
  }

  const handleOpenForm = (form: typeof MOCK_FORMS[0]) => {
    setPreviewForm(form)
    const fields = MOCK_FIELDS[Object.keys(MOCK_FIELDS).find(k => MOCK_FORM_TEMPLATES.find(t => t.name === form.templateName)?.id === k) || ''] || [
      { label: '字段1', value: '已填充值1' },
      { label: '字段2', value: '已填充值2' },
    ]
    setFillFields(fields.map(f => ({ ...f })))
    setViewMode('preview')
  }

  // ── Filling view ──
  if (viewMode === 'filling' && selectedTemplate) {
    return (
      <div className={s.container}>
        <div className={s.header}>
          <button className={s.backBtn} onClick={() => { setViewMode('list'); setSelectedTemplate(null) }}>
            <ArrowLeft size={14} />
            {t('project.forms.back')}
          </button>
          <span className={s.headerTitle}>{selectedTemplate.name}</span>
        </div>
        <div className={s.fillContent}>
          {fillProgress < 100 ? (
            <div className={s.fillProgress}>
              <Loader2 size={24} className={s.spinIcon} />
              <p className={s.fillStatus}>{t('project.forms.extracting')}</p>
              <div className={s.progressBar}>
                <div className={s.progressFill} style={{ width: `${fillProgress}%` }} />
              </div>
              <p className={s.progressText}>{fillProgress}%</p>
            </div>
          ) : (
            <div className={s.fillPreview}>
              <p className={s.fillComplete}>{t('project.forms.fillComplete')}</p>
              <div className={s.fieldList}>
                {fillFields.map((field, idx) => (
                  <div key={idx} className={s.fieldRow}>
                    <label className={s.fieldLabel}>{field.label}</label>
                    <input
                      className={s.fieldInput}
                      value={field.value}
                      onChange={(e) => handleFieldChange(idx, e.target.value)}
                    />
                  </div>
                ))}
              </div>
              <div className={s.fillActions}>
                <button className={s.cancelBtn} onClick={() => { setViewMode('list'); setSelectedTemplate(null) }}>
                  {t('project.forms.cancel')}
                </button>
                <button className={s.exportBtn} onClick={handleExport}>
                  <Download size={14} />
                  {t('project.forms.confirmExport')}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── Preview view ──
  if (viewMode === 'preview' && previewForm) {
    return (
      <div className={s.container}>
        <div className={s.header}>
          <button className={s.backBtn} onClick={() => { setViewMode('list'); setPreviewForm(null) }}>
            <ArrowLeft size={14} />
            {t('project.forms.back')}
          </button>
          <span className={s.headerTitle}>{previewForm.title}</span>
        </div>
        <div className={s.fillContent}>
          <div className={s.fillPreview}>
            <div className={s.fieldList}>
              {fillFields.map((field, idx) => (
                <div key={idx} className={s.fieldRow}>
                  <label className={s.fieldLabel}>{field.label}</label>
                  <input
                    className={s.fieldInput}
                    value={field.value}
                    onChange={(e) => handleFieldChange(idx, e.target.value)}
                  />
                </div>
              ))}
            </div>
            <div className={s.fillActions}>
              <button className={s.cancelBtn} onClick={() => { setViewMode('list'); setPreviewForm(null) }}>
                {t('project.forms.cancel')}
              </button>
              <button className={s.exportBtn} onClick={handleExport}>
                <Download size={14} />
                {t('project.forms.export')}
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── List view (default) ──
  return (
    <div className={s.container}>
      {/* Header */}
      <div className={s.header}>
        <button className={s.primaryBtn} onClick={() => {
          if (viewMode !== 'list') {
            setViewMode('list')
            setSelectedTemplate(null)
            setPreviewForm(null)
          } else {
            templatesRef.current?.scrollIntoView({ behavior: 'smooth' })
          }
        }}>
          <Plus size={16} />
          {t('project.forms.newForm')}
        </button>
      </div>

      {/* Available templates */}
      <div className={s.section} ref={templatesRef}>
        <h4 className={s.sectionTitle}>{t('project.forms.availableTemplates')}</h4>
        <div className={s.templateList}>
          {availableTemplates.map((tpl) => (
            <div key={tpl.id} className={s.templateChip} onClick={() => handleSelectTemplate(tpl)}>
              <FileSpreadsheet size={14} className={s.templateIcon} />
              <span className={s.templateName}>{tpl.name}</span>
              <span className={s.templateFormat}>{tpl.format}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Form instances */}
      <div className={s.section}>
        <h4 className={s.sectionTitle}>{t('project.forms.myForms')}</h4>
        <div className={s.formList}>
          {MOCK_FORMS.map((form) => (
            <div key={form.id} className={s.formCard}>
              <div className={s.formIcon}>
                <Table size={18} />
              </div>
              <div className={s.formInfo}>
                <div className={s.formTitle}>{form.title}</div>
                <div className={s.formMeta}>
                  <span>{form.templateName}</span>
                  <span className={s.dot}>·</span>
                  <span>{form.format}</span>
                  <span className={s.dot}>·</span>
                  <span>{form.createdAt}</span>
                </div>
              </div>
              <div className={s.formStatus}>
                {form.status === 'completed' && (
                  <span className={s.statusCompleted}>
                    <CheckCircle size={14} /> {t('project.forms.completed')}
                  </span>
                )}
                {form.status === 'filling' && (
                  <span className={s.statusFilling}>
                    <Clock size={14} /> {t('project.forms.filling')}
                  </span>
                )}
              </div>
              <div className={s.formActions}>
                <button className={s.actionBtn} onClick={() => handleOpenForm(form)}>
                  {t('project.forms.open')}
                </button>
                <button className={s.actionBtn} onClick={() => { setPreviewForm(form); handleExport() }}>
                  {t('project.forms.export')}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
