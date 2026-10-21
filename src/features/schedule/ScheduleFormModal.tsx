/** src/features/schedule/ScheduleFormModal.tsx — 新建/编辑订阅弹层向导
 *  （2.11 §9.2 规则 9，E3 T7）。三渠道入口归一：
 *  ①来源（从模板占位符表单化 / 自由输入）；②周期（daily 时刻 / weekly 星期多选
 *  +时刻 / monthly 日期多选+时刻，intervalValue 与后端 _WEEKDAYS 约定一致）；
 *  ③去向+保存（不沉淀/项目/基础库，列表源复用 E2 ImportTargetDialog 通道）。
 *  initial.taskId（人工任务「设为定时」）→ refinePrompt 预填并跳过①；
 *  editing（编辑态）→ 跳过①，统一 prompt+占位符回填。 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'
import { useTaskTemplates } from '../task/useTaskTemplates'
import { hasUnfilledPlaceholders, uniquePlaceholderNames, applyValues } from '../task/promptTemplate'
import type { ScheduleRow } from './useSchedules'
import s from './ScheduleFormModal.module.css'

/** ISO 周一~周日（1-7）→ intervalValue 星期缩写（与后端 scheduler._WEEKDAYS 一致） */
export const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'] as const
const MONTH_DAYS = Array.from({ length: 31 }, (_, i) => i + 1)

export function serializeInterval(
  type: 'daily' | 'weekly' | 'monthly', days: number[], time: string,
): string {
  if (type === 'daily') return time
  const ds = [...days].sort((a, b) => a - b)
  if (type === 'weekly') return `${ds.map((d) => WEEKDAYS[d - 1]).join(',')} ${time}`
  return `${ds.join(',')} ${time}`
}

export function parseInterval(
  type: 'daily' | 'weekly' | 'monthly', value: string,
): { days: number[]; time: string } {
  if (type === 'daily') return { days: [], time: value }
  const [ds, time = '09:00'] = value.split(' ')
  if (type === 'weekly') {
    return { days: ds.split(',').map((x) => WEEKDAYS.indexOf(x as never) + 1).filter((n) => n > 0), time }
  }
  return { days: ds.split(',').map(Number).filter((n) => n >= 1 && n <= 31), time }
}

interface ScheduleFormModalProps {
  open: boolean
  /** 渠道③：人工任务「设为定时」→ refinePrompt 提炼预填（跳过第①步） */
  initial?: { taskId?: string }
  /** 编辑态：跳过第①步，prompt+配置回填 */
  editing?: ScheduleRow | null
  onClose: () => void
  onSaved: () => void
}

type Step = 'source' | 'period' | 'target'
type SourceTab = 'template' | 'free'
type IntervalType = 'daily' | 'weekly' | 'monthly'

