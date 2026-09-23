/** src/features/project/tabs/ReportWizard.tsx — 3-step report creation wizard (mock, UI-First) */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Search, ChevronRight, CheckCircle, Clock, Circle, GripVertical, Trash2, Plus, Factory, DollarSign } from 'lucide-react'
import type { ProjectType, ProjectStage } from '../types'
import type { MockReport } from '../types'
import s from './ReportWizard.module.css'

/* ── Mock data ── */
const TEMPLATES = [
  { id: 't1', name: '尽职调查报告', category: '尽调类', format: 'Word', chapters: 9, projectType: 'investment' as ProjectType },
  { id: 't2', name: '企业调研报告', category: '尽调类', format: 'Word', chapters: 8, projectType: 'investment' as ProjectType },
  { id: 't4', name: '招商推介方案', category: '招商方案', format: 'PPT', chapters: 8, projectType: 'recruitment' as ProjectType },
  { id: 't5', name: '项目评估报告', category: '招商方案', format: 'Word', chapters: 6, projectType: 'recruitment' as ProjectType },
  { id: 't6', name: '投后管理报告', category: '投后管理', format: 'Word', chapters: 7, projectType: 'investment' as ProjectType },
  { id: 't8', name: '落地跟踪报告', category: '投后管理', format: 'Word', chapters: 5, projectType: 'recruitment' as ProjectType },
]

/** Mock chapter outlines per template */
const MOCK_OUTLINES: Record<string, string[]> = {
  t1: ['公司概况', '核心团队', '业务模式', '财务分析', '市场分析', '竞争格局', '法律风险', '估值分析', '投资建议'],
  t2: ['企业基本信息', '发展历程', '产品与服务', '技术实力', '市场地位', '管理团队', '商业模式', '发展展望'],
  t3: ['行业概况', '市场规模与增速', '驱动因素', '竞争格局', '产业链分析', '政策环境', '趋势展望'],
  t4: ['项目概况', '企业核心优势', '产业匹配度分析', '政策支持与优惠', '园区配套与区位', '落地方案建议', '预期效益分析', '招商推介总结'],
  t5: ['项目概况', '企业实力评估', '产业契合度', '政策适用性', '风险分析', '综合评估'],
  t6: ['投后概况', '经营业绩回顾', '财务健康度', '重大事项跟踪', '风险评估', '下阶段计划', '管理建议'],
  t7: ['行业概览', '主要玩家', '市场份额', '竞争策略', '趋势预判'],
  t8: ['落地概况', '进度跟踪', '问题与解决', '下阶段计划', '总结'],
}

/** Mock knowledge sources */
const KNOWLEDGE_SOURCES = [
  { id: 'ks-project', name: 'project', nameKey: 'project.report.wizard.projectKb', autoChecked: true, autoMatched: false },
  { id: 'ks-recruitment', name: 'recruitment', nameKey: 'project.report.wizard.recruitmentLib', autoChecked: false, autoMatched: true, matchType: 'recruitment' as ProjectType },
  { id: 'ks-investment', name: 'investment', nameKey: 'project.report.wizard.investmentLib', autoChecked: false, autoMatched: true, matchType: 'investment' as ProjectType },
  { id: 'ks-custom', name: 'custom', nameKey: 'project.report.wizard.customLib', autoChecked: false, autoMatched: false },
]

/** Mock generated content per chapter */
const MOCK_CONTENT: Record<string, string> = {
  '公司概况': 'XX科技有限公司成立于2018年，注册资本5000万元，是一家专注于新材料研发与生产的高新技术企业。公司总部位于上海张江高科技园区，拥有现代化生产基地20000平方米。\n\n> 来源: BP.pdf 第3页',
  '核心团队': '公司创始人兼CEO张三，清华大学材料科学博士，拥有15年行业经验。CTO李四，曾任某上市公司技术总监，主导多项国家级科研项目。核心团队共12人，其中博士3人、硕士6人。\n\n> 来源: BP.pdf 第8页',
  'default': '根据知识库中的相关资料，本章节内容正在由 AI Agent 基于项目知识库和基础库自动生成。生成过程将自动检索相关文档并提取关键信息...\n\n> 来源: 多个知识库文档',
}

interface ReportWizardProps {
  projectType: ProjectType
  currentStage: ProjectStage
  onComplete: (report: MockReport) => void
  onCancel: () => void
}

