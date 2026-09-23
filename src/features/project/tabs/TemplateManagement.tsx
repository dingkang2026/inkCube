/** src/features/project/tabs/TemplateManagement.tsx — Template management (mock, UI-First) */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Copy, Eye, Trash2, Download, Upload, Plus, FileText } from 'lucide-react'
import s from './TemplateManagement.module.css'

interface TemplateItem {
  id: string
  name: string
  category: string
  chapters: number
  format: string
  custom: boolean
}

const PRESET_TEMPLATES: TemplateItem[] = [
  { id: 'pt1', name: '尽职调查报告', category: '尽调类', chapters: 9, format: 'Word', custom: false },
  { id: 'pt2', name: '企业调研报告', category: '尽调类', chapters: 8, format: 'Word', custom: false },
  { id: 'pt3', name: '行业研究报告', category: '行业研究', chapters: 7, format: 'Word', custom: false },
  { id: 'pt4', name: '招商推介方案', category: '招商方案', chapters: 8, format: 'PPT', custom: false },
  { id: 'pt5', name: '项目评估报告', category: '招商方案', chapters: 6, format: 'Word', custom: false },
  { id: 'pt6', name: '投后管理报告', category: '投后管理', chapters: 7, format: 'Word', custom: false },
  { id: 'pt7', name: '竞争格局分析', category: '行业研究', chapters: 5, format: 'Word', custom: false },
  { id: 'pt8', name: '落地跟踪报告', category: '投后管理', chapters: 5, format: 'Word', custom: false },
]

interface TemplateManagementProps {
  onBack: () => void
}

export function TemplateManagement({ onBack }: TemplateManagementProps) {
  const { t } = useTranslation()
  const [customTemplates, setCustomTemplates] = useState<TemplateItem[]>([])
  const [viewingTemplate, setViewingTemplate] = useState<TemplateItem | null>(null)

  const handleCopyTemplate = (tpl: TemplateItem) => {
    const newTpl: TemplateItem = {
      ...tpl,
      id: `ct-${Date.now()}`,
      name: `${tpl.name} (副本)`,
      custom: true,
    }
    setCustomTemplates(prev => [...prev, newTpl])
  }

  const handleCreateCustom = () => {
    const name = prompt(t('project.template.enterName'))
    if (!name?.trim()) return
    const newTpl: TemplateItem = {
      id: `ct-${Date.now()}`,
      name: name.trim(),
      category: '自定义',
      chapters: 0,
      format: 'Word',
      custom: true,
    }
    setCustomTemplates(prev => [...prev, newTpl])
  }

  const handleDeleteCustom = (id: string) => {
    setCustomTemplates(prev => prev.filter(t => t.id !== id))
  }

  const handleImport = () => {
    alert(t('project.template.importMock'))
  }

  const handleExport = (tpl: TemplateItem) => {
    alert(t('project.template.exportMock', { name: tpl.name }))
  }

  const handleView = (tpl: TemplateItem) => {
    setViewingTemplate(tpl)
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
            <h4>{viewingTemplate.name}</h4>
          </div>
          <div className={s.detailContent}>
            <p className={s.detailMeta}>
              {t('project.template.category')}: {viewingTemplate.category} | {viewingTemplate.chapters} {t('project.report.chapters')} | {viewingTemplate.format}
            </p>
            <div className={s.chapterPreview}>
              <p className={s.detailSectionTitle}>{t('project.template.chapterStructure')}:</p>
              {Array.from({ length: viewingTemplate.chapters || 3 }, (_, i) => (
                <div key={i} className={s.mockChapter}>
                  <span>{i + 1}. {t(`project.template.sampleChapter${(i % 3) + 1}`)}</span>
                </div>
              ))}
              {viewingTemplate.chapters === 0 && (
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
            <div className={s.templateList}>
              {PRESET_TEMPLATES.map((tpl) => (
                <div key={tpl.id} className={s.templateRow}>
                  <FileText size={16} className={s.rowIcon} />
                  <div className={s.rowInfo}>
                    <span className={s.rowName}>{tpl.name}</span>
                    <span className={s.rowMeta}>{tpl.category} | {tpl.chapters} {t('project.report.chapters')} | {tpl.format}</span>
                  </div>
                  <div className={s.rowActions}>
                    <button className={s.actionBtn} onClick={() => handleView(tpl)} title={t('project.template.view')}>
                      <Eye size={14} />
                    </button>
                    <button className={s.actionBtn} onClick={() => handleCopyTemplate(tpl)} title={t('project.template.copy')}>
                      <Copy size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Custom templates */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.template.customTemplates')}</div>
            <div className={s.templateList}>
              {customTemplates.length === 0 ? (
                <div className={s.emptyCustom}>{t('project.template.noCustom')}</div>
              ) : (
                customTemplates.map((tpl) => (
                  <div key={tpl.id} className={s.templateRow}>
                    <FileText size={16} className={s.rowIcon} />
                    <div className={s.rowInfo}>
                      <span className={s.rowName}>{tpl.name}</span>
                      <span className={s.rowMeta}>{tpl.category} | {tpl.chapters} {t('project.report.chapters')} | {tpl.format}</span>
                    </div>
                    <div className={s.rowActions}>
                      <button className={s.actionBtn} onClick={() => handleView(tpl)} title={t('project.template.view')}>
                        <Eye size={14} />
                      </button>
                      <button className={s.actionBtn} onClick={() => handleExport(tpl)} title={t('project.template.export')}>
                        <Download size={14} />
                      </button>
                      <button className={s.actionBtnDanger} onClick={() => handleDeleteCustom(tpl.id)} title={t('project.template.delete')}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
            <div className={s.customActions}>
              <button className={s.createBtn} onClick={handleCreateCustom}>
                <Plus size={14} />
                {t('project.template.createCustom')}
              </button>
              <button className={s.importBtn} onClick={handleImport}>
                <Upload size={14} />
                {t('project.template.importTemplate')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