export function ScheduleFormModal({ open, initial, editing, onClose, onSaved }: ScheduleFormModalProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const { templates } = useTaskTemplates('global')

  const [step, setStep] = useState<Step>('source')
  const [sourceTab, setSourceTab] = useState<SourceTab>('template')
  const [tplId, setTplId] = useState<string | null>(null)
  const [phValues, setPhValues] = useState<Record<string, string>>({})
  const [freePrompt, setFreePrompt] = useState('')
  const [refining, setRefining] = useState(false)

  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [category, setCategory] = useState('corporate')
  const [intervalType, setIntervalType] = useState<IntervalType>('daily')
  const [weekDays, setWeekDays] = useState<number[]>([])
  const [monthDays, setMonthDays] = useState<number[]>([])
  const [time, setTime] = useState('09:00')

  const [targetType, setTargetType] = useState<'none' | 'project' | 'baselib'>('none')
  const [targetId, setTargetId] = useState<string | null>(null)
  const [targets, setTargets] = useState<{ id: string; name: string }[]>([])
  const [autoImport, setAutoImport] = useState(true)
  const [saving, setSaving] = useState(false)
  const refineStarted = useRef(false)

  // 打开初始化：编辑态回填 / refine 流程 / 新建清空
  useEffect(() => {
    if (!open) return
    refineStarted.current = false
    if (editing) {
      const parsed = parseInterval(editing.interval_type, editing.interval_value)
      setName(editing.name)
      setPrompt(editing.prompt)
      setCategory(editing.category)
      setIntervalType(editing.interval_type)
      setWeekDays(parsed.days)
      setMonthDays(parsed.days)
      setTime(parsed.time)
      setTargetType(editing.target_type ?? 'none')
      setTargetId(editing.target_id)
      setAutoImport(!!editing.auto_import)
      setStep('period')
      return
    }
    setStep(initial?.taskId ? 'period' : 'source')
    setSourceTab('template')
    setTplId(null)
    setPhValues({})
    setFreePrompt('')
    setName('')
    setPrompt('')
    setCategory('corporate')
    setIntervalType('daily')
    setWeekDays([])
    setMonthDays([])
    setTime('09:00')
    setTargetType('none')
    setTargetId(null)
    setAutoImport(true)
  }, [open, editing, initial?.taskId])

  // 渠道③：refinePrompt 提炼 → 预填跳过第①步；失败 toast 停在自由输入
  useEffect(() => {
    if (!open || !initial?.taskId || editing || refineStarted.current) return
    refineStarted.current = true
    setRefining(true)
    window.electronAPI.invoke('schedules:refinePrompt', { taskId: initial.taskId })
      .then((res) => {
        const r = res as { name?: string; prompt?: string }
        setName(r?.name ?? '')
        setPrompt(r?.prompt ?? '')
        setStep('period')
      })
      .catch((err) => {
        console.error('Refine prompt failed:', err)
        showToast(apiErrorText(
          (err as { errorCode?: string })?.errorCode,
          t('schedule.form.refineFailed'), t), 'error')
        setStep('source')
        setSourceTab('free')
      })
      .finally(() => setRefining(false))
  }, [open, initial?.taskId, editing, showToast, t])

  const loadTargets = useCallback((kind: 'project' | 'baselib') => {
    const channel = kind === 'project' ? 'knowledge:listProjects' : 'baseLib:list'
    // 选择器形态：limit=0 全量（目标下拉不得截断）
    window.electronAPI.invoke(channel, { limit: 0 })
      .then((res) => {
        const rows = kind === 'project'
          ? (res as { projects?: { id: string; name: string }[] })?.projects ?? []
          : (res as { base_libs?: { id: string; name: string }[] })?.base_libs ?? []
        setTargets(rows)
      })
      .catch((err) => console.error('ScheduleFormModal: load targets failed:', err))
  }, [])

  useEffect(() => {
    if (step === 'target' && targetType !== 'none' && targets.length === 0) {
      loadTargets(targetType)
    }
  }, [step, targetType, targets.length, loadTargets])

  if (!open) return null

  const selectedTpl = templates.find((m) => m.id === tplId) ?? null
  // 占位符按名去重（0.5 §4.4）：同一变量在模板中出现多次只渲染一个输入框，
  // applyValues 替换时覆盖全部出现位置
  const phNames = selectedTpl ? uniquePlaceholderNames(selectedTpl.prompt) : []
  const templatePrompt = selectedTpl ? applyValues(selectedTpl.prompt, phValues) : ''
  const sourcePrompt = sourceTab === 'template' ? templatePrompt : freePrompt

  const step1Ready = sourceTab === 'template'
    ? !!selectedTpl && !hasUnfilledPlaceholders(templatePrompt)
    : !!freePrompt.trim()
  const days = intervalType === 'weekly' ? weekDays : monthDays
  const step2Ready = time !== '' && (intervalType === 'daily' || days.length > 0)
  const step3Ready = targetType === 'none' || !!targetId

  const handleSave = async () => {
    if (!step3Ready || saving) return
    const intervalValue = serializeInterval(intervalType, days, time)
    const payload = {
      name: name.trim() || null,
      prompt: prompt || sourcePrompt,
      category,
      intervalType,
      intervalValue,
      targetType: targetType === 'none' ? null : targetType,
      targetId: targetType === 'none' ? null : targetId,
      autoImport: targetType === 'none' ? false : autoImport,
    }
    setSaving(true)
    try {
      if (editing) {
        await window.electronAPI.invoke('schedules:update', { id: editing.id, ...payload })
      } else {
        await window.electronAPI.invoke('schedules:create', payload)
      }
      showToast(t('schedule.form.saved'), 'success')
      onSaved()
      onClose()
    } catch (err) {
      console.error('Save schedule failed:', err)
      showToast(apiErrorText(
        (err as { errorCode?: string })?.errorCode,
        t('schedule.form.saveFailed'), t), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={s.backdrop} onClick={onClose}>
      <div className={s.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className={s.header}>
          <h3 className={s.title}>
            {t(editing ? 'schedule.form.editTitle' : 'schedule.form.newTitle')}
          </h3>
          <button className={s.iconBtn} aria-label={t('common.close')} onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        {refining && <div className={s.refining}>{t('schedule.form.refining')}</div>}

        {step === 'source' && (
          <div className={s.body}>
            <div className={s.tabs} role="tablist">
              <button role="tab" aria-selected={sourceTab === 'template'}
                className={`${s.tab} ${sourceTab === 'template' ? s.tabActive : ''}`}
                onClick={() => setSourceTab('template')}>
                {t('schedule.form.fromTemplate')}
              </button>
              <button role="tab" aria-selected={sourceTab === 'free'}
                className={`${s.tab} ${sourceTab === 'free' ? s.tabActive : ''}`}
                onClick={() => setSourceTab('free')}>
                {t('schedule.form.freeInput')}
              </button>
            </div>
            {sourceTab === 'template' && (
              <div className={s.tplList}>
                {templates.map((m) => (
                  <button key={m.id}
                    className={`${s.tplItem} ${tplId === m.id ? s.tplActive : ''}`}
                    onClick={() => { setTplId(m.id); setPhValues({}) }}>
                    {m.title}
                  </button>
                ))}
              </div>
            )}
            {sourceTab === 'template' && selectedTpl && phNames.length > 0 && (
              <div className={s.phForm}>
                {phNames.map((ph) => (
                  <label key={ph} className={s.phRow}>
                    <span>{t(`schedule.ph.${ph}`, { defaultValue: ph })}</span>
                    <input
                      aria-label={t(`schedule.ph.${ph}`, { defaultValue: ph })}
                      value={phValues[ph] ?? ''}
                      onChange={(e) => setPhValues((prev) => ({ ...prev, [ph]: e.target.value }))}
                    />
                  </label>
                ))}
              </div>
            )}
            {sourceTab === 'free' && (
              <textarea
                className={s.promptArea}
                aria-label={t('schedule.form.promptLabel')}
                placeholder={t('schedule.form.promptPlaceholder')}
                value={freePrompt}
                onChange={(e) => setFreePrompt(e.target.value)}
              />
            )}
            <div className={s.actions}>
              <span />
              <button className={s.primaryBtn} disabled={!step1Ready} onClick={() => setStep('period')}>
                {t('schedule.form.next')}
              </button>
            </div>
          </div>
        )}

        {step === 'period' && (
          <div className={s.body}>
            <div className={s.fieldLabel}>{t('schedule.form.stepPeriod')}</div>
            <div className={s.tabs} role="tablist">
              {(['daily', 'weekly', 'monthly'] as const).map((tp) => (
                <button key={tp} role="tab" aria-selected={intervalType === tp}
                  className={`${s.tab} ${intervalType === tp ? s.tabActive : ''}`}
                  onClick={() => setIntervalType(tp)}>
                  {t(`schedule.period.${tp}`)}
                </button>
              ))}
            </div>
            {intervalType === 'weekly' && (
              <div className={s.dayRow}>
                {WEEKDAYS.map((wd, i) => (
                  <label key={wd} className={s.dayChip}>
                    <input
                      type="checkbox"
                      aria-label={t(`schedule.wd.${wd}`)}
                      checked={weekDays.includes(i + 1)}
                      onChange={(e) => setWeekDays((prev) =>
                        e.target.checked ? [...prev, i + 1] : prev.filter((d) => d !== i + 1))}
                    />
                    {t(`schedule.wd.${wd}`)}
                  </label>
                ))}
              </div>
            )}
            {intervalType === 'monthly' && (
              <div className={s.monthGrid}>
                {MONTH_DAYS.map((d) => (
                  <label key={d} className={s.dayChip}>
                    <input
                      type="checkbox"
                      aria-label={t('schedule.md', { day: d })}
                      checked={monthDays.includes(d)}
                      onChange={(e) => setMonthDays((prev) =>
                        e.target.checked ? [...prev, d] : prev.filter((x) => x !== d))}
                    />
                    {d}
                  </label>
                ))}
              </div>
            )}
            <label className={s.phRow}>
              <span>{t('schedule.form.timeLabel')}</span>
              <input
                aria-label={t('schedule.form.timeLabel')}
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
              />
            </label>
            <div className={s.actions}>
              {editing ? <span /> : (
                <button className={s.secondaryBtn} onClick={() => setStep('source')}>
                  {t('schedule.form.prev')}
                </button>
              )}
              <button className={s.primaryBtn} disabled={!step2Ready} onClick={() => setStep('target')}>
                {t('schedule.form.next')}
              </button>
            </div>
          </div>
        )}

        {step === 'target' && (
          <div className={s.body}>
            <div className={s.fieldLabel}>{t('schedule.form.stepTarget')}</div>
            <label className={s.phRow}>
              <span>{t('schedule.form.nameLabel')}</span>
              <input
                aria-label={t('schedule.form.nameLabel')}
                value={name}
                placeholder={t('schedule.form.namePlaceholder')}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className={s.phRow}>
              <span>{t('schedule.form.categoryLabel')}</span>
              <select
                aria-label={t('schedule.form.categoryLabel')}
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="industry">{t('schedule.tab.industry')}</option>
                <option value="corporate">{t('schedule.tab.corporate')}</option>
                <option value="general">{t('schedule.tab.general')}</option>
              </select>
            </label>
            <div className={s.radioCol} role="radiogroup" aria-label={t('schedule.form.stepTarget')}>
              {(['none', 'project', 'baselib'] as const).map((tp) => (
                <label key={tp} className={s.radioRow}>
                  <input
                    type="radio"
                    name="targetType"
                    checked={targetType === tp}
                    onChange={() => { setTargetType(tp); setTargetId(null); setTargets([]) }}
                  />
                  {t(`schedule.form.target.${tp}`)}
                </label>
              ))}
            </div>
            {targetType !== 'none' && (
              <div className={s.targetList}>
                {targets.length === 0 ? (
                  <div className={s.empty}>{t('schedule.form.loadingTargets')}</div>
                ) : targets.map((item) => (
                  <button key={item.id}
                    className={`${s.tplItem} ${targetId === item.id ? s.tplActive : ''}`}
                    onClick={() => setTargetId(item.id)}>
                    {item.name}
                  </button>
                ))}
                <label className={s.autoImportRow}>
                  <input
                    type="checkbox"
                    checked={autoImport}
                    onChange={(e) => setAutoImport(e.target.checked)}
                  />
                  {t('schedule.form.autoImport')}
                </label>
              </div>
            )}
            <div className={s.actions}>
              <button className={s.secondaryBtn} onClick={() => setStep('period')}>
                {t('schedule.form.prev')}
              </button>
              <button className={s.primaryBtn} disabled={!step3Ready || saving} onClick={handleSave}>
                {saving ? t('schedule.form.saving') : t('schedule.form.save')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
