/** src/features/project/affairs/TaskFormModal.tsx — 任务新建/编辑表单(3.0 §6.1.6/6.1.7)
 *  新建:归当前时期(服务端 POST 按项目 stage 推导,表单不出现时期字段);
 *  编辑:时期可改(PATCH 白名单 phase),类型禁改(白名单无 task_type)。
 *  recurring 类型切换为节奏字段(频率 + due_day:1-31|end|MM-DD),隐藏截止日;
 *  完成匹配词(§6.1.7):资料/周期事务均可自动完成(file_parsed),新建与编辑均可设,
 *  清空=移除自动完成(2026-10-22 放开手动新建资料任务入口,消除「模板任务能自动
 *  完成、手动任务不能」的不对称);
 *  模板带入的 auto_complete 不可改场景原样保留回传(实例与模板解耦,§6.4-1)。
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ProjectStage } from '../types'
import type { PhaseKey, PhaseTask, PhaseTaskType, RecurrenceRule } from './types'
import s from './TaskFormModal.module.css'

export interface TaskFormModalProps {
  projectId: string
  /** 新建默认时期来源(=详情页当前 stage;POST 服务端亦按项目 stage 推导归期,二者一致) */
  stage: ProjectStage
  /** null=新建;PhaseTask=编辑预填(父级 AffairsTab 以 undefined 控制不渲染) */
  initial: PhaseTask | null
  /** 日历点格预填截止日(Task 9 → AffairsTab → 本组件) */
  prefillDueDate?: string | null
  onClose: () => void
  onSaved: () => void
}

const TASK_TYPES: PhaseTaskType[] = ['material', 'report', 'milestone', 'recurring']
const FREQS: RecurrenceRule['freq'][] = ['monthly', 'quarterly', 'yearly']
const PHASES: PhaseKey[] = ['before', 'during', 'after']

