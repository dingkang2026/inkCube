/** src/features/project/tabs/PptWizard.tsx — PPT 创建向导（2.3 §7.1 PptCreateDialog）
 *  表单：标题 / 内容源报告 / 图片来源复合选择（分类圈池 + 高清阈值 + 钉选）/
 *  模板选择器（四类各一下拉，双源条目，每类至多一个；E2 库外置）/
 *  风格文字备注 / 补充要求。
 *  提交 → POST /projects/{pid}/ppts（后端建 origin='ppt' 任务并启动泵）。
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Check, Loader2 } from 'lucide-react'
import s from './ppt.module.css'
import { useToast } from '../../knowledge/Toast'
import { apiErrorText } from '../../../i18n/errorCodes'

interface ReportRow { id: string; title: string }
interface ImageRow {
  id: string
  local_path?: string
  vlm_description?: string | null
  category?: string | null
}
interface TplRow { id: string; summary?: string | null; builtin?: boolean }

/** 模板四类（ppt-master 原生定义，每类至多选一个；选择标识 kind:id） */
const TEMPLATE_KINDS = ['brand', 'style', 'layout', 'deck'] as const

/** 钉选图保证进候选池的阈值上限（与后端 _IMAGE_POOL_CAP 对齐，仅作提示展示） */
const IMAGE_POOL_CAP = 120

/** 本地图片 → inkcube:// 协议 URL（与 ImageGallery.localFileUrl 同规则，CSP img-src 已放行） */
function localFileUrl(filePath: string): string {
  return `inkcube://app?file=${encodeURIComponent(filePath.replace(/\\/g, '/'))}`
}

interface PptWizardProps {
  projectId: string
  onCreated: (pptId: string) => void
  onBack: () => void
}

