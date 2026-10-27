/** src/features/project/tabs/PptTemplateWizard.tsx — Create Template 向导（2.3 §4.2）。
 *  类型四选（brand/style/layout/deck）→ 显示名（slug 自动生成可改）→ 参考素材多选
 *  （A pptx / B svg / C 图片 / D 文档，dialog:openFile multiSelections）→ 补充要求
 *  → ppt-template:create。任务由 ppt_service 泵驱动 Create Template 工作流，
 *  brief 文字门在 PptTemplatePanel JobCard 就地应答（此处只负责发起）。
 *  覆盖层形态复用 TemplateManagement.module.css 抽屉视觉。
 */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, X } from 'lucide-react'
import s from './TemplateManagement.module.css'
import { useDrawerWidth } from '../../../hooks/useDrawerWidth'
import { useToast } from '../../knowledge/Toast'
import { apiErrorText } from '../../../i18n/errorCodes'

const KINDS = ['brand', 'style', 'layout', 'deck'] as const
type Kind = (typeof KINDS)[number]

/** 抽屉宽度下限（默认 60vw 随视口、上限 92vw 随视口，见 useDrawerWidth；与 CSS min-width 兜底同值） */
const DRAWER_MIN_DETAIL = 460

/** 显示名 → slug：小写化、空白转连字符、剔除路径非法字符（中文保留，索引 key 允许） */
function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[\\/:*?"<>|#%&{}$!'@+`=]+/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

interface PptTemplateWizardProps {
  onCreated: () => void
  onClose: () => void
}

export function PptTemplateWizard({ onCreated, onClose }: PptTemplateWizardProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [kind, setKind] = useState<Kind | null>(null)
  const [name, setName] = useState('')
  const [templateId, setTemplateId] = useState('')
  const idEditedRef = useRef(false)
  const [refPaths, setRefPaths] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  // 抽屉宽度/拖宽/防误关守卫统一契约（2026-10-26 由 CSS 固定 46% 不可拖改统一契约）
  const dr = useDrawerWidth({ min: DRAWER_MIN_DETAIL })

  const handleNameChange = (v: string) => {
    setName(v)
    if (!idEditedRef.current) setTemplateId(slugify(v))
  }

  const handleAddRefs = async () => {
    const result: any = await window.electronAPI.invoke('dialog:openFile', {
      filters: [
        { name: 'Reference Files', extensions: ['pptx', 'svg', 'png', 'jpg', 'jpeg', 'webp', 'pdf', 'md', 'docx', 'html', 'htm'] },
      ],
      properties: ['openFile', 'multiSelections'],
    })
    if (!result || result.canceled || !result.filePaths?.length) return
    setRefPaths((prev) => {
      const next = [...prev]
      for (const p of result.filePaths as string[]) if (!next.includes(p)) next.push(p)
      return next
    })
  }

  const handleSubmit = async () => {
    if (!kind || !templateId || !name.trim() || submitting) return
    setSubmitting(true)
    try {
      const result: any = await window.electronAPI.invoke('ppt-template:create', {
        kind,
        templateId,
        templateName: name.trim(),
        refPaths,
        notes: notes.trim() || null,
      })
      if (result?.ppt?.id) {
        showToast(t('project.pptTpl.wizard.created'), 'success')
        onCreated()
      } else if (result?.error) {
        showToast(t('project.pptTpl.wizard.createFailed', {
          error: apiErrorText(result.code, result.error, t),
        }), 'error')
      }
    } catch (err: any) {
      showToast(t('project.pptTpl.wizard.createFailed', { error: String(err?.message || err) }), 'error')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <div className={s.drawerBackdrop} onClick={() => { if (!dr.suppressClick.current) onClose() }} />
      <div className={s.drawer} style={{ width: dr.width }} onMouseDown={dr.onPanelMouseDown}>
        <div
          className={`${s.dragHandle} ${dr.dragging ? s.dragHandleActive : ''}`}
          onMouseDown={dr.onHandleMouseDown}
        />
        <div className={s.drawerHeader}>
          <h4>{t('project.pptTpl.wizard.title')}</h4>
          <button className={s.closeBtn} onClick={onClose} title={t('common.close')}>×</button>
        </div>
        <div className={s.drawerBody}>
          <p className={s.pptWizardHint}>{t('project.pptTpl.wizard.hint')}</p>

          {/* 类型四选 */}
          <div className={s.detailBlock}>
            <p className={s.detailSectionTitle}>{t('project.pptTpl.wizard.kindLabel')}</p>
            <div className={s.pptTplKindGrid}>
              {KINDS.map((k) => (
                <button
                  key={k}
                  type="button"
                  className={`${s.pptTplKindCard} ${kind === k ? s.pptTplKindActive : ''}`}
                  onClick={() => setKind(k)}
                >
                  <span className={s.pptTplKindName}>{t(`project.pptTpl.kind.${k}`)}</span>
                  <span className={s.pptTplKindDesc}>{t(`project.pptTpl.wizard.kindDesc.${k}`)}</span>
                </button>
              ))}
            </div>
          </div>

          {/* 显示名 / 模板 id */}
          <label className={s.pptWizardField}>
            {t('project.pptTpl.wizard.nameLabel')}
            <input
              className={s.pptWizardInput}
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              placeholder={t('project.pptTpl.wizard.namePlaceholder')}
              autoFocus
            />
          </label>
          <label className={s.pptWizardField}>
            {t('project.pptTpl.wizard.idLabel')}
            <input
              className={s.pptWizardInput}
              value={templateId}
              onChange={(e) => { idEditedRef.current = true; setTemplateId(e.target.value) }}
              placeholder={t('project.pptTpl.wizard.idPlaceholder')}
            />
            <span className={s.pptWizardFieldHint}>{t('project.pptTpl.wizard.idHint')}</span>
          </label>

          {/* 参考素材 */}
          <div className={s.detailBlock}>
            <p className={s.detailSectionTitle}>{t('project.pptTpl.wizard.refsLabel')}</p>
            <p className={s.pptWizardFieldHint}>{t('project.pptTpl.wizard.refsHint')}</p>
            <button className={s.actionBtn} onClick={() => void handleAddRefs()}>
              <Plus size={14} /> {t('project.pptTpl.wizard.addRefs')}
            </button>
            {refPaths.length === 0 ? (
              <p className={s.emptyState}>{t('project.pptTpl.wizard.refsEmpty')}</p>
            ) : (
              <div className={s.pptWizardRefs}>
                {refPaths.map((p) => (
                  <span key={p} className={s.pptWizardRef}>
                    <span className={s.pptWizardRefName} title={p}>{p.replace(/^.*[\\/]/, '')}</span>
                    <button
                      className={s.pptWizardRefRemove}
                      onClick={() => setRefPaths((prev) => prev.filter((x) => x !== p))}
                      title={t('common.delete')}
                    ><X size={12} /></button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* 补充要求 */}
          <label className={s.pptWizardField}>
            {t('project.pptTpl.wizard.notesLabel')}
            <textarea
              className={`${s.pptWizardInput} ${s.pptWizardTextarea}`}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={t('project.pptTpl.wizard.notesPlaceholder')}
              rows={3}
            />
          </label>
        </div>

        <div className={s.detailActions}>
          <button className={s.actionBtn} onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </button>
          <button
            className={s.primaryBtn}
            onClick={handleSubmit}
            disabled={!kind || !name.trim() || !templateId || submitting}
          >
            {submitting
              ? <><Loader2 size={14} className={s.spinIcon} /> {t('project.pptTpl.wizard.submitting')}</>
              : t('project.pptTpl.wizard.submit')}
          </button>
        </div>
      </div>
    </>
  )
}