export function TaskFormModal({ projectId, stage, initial, prefillDueDate, onClose, onSaved }: TaskFormModalProps) {
  const { t } = useTranslation()
  const [title, setTitle] = useState(initial?.title ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [taskType, setTaskType] = useState<PhaseTaskType>(initial?.task_type ?? 'material')
  const [phase, setPhase] = useState<PhaseKey>(initial?.phase ?? (stage !== 'none' ? stage : 'before'))
  const [dueDate, setDueDate] = useState(initial?.due_date?.slice(0, 10) ?? prefillDueDate ?? '')
  const isRecurring = taskType === 'recurring'
  const [freq, setFreq] = useState<RecurrenceRule['freq']>(initial?.recurrence_rule?.freq ?? 'monthly')
  const [dueDay, setDueDay] = useState<string>(
    initial?.recurrence_rule ? String(initial.recurrence_rule.due_day) : '15')
  // 完成匹配词(§6.1.7,2026-10-22 放开 material):资料/周期事务均走 file_parsed
  // 闭环,新建与编辑均可设;清空保存 = 移除自动完成规则(null,退回手动完成);
  // report_completed 规则(报告类)不在表单展示、原样透传(§6.4-1)
  const showMatch = isRecurring || taskType === 'material'
  const [matchInput, setMatchInput] = useState(initial?.auto_complete?.match ?? '')
  const [dueDayError, setDueDayError] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const [saving, setSaving] = useState(false)

  // 与后端 parse_rule 同口径的 freq 联动校验:monthly/quarterly 取 1-31 数字
  // (quarterly 可 end),yearly 取 MM-DD(月 1-12/日 1-31)。伪日期(02-30)
  // 交后端权威拒绝并经 saveError 反馈,避免表单重复实现真实日历校验造成双处漂移
  const parseDueDay = (freq: RecurrenceRule['freq'], raw: string): number | 'end' | string | null => {
    const v = raw.trim()
    if (freq === 'yearly') {
      const m = /^(\d{2})-(\d{2})$/.exec(v)
      if (!m) return null
      const mo = Number(m[1]), d = Number(m[2])
      return mo >= 1 && mo <= 12 && d >= 1 && d <= 31 ? v : null
    }
    if (freq === 'quarterly' && v === 'end') return 'end'
    if (/^\d{1,2}$/.test(v)) {
      const n = Number(v)
      return n >= 1 && n <= 31 ? n : null
    }
    return null
  }

  const submit = async () => {
    if (!title.trim() || saving) return
    let recurrenceRule: RecurrenceRule | null = null
    let due: string | null = dueDate || null
    // 自动完成规则(§6.1.7):showMatch 场景按输入落 file_parsed(清空=移除);
    // 切类型即清空残留输入(type onChange),隐藏场景不产生新规则
    const match = matchInput.trim()
    const autoComplete: PhaseTask['auto_complete'] = initial
      ? (showMatch ? (match ? { on: 'file_parsed', match } : null) : initial.auto_complete)
      : (showMatch && match ? { on: 'file_parsed', match } : null)
    if (isRecurring) {
      const day = parseDueDay(freq, dueDay)
      if (day == null) { setDueDayError(true); return }
      setDueDayError(false)
      recurrenceRule = { freq, due_day: day }
      due = null
    }
    setSaving(true)
    setSaveError(false)
    try {
      if (initial) {
        await window.electronAPI.invoke('affairs:updateTask', {
          projectId, taskId: initial.id,
          title: title.trim(), description,
          // 编辑 recurring 不回传新 due(表单无此字段)保留原值——roll 只补缺失
          // occ 不重算已存在实例,清空会致当期实例从日历/逾期口径消失
          dueDate: isRecurring ? initial.due_date : due,
          phase,
          // 非 recurring 但带 rule 的种子(report+rule,§3.3,RhythmCards 调整
          // 节奏的唯一在线编辑入口)原样透传,与 auto_complete 同策略(§6.4-1);
          // 回传 null 会抹规则致周期任务永久停止滚动
          recurrenceRule: isRecurring ? recurrenceRule : initial.recurrence_rule,
          autoComplete,
        })
      } else {
        await window.electronAPI.invoke('affairs:createTask', {
          projectId, title: title.trim(), taskType, description,
          dueDate: due, recurrenceRule, autoComplete,
        })
      }
      onSaved()
      onClose()
    } catch (err) {
      console.error('Failed to save task:', err)
      setSaveError(true)   // 弹窗保持打开底部显错;失败不得静默(对齐 loadFailed 原则)
    } finally {
      setSaving(false)
    }
  }

  // 点蒙板不关闭（表单模态防丢输入，0.3 §5.2），仅取消按钮/保存流程关闭
  return (
    <div className={s.overlay}>
      <div className={s.modal} onClick={(e) => e.stopPropagation()}>
        <h3 className={s.title}>{initial ? t('affairs.form.editTitle') : t('affairs.form.newTitle')}</h3>
        <input className={s.input} placeholder={t('affairs.form.titleLabel')}
          value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        <textarea className={s.textarea} placeholder={t('affairs.form.descLabel')}
          value={description} onChange={(e) => setDescription(e.target.value)} />
        <div className={s.fieldRow}>
          <label className={s.label}>{t('affairs.form.typeLabel')}</label>
          <select className={s.input} value={taskType} disabled={!!initial}
            onChange={(e) => { setTaskType(e.target.value as PhaseTaskType); setDueDayError(false); setMatchInput('') }}>
            {TASK_TYPES.map((tt) => <option key={tt} value={tt}>{t(`affairs.type.${tt}`)}</option>)}
          </select>
        </div>
        {initial && (
          <div className={s.fieldRow}>
            <label className={s.label}>{t('affairs.form.phaseLabel')}</label>
            <select className={s.input} value={phase}
              onChange={(e) => setPhase(e.target.value as PhaseKey)}>
              {PHASES.map((p) => <option key={p} value={p}>{t(`project.stage.${p}`)}</option>)}
            </select>
          </div>
        )}
        {isRecurring ? (
          <div className={s.fieldRow}>
            <label className={s.label}>{t('affairs.form.rhythmLabel')}</label>
            <select className={s.freqSelect} value={freq}
              onChange={(e) => { setFreq(e.target.value as RecurrenceRule['freq']); setDueDayError(false) }}>
              {FREQS.map((f) => <option key={f} value={f}>{t(`affairs.freq${f[0].toUpperCase()}${f.slice(1)}`)}</option>)}
            </select>
            <input className={s.dayInput} value={dueDay}
              onChange={(e) => { setDueDay(e.target.value); setDueDayError(false) }}
              placeholder={t('affairs.form.dueDayPlaceholder')} />
          </div>
        ) : (
          <div className={s.fieldRow}>
            <label className={s.label}>{t('affairs.form.dueLabel')}</label>
            <input type="date" className={s.input} value={dueDate}
              onChange={(e) => setDueDate(e.target.value)} />
          </div>
        )}
        {showMatch && (
          <div className={s.fieldRow}>
            <label className={s.label}>{t('affairs.form.matchLabel')}</label>
            <input className={s.input} value={matchInput}
              onChange={(e) => setMatchInput(e.target.value)}
              placeholder={t('affairs.form.matchPlaceholder')} />
          </div>
        )}
        {dueDayError && <p className={s.error}>{t('affairs.form.dueDayInvalid')}</p>}
        {saveError && <p className={s.error}>{t('affairs.form.saveFailed')}</p>}
        <div className={s.actions}>
          <button className={s.btnCancel} disabled={saving} onClick={() => { if (!saving) onClose() }}>{t('affairs.form.cancel')}</button>
          <button className={s.btnCreate} disabled={!title.trim() || saving}
            onClick={() => void submit()}>
            {saving ? t('affairs.form.saving') : t('affairs.form.save')}
          </button>
        </div>
      </div>
    </div>
  )
}
