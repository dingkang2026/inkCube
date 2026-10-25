/**
 * LeadPool（0.6 §6.1/§6.2/§6.3 V2-E2）：情报中心线索池子 Tab。
 * 数据源 = GET /leads（limit=0 全量，分组视图豁免，PM 裁决）；四活动列
 * （新线索/接触中/评估中/搁置）+ 已立项置灰区（点击跳项目）。
 * 卡片动作（薄卡不变量 §6.3-8：只有名称/来源/状态/备注/立项指向）：
 * - 状态推进：pill 点击即 PATCH leads:update（当前态禁用）
 * - 备注：inline 编辑 blur 保存（值有变才发请求）
 * - 立项：内联模态（类型三选 + 名称预填）→ onConvertProject（App 层
 *   knowledge:createProject，失败 toast 留原页可重试）→ 成功后回填
 *   projectId + converted；回填失败只提示不重试（项目已建，重试会重复立项）
 * - 升级订阅：onUpgrade(lead) → App 级 ScheduleFormModal prefill（零新通道）
 * - 删除：leads:delete（裸 id 参数契约）
 * 空态引导：「从情报期次存一条线索试试」→ 去情报流 + 手动录入。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Repeat, Rocket, Target, X } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import { useConfirm } from '../../components/ConfirmDialog'
import type { ProjectType } from '../project/types'
import s from './LeadPool.module.css'

export interface LeadRow {
  id: string
  name: string
  source_type: 'run' | 'manual'
  source_run_id: string | null
  source_schedule_name: string
  status: 'new' | 'contacting' | 'evaluating' | 'hold' | 'converted'
  note: string
  project_id: string | null
  created_at: string
  updated_at: string
}

const ACTIVE_STATUSES = ['new', 'contacting', 'evaluating', 'hold'] as const
const CONVERT_TYPES: Array<{ value: ProjectType; labelKey: string }> = [
  { value: 'general', labelKey: 'intel.lead.convert.typeGeneral' },
  { value: 'recruitment', labelKey: 'intel.lead.convert.typeRecruit' },
  { value: 'investment', labelKey: 'intel.lead.convert.typeInvest' },
]

interface LeadPoolProps {
  /** 立项 = App 层 knowledge:createProject（失败返回 undefined 并自行 toast） */
  onConvertProject: (name: string, type: ProjectType) => Promise<{ id?: string } | undefined>
  /** 已立项卡「查看项目」→ App 切项目页 */
  onOpenProject: (projectId: string) => void
  /** 升级订阅 → App 级 ScheduleFormModal（盯梢 prompt 预填，零新通道） */
  onUpgrade: (lead: LeadRow) => void
  /** 空态「去情报流看看」→ 情报流子 Tab */
  onGoFeed: () => void
}

