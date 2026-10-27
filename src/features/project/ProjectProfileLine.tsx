/**
 * ProjectProfileLine — 详情头部产业画像行（2.1 §4.5/§6.2，2026-10-25）。
 *
 * 数据源：project.industry_profile（JSON 数组）+ profile_source；
 * 提取中态由父组件经 extracting prop 下发（心跳 useActivity('profile_extract')
 * 派生，父组件负责终态边沿刷新，零新增轮询，0.4 §3.1）。
 *
 * 交互（免培训 §2.10）：
 * - 默认单行截断（2026-10-26 用户裁决）：条目 >3 时 chips 限一行 + 「展开/收起」
 *   toggle，头部信息区不再被多行画像撑高；
 * - 每条「链条·细分」标签带「盯产业」钮（dispatch app:open-schedule-wizard，
 *   App 级新建订阅弹层预填产业舆情模板）与删除钮；
 * - 行尾「+」行内表单新增条目；「重新提取」钮（knowledge:extractProfile，
 *   异步受理，在途 409 经返回值 code 透传）；
 * - 任何人工编辑置 profile_source='manual'（编译不自动覆盖，2.1 §4.5 人工优先）；
 * - NULL 态提示「编译 Wiki 后自动提取」、'[]' 态提示「暂未识别到产业链」
 *   （两态文案不同，与后端语义区分对齐）。
 */
import { useMemo, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Factory, Radio, X, Plus, RefreshCw, Loader2, Check, ChevronDown } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import type { Project } from '../knowledge/types'
import s from './ProjectProfileLine.module.css'

interface ProfileEntry { chain: string; segment: string }

interface ProjectProfileLineProps {
  project: Project
  /** 画像提取进行中（父组件由心跳 useActivity('profile_extract') 派生） */
  extracting: boolean
  onProjectDataChanged?: () => void
}

function parseEntries(raw: string | null | undefined): ProfileEntry[] {
  if (!raw) return []
  try {
    const data = JSON.parse(raw)
    if (!Array.isArray(data)) return []
    return data
      .filter((x): x is { chain: string; segment?: string } =>
        !!x && typeof x === 'object'
        && typeof (x as Record<string, unknown>).chain === 'string'
        && ((x as Record<string, unknown>).chain as string).trim() !== '')
      .map((x) => ({ chain: x.chain.trim(), segment: (x.segment ?? '').trim() }))
  } catch {
    return []
  }
}

