/** src/features/schedule/ScheduleFormModal.tsx — 新建/编辑订阅弹层向导
 *  （2.11 §9.2 规则 9，E3 T7；§12 收敛 2026-10-25 两类订阅）。类型由入口
 *  给定（createKind），不可中途切换：
 *  - 情报订阅（intel）：①来源（从模板占位符表单化 / 自由输入）→ ②周期
 *    （daily 时刻 / weekly 星期多选+时刻 / monthly 日期多选+时刻）→
 *    ③名称+分类+去向+保存；
 *  - 组合周报（rollup）：无来源步（情报模板对汇总无意义）→ ②周期 →
 *    ③名称+汇总骨架（预填默认模板含 {sections}）+汇总范围+「不沉淀」说明。
 *  editing（编辑态）→ 跳过①，统一回填，类型随 editing.scope。 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { X, Newspaper, Activity, TrendingUp, Target, Landmark, MapPin, Radio, Sparkles } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'
import { useTaskTemplates } from '../task/useTaskTemplates'
import {
  hasUnfilledPlaceholders, uniquePlaceholderNames, applyValues,
  INTEL_TEMPLATES, INTEL_CATEGORY_MAP, sortTemplatesByPersona, BUILTIN_PREFIX,
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
  /** 编辑态：跳过第①步，prompt+配置回填（类型随 editing.scope） */
  editing?: ScheduleRow | null
  /** 新建类型（2.11 §12 收敛）：intel 情报订阅（默认）/ rollup 组合周报
   *  （跳过模板步，第③步为骨架+汇总范围）；编辑态忽略 */
  createKind?: ScheduleKind
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
/** 订阅类型（2.11 §12 收敛）：intel 情报订阅（scope=NULL）/ rollup 组合周报；
 *  由创建入口给定，编辑态随 editing.scope，不可切换 */
export type ScheduleKind = 'intel' | 'rollup'

/** 内置订阅模板 icon（id 字符串）→ lucide 组件；自定义模板统一 Sparkles（同 TaskTemplatePanel 模式） */
const INTEL_ICONS: Record<string, LucideIcon> = {
  Newspaper, Activity, Radio, TrendingUp, Target, Landmark, MapPin,
}