export function LeadPool({ onConvertProject, onOpenProject, onUpgrade, onGoFeed }: LeadPoolProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()
  const [leads, setLeads] = useState<LeadRow[]>([])
  const [loaded, setLoaded] = useState(false)
  // 首拉失败标记（静默 catch 排查 2026-10-24）：失败 → 错误+重试，不得落到
  // 空列结构伪装「零线索」；重拉失败（loaded=true）保留现有内容静默
  const [loadFailed, setLoadFailed] = useState(false)
  const loadSeq = useRef(0)

  // 立项内联模态
  const [convertTarget, setConvertTarget] = useState<LeadRow | null>(null)
  const [convertName, setConvertName] = useState('')
  const [convertType, setConvertType] = useState<ProjectType>('general')
  const [converting, setConverting] = useState(false)
  // 手动录入模态
  const [manualOpen, setManualOpen] = useState(false)
  const [manualName, setManualName] = useState('')
  const [manualNote, setManualNote] = useState('')
  const [manualSaving, setManualSaving] = useState(false)

  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const res = await window.electronAPI.invoke('leads:list', { limit: 0 }) as { leads?: LeadRow[] }
      if (seq !== loadSeq.current) return
      setLeads(res.leads ?? [])
      setLoaded(true)
      setLoadFailed(false)
    } catch {
      // 失败不覆盖现有内容；仅未加载过（loaded=false）时升级错误态
      setLoadFailed(true)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const changeStatus = useCallback(async (lead: LeadRow, status: LeadRow['status']) => {
    try {
      await window.electronAPI.invoke('leads:update', { id: lead.id, status })
      await load()
    } catch {
      showToast(t('intel.lead.opFailed'), 'error')
    }
  }, [load, showToast, t])

  const saveNote = useCallback(async (leadId: string, note: string) => {
    try {
      await window.electronAPI.invoke('leads:update', { id: leadId, note })
      await load()
    } catch {
      showToast(t('intel.lead.opFailed'), 'error')
    }
  }, [load, showToast, t])

  // 删除二次确认（CodeReview 建议 2026-10-24 采纳）：不可逆破坏性操作，与
  // 项目删除同一确认层；线索重建成本虽低，误触 X 即永久删不可接受
  const handleDelete = useCallback(async (lead: LeadRow) => {
    if (!(await confirmDialog({
      title: t('intel.lead.deleteLabel'),
      message: t('intel.lead.confirmDeleteLead', { name: lead.name }),
    }))) return
    try {
      await window.electronAPI.invoke('leads:delete', lead.id)
      await load()
    } catch {
      showToast(t('intel.lead.opFailed'), 'error')
    }
  }, [confirmDialog, load, showToast, t])

  const openConvert = (lead: LeadRow) => {
    setConvertTarget(lead)
    setConvertName(lead.name)
    setConvertType('general')
    setConverting(false)
  }

  // 立项：建项目成功 → 回填 projectId + converted（失败不推进，模态保留可重试）；
  // 回填失败仅提示并关闭（项目已建，重试会重复立项——0.6 §6.2 立项失败语义）
  const handleConvert = async () => {
    if (!convertTarget || converting) return
    setConverting(true)
    let created: { id?: string } | undefined
    try {
      created = await onConvertProject(convertName.trim() || convertTarget.name, convertType)
    } catch {
      created = undefined
    }
    if (!created?.id) {
      setConverting(false)
      return
    }
    try {
      await window.electronAPI.invoke('leads:update', {
        id: convertTarget.id, projectId: created.id, status: 'converted',
      })
      showToast(t('intel.lead.convert.success'), 'success')
    } catch {
      showToast(t('intel.lead.convert.patchFailed'), 'error')
    }
    setConverting(false)
    setConvertTarget(null)
    void load()
  }

  const handleManualCreate = async () => {
    const name = manualName.trim()
    if (!name || manualSaving) return
    setManualSaving(true)
    try {
      await window.electronAPI.invoke('leads:create', {
        name, sourceType: 'manual', note: manualNote,
      })
      showToast(t('intel.lead.manualAdded'), 'success')
      setManualOpen(false)
      setManualName('')
      setManualNote('')
      await load()
    } catch {
      showToast(t('intel.lead.opFailed'), 'error')
    } finally {
      setManualSaving(false)
    }
  }

  const relTime = (iso: string) => {
    const minutes = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
    const hours = Math.floor(minutes / 60)
    return minutes < 1 ? t('activity.time.justNow')
      : minutes < 60 ? t('activity.time.minutesAgo', { count: minutes })
      : hours < 24 ? t('activity.time.hoursAgo', { count: hours })
      : t('activity.time.daysAgo', { count: Math.floor(hours / 24) })
  }

  const sourceLabel = (lead: LeadRow) =>
    lead.source_type === 'run'
      ? (lead.source_schedule_name || t('intel.tab.feed'))
      : t('intel.lead.sourceManual')

  // 首拉失败态（静默 catch 排查 2026-10-24）：错误+重试，区别于真实零线索引导态
  if (loadFailed && !loaded) {
    return (
      <div className={s.wrap} data-testid="lead-pool">
        <div className={s.empty} data-testid="lead-error">
          <Target size={32} className={s.emptyIcon} />
          <p className={s.emptyText}>{t('intel.lead.loadFailed')}</p>
          <button type="button" className={s.emptyBtn} onClick={() => void load()}>
            {t('knowledge.retry')}
          </button>
        </div>
      </div>
    )
  }

  // 零线索空态（0.6 §6.2）：引导从情报期次沉淀 + 手动录入兜底
  if (loaded && leads.length === 0) {
    return (
      <div className={s.wrap} data-testid="lead-pool">
        <div className={s.empty} data-testid="lead-empty">
          <Target size={32} className={s.emptyIcon} />
          <p className={s.emptyText}>{t('intel.lead.empty')}</p>
          <div className={s.emptyActions}>
            <button type="button" className={s.emptyBtn} onClick={onGoFeed}>
              {t('intel.lead.emptyGoFeed')}
            </button>
            <button type="button" className={s.ghostBtn} data-testid="lead-add-manual" onClick={() => setManualOpen(true)}>
              <Plus size={14} />{t('intel.lead.addManual')}
            </button>
          </div>
        </div>
        {manualOpen && renderManualModal()}
      </div>
    )
  }

  const groups = ACTIVE_STATUSES.map((st) => ({ st, items: leads.filter((l) => l.status === st) }))
  const convertedLeads = leads.filter((l) => l.status === 'converted')

  return (
    <div className={s.wrap} data-testid="lead-pool">
      <div className={s.poolActions}>
        <button type="button" className={s.ghostBtn} data-testid="lead-add-manual" onClick={() => setManualOpen(true)}>
          <Plus size={14} />{t('intel.lead.addManual')}
        </button>
      </div>
      <div className={s.columns}>
        {groups.map(({ st, items }) => (
          <div key={st} className={s.col} data-testid={`lead-col-${st}`}>
            <div className={s.colTitle}>
              {t(`intel.lead.status.${st}`)}
              <span className={s.colCount}>{items.length}</span>
            </div>
            <div className={s.colCards}>
              {items.map((lead) => (
                <div key={lead.id} className={s.card} data-testid="lead-card">
                  <div className={s.cardHead}>
                    <span className={s.cardName}>{lead.name}</span>
                    <button
                      type="button"
                      className={s.delBtn}
                      data-testid="lead-delete"
                      aria-label={t('intel.lead.deleteLabel')}
                      onClick={() => void handleDelete(lead)}
                    >
                      <X size={12} />
                    </button>
                  </div>
                  <div className={s.cardMeta}>
                    <span className={s.srcChip}>{sourceLabel(lead)}</span>
                    <span className={s.cardTime}>{relTime(lead.updated_at)}</span>
                  </div>
                  <div className={s.pills} role="group" aria-label={t('intel.lead.statusLabel')}>
                    {ACTIVE_STATUSES.map((st2) => (
                      <button
                        key={st2}
                        type="button"
                        data-testid="lead-pill"
                        className={`${s.pill} ${lead.status === st2 ? s.pillActive : ''}`}
                        disabled={lead.status === st2}
                        onClick={() => void changeStatus(lead, st2)}
                      >
                        {t(`intel.lead.status.${st2}`)}
                      </button>
                    ))}
                  </div>
                  <NoteEdit lead={lead} onSave={saveNote} />
                  <div className={s.cardActions}>
                    <button
                      type="button"
                      className={s.primaryAction}
                      data-testid="lead-convert"
                      onClick={() => openConvert(lead)}
                    >
                      <Rocket size={12} />{t('intel.lead.convert.action')}
                    </button>
                    <button
                      type="button"
                      className={s.minorAction}
                      data-testid="lead-upgrade"
                      onClick={() => onUpgrade(lead)}
                    >
                      <Repeat size={12} />{t('intel.lead.upgrade')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {convertedLeads.length > 0 && (
        <div className={s.convertedZone} data-testid="lead-converted-zone">
          <div className={s.colTitle}>
            {t('intel.lead.groupConverted')}
            <span className={s.colCount}>{convertedLeads.length}</span>
          </div>
          <div className={s.colCards}>
            {convertedLeads.map((lead) => (
              <div key={lead.id} className={`${s.card} ${s.cardConverted}`} data-testid="lead-card-converted">
                <div className={s.cardHead}>
                  <span className={s.cardName}>{lead.name}</span>
                  <button
                    type="button"
                    className={s.delBtn}
                    data-testid="lead-delete"
                    aria-label={t('intel.lead.deleteLabel')}
                    onClick={() => void handleDelete(lead)}
                  >
                    <X size={12} />
                  </button>
                </div>
                <div className={s.cardMeta}>
                  <span className={s.srcChip}>{sourceLabel(lead)}</span>
                  <span className={s.cardTime}>{relTime(lead.updated_at)}</span>
                </div>
                {lead.project_id && (
                  <button
                    type="button"
                    className={s.minorAction}
                    data-testid="lead-open-project"
                    onClick={() => onOpenProject(lead.project_id!)}
                  >
                    {t('intel.lead.viewProject')}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {convertTarget && (
        <div className={s.backdrop} onClick={() => { if (!converting) setConvertTarget(null) }}>
          <div className={s.dialog} role="dialog" aria-modal="true" data-testid="lead-convert-dialog" onClick={(e) => e.stopPropagation()}>
            <div className={s.dialogHead}>
              <h3 className={s.dialogTitle}>{t('intel.lead.convert.title')}</h3>
              <button type="button" className={s.delBtn} aria-label={t('common.close')} onClick={() => setConvertTarget(null)}>
                <X size={14} />
              </button>
            </div>
            <label className={s.field}>
              <span>{t('intel.lead.convert.nameLabel')}</span>
              <input
                data-testid="lead-convert-name"
                value={convertName}
                onChange={(e) => setConvertName(e.target.value)}
              />
            </label>
            <div className={s.fieldLabel}>{t('intel.lead.convert.typeLabel')}</div>
            <div className={s.typeRow} role="radiogroup" aria-label={t('intel.lead.convert.typeLabel')}>
              {CONVERT_TYPES.map((tp) => (
                <button
                  key={tp.value}
                  type="button"
                  role="radio"
                  aria-checked={convertType === tp.value}
                  className={`${s.pill} ${convertType === tp.value ? s.pillActive : ''}`}
                  onClick={() => setConvertType(tp.value)}
                >
                  {t(tp.labelKey)}
                </button>
              ))}
            </div>
            <div className={s.dialogActions}>
              <button type="button" className={s.ghostBtn} onClick={() => setConvertTarget(null)}>
                {t('common.cancel')}
              </button>
              <button
                type="button"
                className={s.primaryBtn}
                data-testid="lead-convert-confirm"
                disabled={converting}
                onClick={() => void handleConvert()}
              >
                {converting ? t('intel.lead.convert.converting') : t('intel.lead.convert.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}

      {manualOpen && renderManualModal()}
    </div>
  )

  function renderManualModal() {
    return (
      <div className={s.backdrop} onClick={() => { if (!manualSaving) setManualOpen(false) }}>
        <div className={s.dialog} role="dialog" aria-modal="true" data-testid="lead-manual-dialog" onClick={(e) => e.stopPropagation()}>
          <div className={s.dialogHead}>
            <h3 className={s.dialogTitle}>{t('intel.lead.manualTitle')}</h3>
            <button type="button" className={s.delBtn} aria-label={t('common.close')} onClick={() => setManualOpen(false)}>
              <X size={14} />
            </button>
          </div>
          <label className={s.field}>
            <span>{t('intel.lead.nameLabel')}</span>
            <input
              data-testid="lead-manual-name"
              value={manualName}
              placeholder={t('intel.lead.namePlaceholder')}
              onChange={(e) => setManualName(e.target.value)}
            />
          </label>
          <label className={s.field}>
            <span>{t('intel.lead.noteLabel')}</span>
            <textarea
              data-testid="lead-manual-note"
              value={manualNote}
              placeholder={t('intel.lead.notePlaceholder')}
              onChange={(e) => setManualNote(e.target.value)}
            />
          </label>
          <div className={s.dialogActions}>
            <button type="button" className={s.ghostBtn} onClick={() => setManualOpen(false)}>
              {t('common.cancel')}
            </button>
            <button
              type="button"
              className={s.primaryBtn}
              data-testid="lead-manual-confirm"
              disabled={!manualName.trim() || manualSaving}
              onClick={() => void handleManualCreate()}
            >
              {t('intel.lead.addManual')}
            </button>
          </div>
        </div>
      </div>
    )
  }
}

/** 备注 inline 编辑（薄卡字段）：点击进入编辑，blur 时值有变才 PATCH */
function NoteEdit({ lead, onSave }: { lead: LeadRow; onSave: (leadId: string, note: string) => Promise<void> }) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState('')

  if (!editing) {
    return (
      <button
        type="button"
        className={`${s.noteBtn} ${lead.note ? '' : s.noteEmpty}`}
        data-testid="lead-note"
        onClick={() => { setVal(lead.note); setEditing(true) }}
      >
        {lead.note || t('intel.lead.notePlaceholder')}
      </button>
    )
  }
  return (
    <textarea
      className={s.noteArea}
      data-testid="lead-note-input"
      autoFocus
      value={val}
      onChange={(e) => setVal(e.target.value)}
      onBlur={() => {
        setEditing(false)
        if (val !== lead.note) void onSave(lead.id, val)
      }}
    />
  )
}