export function ProjectProfileLine({ project, extracting, onProjectDataChanged }: ProjectProfileLineProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const entries = useMemo(() => parseEntries(project.industry_profile), [project.industry_profile])
  const [adding, setAdding] = useState(false)
  const [newChain, setNewChain] = useState('')
  const [newSegment, setNewSegment] = useState('')
  const [saving, setSaving] = useState(false)
  // 单行折叠（2026-10-26 用户裁决）：>3 条默认收为一行，展开看全量
  const [expanded, setExpanded] = useState(false)
  const collapsible = entries.length > 3
  // NULL = 未提取（编译后自动提取）；'[]' = 已提取未识别（解析失败兜底为空数组展示同款提示）
  const neverExtracted = project.industry_profile == null

  // 人工编辑（增/删）→ PATCH 置 manual：编译不再自动覆盖，「重新提取」可恢复；
  // 返回是否成功（失败保留现状，新增表单不收起、输入不清空，可直接重试）
  const persist = useCallback(async (next: ProfileEntry[]): Promise<boolean> => {
    setSaving(true)
    try {
      await window.electronAPI.invoke('knowledge:updateProject', {
        projectId: project.id,
        industry_profile: JSON.stringify(next),
        profile_source: 'manual',
      })
      showToast(t('project.profile.manualSaved'), 'success')
      onProjectDataChanged?.()
      return true
    } catch (err) {
      console.error('Failed to update industry profile:', err)
      showToast(t('project.profile.editFailed'), 'error')
      return false
    } finally {
      setSaving(false)
    }
  }, [project.id, onProjectDataChanged, showToast, t])

  const removeEntry = useCallback((idx: number) => {
    void persist(entries.filter((_, i) => i !== idx))
  }, [entries, persist])

  const submitAdd = useCallback(() => {
    const chain = newChain.trim()
    if (!chain || saving) return
    void persist([...entries, { chain, segment: newSegment.trim() }]).then((ok) => {
      if (!ok) return                        // 保存失败：保留输入与表单（review s-1）
      setNewChain('')
      setNewSegment('')
      setAdding(false)
    })
  }, [newChain, newSegment, entries, saving, persist])

  // 重新提取（异步受理）：进行中态经心跳浮现，完成后父组件边沿刷新；
  // 409 在途经 structuredPythonResponse 返回值 code 透传（不抛异常）
  const reExtract = useCallback(async () => {
    if (extracting || saving) return
    try {
      const res: any = await window.electronAPI.invoke('knowledge:extractProfile', project.id)
      if (!res?.ok) {
        showToast(
          res?.code === 'PROFILE_EXTRACT_RUNNING'
            ? t('project.profile.extractRunning')
            : t('project.profile.extractFailed'),
          res?.code === 'PROFILE_EXTRACT_RUNNING' ? 'info' : 'error',
        )
      }
    } catch (err) {
      console.error('Failed to extract profile:', err)
      showToast(t('project.profile.extractFailed'), 'error')
    }
  }, [project.id, extracting, saving, showToast, t])

  // 盯产业（2.11 §8.1「从项目画像」渠道）：App 级弹层链组装 tplId 与占位符名
  // （i18n prompt 键提取，语言无关）；这里只带订阅名与产业值（细分优先，无细分用链条名）
  const watch = useCallback((en: ProfileEntry) => {
    window.dispatchEvent(new CustomEvent('app:open-schedule-wizard', {
      detail: { name: project.name, phValue: en.segment || en.chain },
    }))
  }, [project.name])

  return (
    <div className={s.profileLine}>
      <span className={s.profileLabel}>
        <Factory size={13} />
        {t('project.profile.label')}
      </span>
      {extracting ? (
        <span className={s.profileExtracting}>
          <Loader2 size={12} className={s.spin} />
          {t('project.profile.extracting')}
        </span>
      ) : entries.length > 0 ? (
        <span className={`${s.profileChips} ${collapsible && !expanded ? s.profileChipsClamped : ''}`}>
          {entries.map((en, i) => (
            <span key={`${en.chain}-${i}`} className={s.profileChip}>
              <span
                className={s.profileChipText}
                title={en.segment ? `${en.chain} · ${en.segment}` : en.chain}
              >
                {en.chain}
                {en.segment && <span className={s.profileChipSegment}>·{en.segment}</span>}
              </span>
              <button
                className={s.profileWatch}
                title={t('project.profile.watchTitle', { industry: en.segment || en.chain })}
                onClick={() => watch(en)}
              >
                <Radio size={11} />
                {t('project.profile.watch')}
              </button>
              <button
                className={s.profileRemove}
                title={t('project.profile.removeTitle')}
                disabled={saving}
                onClick={() => removeEntry(i)}
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </span>
      ) : (
        <span className={s.profileEmpty}>
          {neverExtracted ? t('project.profile.emptyHint') : t('project.profile.noneFound')}
        </span>
      )}
      {collapsible && (
        <button className={s.profileToggle} onClick={() => setExpanded(!expanded)}>
          <ChevronDown size={12} className={expanded ? s.profileToggleUp : undefined} />
          {expanded
            ? t('project.profile.collapse')
            : t('project.profile.expand', { count: entries.length })}
        </button>
      )}
      {adding ? (
        <span className={s.profileAddForm}>
          <input
            className={s.profileAddInput}
            placeholder={t('project.profile.chainPh')}
            value={newChain}
            onChange={(e) => setNewChain(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitAdd()
              if (e.key === 'Escape') setAdding(false)
            }}
            autoFocus
          />
          <input
            className={`${s.profileAddInput} ${s.profileAddInputShort}`}
            placeholder={t('project.profile.segmentPh')}
            value={newSegment}
            onChange={(e) => setNewSegment(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitAdd()
              if (e.key === 'Escape') setAdding(false)
            }}
          />
          <button
            className={s.profileAddConfirm}
            title={t('project.profile.confirmAdd')}
            disabled={!newChain.trim() || saving}
            onClick={submitAdd}
          >
            <Check size={12} />
          </button>
          <button
            className={s.profileAddCancel}
            title={t('knowledge.cancel')}
            onClick={() => setAdding(false)}
          >
            <X size={12} />
          </button>
        </span>
      ) : (
        <button
          className={s.profileAdd}
          title={t('project.profile.addTitle')}
          disabled={saving}
          onClick={() => setAdding(true)}
        >
          <Plus size={12} />
          {t('project.profile.add')}
        </button>
      )}
      <button
        className={s.profileReExtract}
        title={t('project.profile.reExtractTitle')}
        disabled={extracting || saving}
        onClick={() => void reExtract()}
      >
        <RefreshCw size={12} />
        {t('project.profile.reExtract')}
      </button>
    </div>
  )
}