export function PptWizard({ projectId, onCreated, onBack }: PptWizardProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [title, setTitle] = useState('')
  const [reports, setReports] = useState<ReportRow[]>([])
  const [sourceReportId, setSourceReportId] = useState('')
  const [images, setImages] = useState<ImageRow[]>([])
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({})
  const [selectedCats, setSelectedCats] = useState<Set<string>>(new Set())
  const [hdOnly, setHdOnly] = useState(false)
  const [pinnedImages, setPinnedImages] = useState<Set<string>>(new Set())
  const [templates, setTemplates] = useState<Record<string, TplRow[]>>({})
  const [tplByKind, setTplByKind] = useState<Record<string, string>>({})
  const [styleRef, setStyleRef] = useState('')
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // 内容源：仅取已完成报告（generating/error 无稳定正文，不作内容源）
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        // 选择器形态：limit=0 全量（分页不得截断内容源下拉）
        const result: any = await window.electronAPI.invoke('reports:listReports', { projectId, limit: 0 })
        const done = (result.reports || []).filter((r: any) => r.status === 'done')
        if (!cancelled) setReports(done)
      } catch (err) {
        console.error('Failed to load reports for ppt wizard:', err)
      }
    })()
    return () => { cancelled = true }
  }, [projectId])

  // 配图：项目图片库已完成描述的图（服务端 status=done 过滤）+ 分类计数（圈池 chips 数据源）
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const result: any = await window.electronAPI.invoke('knowledge:getProjectImages', {
          projectId, limit: 200, status: 'done',
        })
        if (cancelled) return
        setImages(result.images || [])
        setCategoryCounts(result.category_counts || {})
      } catch (err) {
        console.error('Failed to load project images for ppt wizard:', err)
      }
    })()
    return () => { cancelled = true }
  }, [projectId])

  // 模板选择器数据源：双源列表（内置带 builtin 标记 + 用户库，E2 库外置）
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const result: any = await window.electronAPI.invoke('ppt-template:list')
        if (!cancelled) setTemplates(result?.templates || {})
      } catch (err) {
        console.error('Failed to load ppt templates for wizard:', err)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const toggleCat = (cat: string) => {
    setSelectedCats((prev) => {
      const next = new Set(prev)
      if (next.has(cat)) next.delete(cat)
      else next.add(cat)
      return next
    })
  }

  const togglePin = (id: string) => {
    setPinnedImages((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSubmit = async () => {
    if (!title.trim() || submitting) return
    setSubmitting(true)
    // 复合选择（2.3 §4.1 E2）：钉选全收 + 分类/高清圈池；三者全空 = 不带图
    const hasSelection = pinnedImages.size > 0 || selectedCats.size > 0 || hdOnly
    // 模板选择标识串 kind:id（后端解析为 explicit root 契约写入任务消息；
    // 全不选 = 由 Stage 1 内置候选自主决定，默认行为不变）
    const templateRefs = TEMPLATE_KINDS
      .map((k) => (tplByKind[k] ? `${k}:${tplByKind[k]}` : ''))
      .filter(Boolean)
      .join(',') || null
    try {
      const result: any = await window.electronAPI.invoke('ppt:create', {
        projectId,
        title: title.trim(),
        sourceReportId: sourceReportId || null,
        imageSelection: hasSelection
          ? {
              categories: [...selectedCats],
              minWidth: hdOnly ? 800 : null,
              pinnedIds: [...pinnedImages],
            }
          : null,
        templateRefs,
        styleRef: styleRef.trim() || null,
        notes: notes.trim() || null,
      })
      if (result?.ppt?.id) {
        showToast(t('project.ppt.toast.created'), 'success')
        onCreated(result.ppt.id)
      } else if (result?.error) {
        showToast(t('project.ppt.toast.createFailed', {
          error: apiErrorText(result.code, result.error, t),
        }), 'error')
      }
    } catch (err: any) {
      showToast(t('project.ppt.toast.createFailed', { error: String(err?.message || err) }), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={s.wizard}>
      <div className={s.wizardHeader}>
        <button className={s.backBtn} onClick={onBack}>
          <ArrowLeft size={16} /> {t('project.ppt.wizard.back')}
        </button>
        <h3 className={s.wizardTitle}>{t('project.ppt.wizard.title')}</h3>
      </div>

      <div className={s.form}>
        <label className={s.formLabel}>
          {t('project.ppt.wizard.titleLabel')}
          <input
            className={s.formInput}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t('project.ppt.wizard.titlePlaceholder')}
            autoFocus
          />
        </label>

        <label className={s.formLabel}>
          {t('project.ppt.wizard.sourceLabel')}
          <select
            className={s.formInput}
            value={sourceReportId}
            onChange={(e) => setSourceReportId(e.target.value)}
          >
            <option value="">{t('project.ppt.wizard.sourceNone')}</option>
            {reports.map((r) => (
              <option key={r.id} value={r.id}>{r.title}</option>
            ))}
          </select>
        </label>

        <div className={s.formLabel}>
          {t('project.ppt.wizard.imagesLabel')}
          {images.length === 0 ? null : (
            <>
              <p className={s.formHint}>{t('project.ppt.wizard.imagesHint', { cap: IMAGE_POOL_CAP })}</p>
              {Object.keys(categoryCounts).length > 0 && (
                <>
                  <p className={s.formHint}>{t('project.ppt.wizard.catsLabel')}</p>
                  <div className={s.chipRow}>
                    {Object.entries(categoryCounts)
                      .filter(([, n]) => n > 0)
                      .map(([cat, n]) => (
                        <button
                          key={cat}
                          className={`${s.chip} ${selectedCats.has(cat) ? s.chipActive : ''}`}
                          onClick={() => toggleCat(cat)}
                        >
                          {t(`knowledge.category_${cat}`)} ({n})
                        </button>
                      ))}
                  </div>
                </>
              )}
              <label className={s.hdToggle}>
                <input
                  type="checkbox"
                  checked={hdOnly}
                  onChange={(e) => setHdOnly(e.target.checked)}
                />
                {t('project.ppt.wizard.hdOnly')}
              </label>
              <div className={s.imageGrid}>
                {images.map((img) => (
                  <button
                    key={img.id}
                    className={`${s.imageCell} ${pinnedImages.has(img.id) ? s.imageCellSelected : ''}`}
                    onClick={() => togglePin(img.id)}
                    title={img.vlm_description || img.id}
                  >
                    <img className={s.imageThumb} src={img.local_path ? localFileUrl(img.local_path) : ''} alt="" />
                    <span className={s.imageCheck}>{pinnedImages.has(img.id) ? <Check size={12} /> : ''}</span>
                  </button>
                ))}
              </div>
              <p className={s.formHint}>{t('project.ppt.wizard.pinHint')}</p>
            </>
          )}
        </div>

        <div className={s.formLabel}>
          {t('project.ppt.wizard.templateLabel')}
          <p className={s.formHint}>{t('project.ppt.wizard.templateHint')}</p>
          {TEMPLATE_KINDS.map((kind) => (
            <label key={kind} className={s.tplRow}>
              <span className={s.tplKind}>{t(`project.ppt.wizard.kind_${kind}`)}</span>
              <select
                className={s.formInput}
                value={tplByKind[kind] || ''}
                onChange={(e) =>
                  setTplByKind((prev) => ({ ...prev, [kind]: e.target.value }))}
              >
                <option value="">{t('project.ppt.wizard.templateNone')}</option>
                {(templates[kind] || []).map((tp) => (
                  <option key={tp.id} value={tp.id}>
                    {tp.id} · {tp.builtin ? t('project.ppt.wizard.builtinBadge') + ' · ' : ''}
                    {(tp.summary || '').slice(0, 48)}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>

        <label className={s.formLabel}>
          {t('project.ppt.wizard.styleLabel')}
          <input
            className={s.formInput}
            value={styleRef}
            onChange={(e) => setStyleRef(e.target.value)}
            placeholder={t('project.ppt.wizard.stylePlaceholder')}
          />
        </label>

        <label className={s.formLabel}>
          {t('project.ppt.wizard.notesLabel')}
          <textarea
            className={`${s.formInput} ${s.formTextarea}`}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={t('project.ppt.wizard.notesPlaceholder')}
            rows={3}
          />
        </label>
      </div>

      <div className={s.wizardActions}>
        <button className={s.secondaryBtn} onClick={onBack}>
          {t('project.ppt.wizard.back')}
        </button>
        <button
          className={s.primaryBtn}
          onClick={handleSubmit}
          disabled={!title.trim() || submitting}
        >
          {submitting ? <><Loader2 size={14} className={s.spinning} /> {t('project.ppt.wizard.submitting')}</>
            : t('project.ppt.wizard.submit')}
        </button>
      </div>
    </div>
  )
}
