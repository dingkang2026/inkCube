/** src/features/schedule/ScheduleFormModal.tsx — 新建/编辑订阅弹层向导
 *  （2.11 §9.2 规则 9，E3 T7；V2.1-T2 订阅定位三型 2.11 §12）。两步流程：
 *  ①来源（从模板占位符表单化 / 自由输入）；②周期（daily 时刻 / weekly 星期多选
 *  +时刻 / monthly 日期多选+时刻，intervalValue 与后端 _WEEKDAYS 约定一致）；
 *  ③定位+去向+保存：scope 三选（盯梢/撒网/组合周报）——选组合周报时本步切
 *  汇总骨架编辑（预填默认模板含 {sections}）+ 汇总范围多选（默认全量），
 *  隐藏去向区（rollup 不沉淀）；其余 scope 保持去向配置。editing（编辑态）
 *  → 跳过①，统一 prompt+配置回填。 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'
import { useTaskTemplates } from '../task/useTaskTemplates'
import {
  hasUnfilledPlaceholders, uniquePlaceholderNames, applyValues,
  INTEL_TEMPLATES, INTEL_CATEGORY_MAP, sortTemplatesByPersona,
} from '../task/promptTemplate'
import type { ScheduleRow } from './useSchedules'
import { useSettings } from '../settings/useSettings'
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
  /** 编辑态：跳过第①步，prompt+配置回填 */
  editing?: ScheduleRow | null
  /** 新建态预填（0.6 §6.1 V2-E2 线索升级订阅）：名称进订阅名、
   *  prompt 进自由输入（并切到自由输入 tab）；编辑态忽略（editing 走 update，
   *  两者不可混用） */
  prefill?: { name?: string; prompt?: string }
  onClose: () => void
  onSaved: () => void
}

type Step = 'source' | 'period' | 'target'
type SourceTab = 'template' | 'free'
type IntervalType = 'daily' | 'weekly' | 'monthly'
type Scope = 'focus' | 'sweep' | 'rollup'
const SCOPES: Scope[] = ['focus', 'sweep', 'rollup']