export function ScheduleFormModal({ open, editing, createKind, prefill, onClose, onSaved }: ScheduleFormModalProps) {
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

  // 订阅类型（2.11 §12 收敛）：由入口给定不可切换；rollup 第③步为骨架+汇总范围
  const [kind, setKind] = useState<ScheduleKind>('intel')
  const [rollupPrompt, setRollupPrompt] = useState('')
  const [digestIds, setDigestIds] = useState<string[]>([])
  const [digestSources, setDigestSources] = useState<{ id: string; name: string }[]>([])

  // 打开初始化：编辑态回填 / 新建按类型清空（rollup 跳过模板步）
  useEffect(() => {
    if (!open) return
    // rollup 候选缓存打开即重置（V2 全量 review m-1 2026-10-24）：组件关闭不卸载、
    // 状态常驻，不重置则候选跨开合固化（会话新建订阅不入候选/已删订阅残留死选项）
    setDigestSources([])
    if (editing) {
      const parsed = parseInterval(editing.interval_type, editing.interval_value)
      setKind(editing.scope === 'rollup' ? 'rollup' : 'intel')
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
      setRollupPrompt(editing.scope === 'rollup' ? editing.prompt : '')
      setDigestIds(editing.digest_schedule_ids ?? [])
      setStep('period')
      return
    }
    const k: ScheduleKind = createKind ?? 'intel'
    setKind(k)
    setStep(k === 'rollup' ? 'period' : 'source')
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
    // 新建 rollup 预填默认骨架（i18n rollupPromptTemplate，含 {sections}）：
    // 跳过模板步的补偿——用户不试错即知产出形态，可改可清
    setRollupPrompt(k === 'rollup' ? t('schedule.form.rollupPromptTemplate') : '')
    setDigestIds([])
  }, [open, editing, prefill, createKind])

  const loadTargets = useCallback((targetKind: 'project' | 'baselib') => {
    const channel = targetKind === 'project' ? 'knowledge:listProjects' : 'baseLib:list'
    // 选择器形态：limit=0 全量（目标下拉不得截断）
    window.electronAPI.invoke(channel, { limit: 0 })
      .then((res) => {
        const rows = targetKind === 'project'
          ? (res as { projects?: { id: string; name: string }[] })?.projects ?? []
          : (res as { base_libs?: { id: string; name: string }[] })?.base_libs ?? []
        setTargets(rows)
      })
      .catch((err) => {
        console.error('ScheduleFormModal: load targets failed:', err)
        // 失败反馈（页面遍历 review N4-1 2026-10-24）：不再永久「加载中」占位
        showToast(t('common.listLoadFailed'), 'error')
      })
  }, [])

  useEffect(() => {
    if (step === 'target' && targetType !== 'none' && targets.length === 0) {
      loadTargets(targetType)
    }
  }, [step, targetType, targets.length, loadTargets])

  // rollup 汇总范围候选：非 rollup 订阅全量（排除自身防自聚合，2.11 §12 不变量①）
  useEffect(() => {
    if (!open || step !== 'target' || kind !== 'rollup' || digestSources.length > 0) return
    window.electronAPI.invoke('schedules:list', { limit: 0 })
      .then((res) => {
        const rows = (res as { schedules?: ScheduleRow[] })?.schedules ?? []
        setDigestSources(rows
          .filter((r) => r.scope !== 'rollup' && r.id !== editing?.id)
          .map((r) => ({ id: r.id, name: r.name })))
      })
      .catch((err) => {
        console.error('ScheduleFormModal: load digest sources failed:', err)
        // 失败反馈（N4-1，同 loadTargets）：候选拉取失败不再静默空列表
        showToast(t('common.listLoadFailed'), 'error')
      })
  }, [open, step, kind, digestSources.length, editing?.id])

  if (!open) return null

  const selectedTpl = templates.find((m) => m.id === tplId) ?? null
  // 「你将获得」产出结构要点（i18n out 以 \n 分行；仅内置模板配置，自定义模板无此键自然跳过）
  const tplOutLines = selectedTpl?.builtin
    ? t(`task.tpl.${selectedTpl.id.slice(BUILTIN_PREFIX.length)}.out`, { defaultValue: '' })
        .split('\n').filter(Boolean)
    : []
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
  const step3Ready = kind === 'rollup'
    ? rollupPrompt.includes('{sections}')
    : targetType === 'none' || !!targetId

  const handleSave = async () => {
    if (!step3Ready || saving) return
    const isRollup = kind === 'rollup'
    const intervalValue = serializeInterval(intervalType, days, time)
    const payload = {
      name: name.trim() || null,
      prompt: isRollup ? rollupPrompt : (prompt || sourcePrompt),
      // 组合周报是跨订阅汇总，产出入通用页签（后端默认同值，显式发送自明）
      category: isRollup ? 'general' : category,
      intervalType,
      intervalValue,
      // 类型标记（编辑态 PATCH 白名单忽略，类型不可改）
      scope: isRollup ? 'rollup' : null,
      // 不勾选 = 全量（后端 NULL 语义）；空数组永不发出（后端视作无源）
      digestScheduleIds: isRollup && digestIds.length > 0 ? digestIds : null,
      targetType: isRollup ? null : (targetType === 'none' ? null : targetType),
      targetId: isRollup ? null : (targetType === 'none' ? null : targetId),
      autoImport: isRollup ? false : (targetType === 'none' ? false : autoImport),
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
            {t(editing
              ? 'schedule.form.editTitle'
              : kind === 'rollup' ? 'schedule.form.newRollupTitle' : 'schedule.form.newTitle')}
          </h3>
          <button className={s.iconBtn} aria-label={t('common.close')} onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        {step === 'source' && kind === 'intel' && (
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
                {sortedTemplates.map((m) => {
                  const Icon = m.builtin
                    ? INTEL_ICONS[m.id.slice(BUILTIN_PREFIX.length)]
                    : Sparkles
                  return (
                    <button key={m.id}
                      className={`${s.tplItem} ${tplId === m.id ? s.tplActive : ''}`}
                      onClick={() => {
                        setTplId(m.id)
                        setPhValues({})
                        // 模板分类自动映射订阅产出分类（0.6 §3.2 T12）
                        setCategory(INTEL_CATEGORY_MAP[m.category])
                      }}>
                      <span className={s.tplIcon}>{Icon ? <Icon size={16} /> : null}</span>
                      <span className={s.tplTexts}>
                        <span className={s.tplTitle}>{m.title}</span>
                        <span className={s.tplDesc}>{m.desc}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
            {/* 「你将获得」产出结构预览（2026-10-24）：选中内置模板即展示每期产出要点 */}
            {sourceTab === 'template' && selectedTpl && tplOutLines.length > 0 && (
              <div className={s.outPreview}>
                <div className={s.outPreviewTitle}>{t('schedule.form.outPreviewTitle')}</div>
                <ul className={s.outPreviewList}>
                  {tplOutLines.map((line, i) => <li key={i}>{line}</li>)}
                </ul>
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
              {editing || kind === 'rollup' ? <span /> : (
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
            <div className={s.fieldLabel}>
              {t(kind === 'rollup'
                ? 'schedule.form.stepTargetRollup' : 'schedule.form.stepTarget')}
            </div>
            <label className={s.phRow}>
              <span>{t('schedule.form.nameLabel')}</span>
              <input
                aria-label={t('schedule.form.nameLabel')}
                value={name}
                placeholder={t('schedule.form.namePlaceholder')}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            {kind === 'rollup' ? (
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
                {/* 去向显式化（免培训 §2.10）：rollup 不沉淀，说明块替代去向配置 */}
                <div className={s.fieldLabel}>{t('schedule.form.targetSectionLabel')}</div>
                <p className={s.rollupHint}>{t('schedule.form.rollupNoTarget')}</p>
              </>
            ) : (
              <>
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
                <div className={s.fieldLabel}>{t('schedule.form.targetSectionLabel')}</div>
                <p className={s.rollupHint}>{t('schedule.form.targetSectionHint')}</p>
                <div className={s.radioCol} role="radiogroup" aria-label={t('schedule.form.targetSectionLabel')}>
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