export function ReportWizard({ projectType, currentStage: _currentStage, onComplete, onCancel }: ReportWizardProps) {
  const { t } = useTranslation()
  const [step, setStep] = useState(1)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedTemplate, setSelectedTemplate] = useState<typeof TEMPLATES[0] | null>(null)
  const [chapters, setChapters] = useState<string[]>([])
  const [checkedSources, setCheckedSources] = useState<Set<string>>(new Set())
  const [supplementaryNotes, setSupplementaryNotes] = useState('')
  const [outputFormat, setOutputFormat] = useState<'Word' | 'PPT'>('Word')

  // Step 3 state
  const [generatedChapters, setGeneratedChapters] = useState<Set<number>>(new Set())
  const [currentGenerating, setCurrentGenerating] = useState(-1)
  const [previewContent, setPreviewContent] = useState('')
  const [isComplete, setIsComplete] = useState(false)

  // Initialize knowledge sources based on project type
  useEffect(() => {
    const initial = new Set<string>()
    initial.add('ks-project') // always auto-checked
    KNOWLEDGE_SOURCES.forEach(ks => {
      if (ks.autoMatched && ks.matchType === projectType) {
        initial.add(ks.id)
      }
    })
    setCheckedSources(initial)
  }, [projectType])

  const handleSelectTemplate = useCallback((tpl: typeof TEMPLATES[0]) => {
    setSelectedTemplate(tpl)
    setChapters([...(MOCK_OUTLINES[tpl.id] || ['概述', '详细分析', '总结'])])
    setOutputFormat(tpl.format as 'Word' | 'PPT')
    setStep(2)
  }, [])

  const handleStartGenerate = useCallback(() => {
    setStep(3)
    setGeneratedChapters(new Set())
    setCurrentGenerating(0)
    setPreviewContent('')
    setIsComplete(false)
  }, [])

  // Mock generation progress
  useEffect(() => {
    if (step !== 3 || isComplete || currentGenerating < 0 || currentGenerating >= chapters.length) return

    const timer = setTimeout(() => {
      setGeneratedChapters(prev => new Set([...prev, currentGenerating]))
      const chapterName = chapters[currentGenerating]
      const content = MOCK_CONTENT[chapterName] || MOCK_CONTENT['default']
      setPreviewContent(prev => prev + `\n## ${currentGenerating + 1}. ${chapterName}\n\n${content}\n\n---\n\n`)

      if (currentGenerating + 1 >= chapters.length) {
        setCurrentGenerating(-1)
        setIsComplete(true)
      } else {
        setCurrentGenerating(currentGenerating + 1)
      }
    }, 1200)

    return () => clearTimeout(timer)
  }, [step, isComplete, currentGenerating, chapters])

  const handleFinish = useCallback(() => {
    if (!selectedTemplate) return
    const newReport: MockReport = {
      id: `r-${Date.now()}`,
      title: `${t('project.report.wizard.generatedReport')} — ${selectedTemplate.name}`,
      templateName: selectedTemplate.name,
      outputFormat: selectedTemplate.format as 'Word' | 'PPT',
      createdAt: new Date().toISOString().slice(0, 10),
      status: 'completed',
    }
    onComplete(newReport)
  }, [selectedTemplate, onComplete, t])

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

  const filteredTemplates = searchQuery
    ? TEMPLATES.filter(tpl => tpl.name.includes(searchQuery) || tpl.category.includes(searchQuery))
    : TEMPLATES

  // Group templates by category
  const grouped = filteredTemplates.reduce<Record<string, typeof TEMPLATES>>((acc, tpl) => {
    (acc[tpl.category] ||= []).push(tpl)
    return acc
  }, {})

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

          {Object.entries(grouped).map(([category, tpls]) => (
            <div key={category} className={s.categoryGroup}>
              <div className={s.categoryTitle}>{category}</div>
              <div className={s.templateGrid}>
                {tpls.map((tpl) => (
                  <div key={tpl.id} className={s.templateCard} onClick={() => handleSelectTemplate(tpl)}>
                    <div className={s.templateName}>{tpl.name}</div>
                    <div className={s.templateMeta}>
                      <span>{tpl.projectType === 'investment' ? <DollarSign size={12} /> : <Factory size={12} />}</span>
                      <span>{tpl.chapters} {t('project.report.chapters')}</span>
                      <span>{tpl.format}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className={s.categoryGroup}>
            <div className={s.categoryTitle}>{t('project.report.wizard.customTemplates')}</div>
            <div className={s.templateGrid}>
              <div className={`${s.templateCard} ${s.templateCardAdd}`}>
                <Plus size={20} />
                <span>{t('project.report.wizard.createTemplate')}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Step 2: Adjust outline + knowledge sources */}
      {step === 2 && selectedTemplate && (
        <div className={s.stepContent}>
          <div className={s.stepRow}>
            <span className={s.stepLabel}>{t('project.report.wizard.template')}</span>
            <span className={s.stepValue}>{selectedTemplate.name}</span>
            <span className={s.stepLabel}>{t('project.report.wizard.outputFormat')}</span>
            <select className={s.formatSelect} value={outputFormat} onChange={(e) => setOutputFormat(e.target.value as 'Word' | 'PPT')}>
              <option value="Word">Word</option>
              <option value="PPT">PPT</option>
            </select>
          </div>

          {/* Knowledge sources */}
          <div className={s.section}>
            <div className={s.sectionTitle}>{t('project.report.wizard.knowledgeSources')}</div>
            {KNOWLEDGE_SOURCES.map((ks) => (
              <label key={ks.id} className={s.checkboxRow}>
                <input
                  type="checkbox"
                  checked={checkedSources.has(ks.id)}
                  onChange={() => toggleSource(ks.id)}
                  disabled={ks.autoChecked}
                />
                <span className={s.checkboxLabel}>{t(ks.nameKey)}</span>
                {ks.autoChecked && <span className={s.autoTag}>{t('project.report.wizard.autoIncluded')}</span>}
                {ks.autoMatched && ks.matchType === projectType && !ks.autoChecked && (
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