export function ScheduleFormModal({ open, editing, prefill, onClose, onSaved }: ScheduleFormModalProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const { settings } = useSettings()
  // T12 模板分家：第①步模板列表 = 订阅白名单 INTEL_TEMPLATES + 自定义模板
  const { templates } = useTaskTemplates('global', undefined, INTEL_TEMPLATES)

  const [step, setStep] = useState<Step>('source')
  const [sourceTab, setSourceTab] = useState<SourceTab>('template')
  const [tplId, setTplId] = useState<string | null>(null)
  const [phValues, setPhValues] = useState<Record<string, string>>({})
  const [freePrompt, setFreePrompt] = useState('')

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

  // 订阅定位（2.11 §12）：rollup 时第③步切骨架+汇总范围，去向区隐藏
  const [scope, setScope] = useState<Scope>('focus')
  const [rollupPrompt, setRollupPrompt] = useState('')
  const [digestIds, setDigestIds] = useState<string[]>([])
  const [digestSources, setDigestSources] = useState<{ id: string; name: string }[]>([])

  // 打开初始化：编辑态回填 / 新建清空
  useEffect(() => {
    if (!open) return
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
      setScope(editing.scope)
      setRollupPrompt(editing.scope === 'rollup' ? editing.prompt : '')
      setDigestIds(editing.digest_schedule_ids ?? [])
      setStep('period')
      return
    }
    setStep('source')
    setSourceTab(prefill?.prompt ? 'free' : 'template')
    setTplId(null)
    setPhValues({})
    setFreePrompt(prefill?.prompt ?? '')
    setName(prefill?.name ?? '')
    setPrompt('')
    setCategory('corporate')
    setIntervalType('daily')
    setWeekDays([])
    setMonthDays([])
    setTime('09:00')
    setTargetType('none')
    setTargetId(null)
    setAutoImport(true)
    setScope('focus')
    setRollupPrompt('')
    setDigestIds([])
  }, [open, editing, prefill])

  // scope 切到 rollup 且骨架为空 → 预填默认模板（含 {sections}，用户可改）
  const switchScope = (next: Scope) => {
    setScope(next)
    if (next === 'rollup' && !rollupPrompt.trim()) {
      setRollupPrompt(t('schedule.form.rollupPromptTemplate'))
    }
  }

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

  // rollup 汇总范围候选：非 rollup 订阅全量（排除自身防自聚合，2.11 §12 不变量①）
  useEffect(() => {
    if (!open || step !== 'target' || scope !== 'rollup' || digestSources.length > 0) return
    window.electronAPI.invoke('schedules:list', { limit: 0 })
      .then((res) => {
        const rows = (res as { schedules?: ScheduleRow[] })?.schedules ?? []
        setDigestSources(rows
          .filter((r) => r.scope !== 'rollup' && r.id !== editing?.id)
          .map((r) => ({ id: r.id, name: r.name })))
      })
      .catch((err) => console.error('ScheduleFormModal: load digest sources failed:', err))
  }, [open, step, scope, digestSources.length, editing?.id])

  if (!open) return null

  const selectedTpl = templates.find((m) => m.id === tplId) ?? null
  // 占位符按名去重（0.5 §4.4）：同一变量在模板中出现多次只渲染一个输入框，
  // applyValues 替换时覆盖全部出现位置
  const phNames = selectedTpl ? uniquePlaceholderNames(selectedTpl.prompt) : []
  const templatePrompt = selectedTpl ? applyValues(selectedTpl.prompt, phValues) : ''
  const sourcePrompt = sourceTab === 'template' ? templatePrompt : freePrompt
  // persona 排序（非置顶区置顶，0.6 §3.2 T12）
  const sortedTemplates = sortTemplatesByPersona(templates, settings?.persona ?? '', 'intel')
  // 自由输入四分类直落 category（corporate/industry/region/general，0.6 §3.2 T12）
  const INTEL_CATEGORIES = ['corporate', 'industry', 'region', 'general'] as const

  const step1Ready = sourceTab === 'template'
    ? !!selectedTpl && !hasUnfilledPlaceholders(templatePrompt)
    : !!freePrompt.trim()
  const days = intervalType === 'weekly' ? weekDays : monthDays
  const step2Ready = time !== '' && (intervalType === 'daily' || days.length > 0)
  // rollup：骨架须含 {sections}（后端同校验 400 ROLLUP_PROMPT_NEEDS_SECTION）
  const step3Ready = scope === 'rollup'
    ? rollupPrompt.includes('{sections}')
    : targetType === 'none' || !!targetId

  const handleSave = async () => {
    if (!step3Ready || saving) return
    const intervalValue = serializeInterval(intervalType, days, time)
    const payload = {
      name: name.trim() || null,
      prompt: scope === 'rollup' ? rollupPrompt : (prompt || sourcePrompt),
      category,
      intervalType,
      intervalValue,
      scope,
      // 不勾选 = 全量（后端 NULL 语义）；空数组永不发出（后端视作无源）
      digestScheduleIds: scope === 'rollup' && digestIds.length > 0 ? digestIds : null,
      targetType: scope === 'rollup' ? null : (targetType === 'none' ? null : targetType),
      targetId: scope === 'rollup' ? null : (targetType === 'none' ? null : targetId),
      autoImport: scope === 'rollup' ? false : (targetType === 'none' ? false : autoImport),
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
                {sortedTemplates.map((m) => (
                  <button key={m.id}
                    className={`${s.tplItem} ${tplId === m.id ? s.tplActive : ''}`}
                    onClick={() => {
                      setTplId(m.id)
                      setPhValues({})
                      // 模板分类自动映射订阅产出分类（0.6 §3.2 T12）
                      setCategory(INTEL_CATEGORY_MAP[m.category])
                    }}>
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
              <>
                <textarea
                  className={s.promptArea}
                  aria-label={t('schedule.form.promptLabel')}
                  placeholder={t('schedule.form.promptPlaceholder')}
                  value={freePrompt}
                  onChange={(e) => setFreePrompt(e.target.value)}
                />
                <div className={s.catRow} role="radiogroup" aria-label={t('schedule.form.categoryLabel')}>
                  {INTEL_CATEGORIES.map((cat) => (
                    <button
                      key={cat}
                      type="button"
                      role="radio"
                      aria-checked={category === cat}
                      className={`${s.catChip} ${category === cat ? s.catChipActive : ''}`}
                      onClick={() => setCategory(cat)}
                    >
                      {t(`schedule.tab.${cat}`)}
                    </button>
                  ))}
                </div>
              </>
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
                <option value="corporate">{t('schedule.tab.corporate')}</option>
                <option value="industry">{t('schedule.tab.industry')}</option>
                <option value="region">{t('schedule.tab.region')}</option>
                <option value="general">{t('schedule.tab.general')}</option>
              </select>
            </label>
            <div className={s.radioCol} role="radiogroup" aria-label={t('schedule.form.scopeLabel')}>
              {SCOPES.map((sc) => (
                <label key={sc} className={s.radioRow}>
                  <input
                    type="radio"
                    name="scope"
                    checked={scope === sc}
                    onChange={() => switchScope(sc)}
                  />
                  {t(`schedule.scope.${sc}`)}
                </label>
              ))}
            </div>
            {scope === 'rollup' ? (
              <>
                <label className={s.phRow}>
                  <span>{t('schedule.form.rollupSkeletonLabel')}</span>
                  <textarea
                    className={s.promptArea}
                    aria-label={t('schedule.form.rollupSkeletonLabel')}
                    value={rollupPrompt}
                    onChange={(e) => setRollupPrompt(e.target.value)}
                  />
                </label>
                <p className={s.rollupHint}>{t('schedule.form.rollupSkeletonHint')}</p>
                <div className={s.fieldLabel}>{t('schedule.form.rollupScopeLabel')}</div>
                <div className={s.targetList}>
                  {digestSources.length === 0 ? (
                    <div className={s.empty}>{t('schedule.form.loadingTargets')}</div>
                  ) : digestSources.map((src) => (
                    <label key={src.id} className={s.dayChip}>
                      <input
                        type="checkbox"
                        aria-label={src.name}
                        checked={digestIds.includes(src.id)}
                        onChange={(e) => setDigestIds((prev) =>
                          e.target.checked ? [...prev, src.id] : prev.filter((x) => x !== src.id))}
                      />
                      {src.name}
                    </label>
                  ))}
                </div>
                <p className={s.rollupHint}>{t('schedule.form.rollupScopeHint')}</p>
              </>
            ) : (
              <>
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
              </>
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
