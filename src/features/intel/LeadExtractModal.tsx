/**
 * LeadExtractModal（0.6 §3.3 V2-E2）：阅读页「提取线索」LLM 候选勾选入池模态。
 * 数据流：并行 leads:extract（runId → 候选清单，同步 invoke 按钮 loading）
 * + leads:list limit=0（已在池判定）。不变量 §6.3-10：候选零自动入池——
 * 默认全选 + 用户点击「入池」确认，本期已入池条目禁选标「已在池」（零重复），
 * 跨期同名软提示不硬拦（同企业新动态可能是新线索）。
 * 批量入池 = 前端循环 leads:create（§5 五通道契约，无批量端点）；
 * 入池中途失败：已成功项按池判定剔除、模态保留可重试（不重跑 extract，
 * 避免重复 LLM 调用）。入池成功 toast 可跳线索池（app:navigate-intel tab=leads）。
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import type { LeadRow } from './LeadPool'
import s from './LeadExtractModal.module.css'

export interface LeadCandidate {
  name: string
  signal: string
  evidence: string
}

interface LeadExtractModalProps {
  runId: string
  /** 订阅名快照（来源可读兜底，期次删除不悬空） */
  scheduleName: string
  onClose: () => void
}

export function LeadExtractModal({ runId, scheduleName, onClose }: LeadExtractModalProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [candidates, setCandidates] = useState<LeadCandidate[]>([])
  const [poolRows, setPoolRows] = useState<LeadRow[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    try {
      const [ext, pool] = await Promise.all([
        window.electronAPI.invoke('leads:extract', runId) as Promise<{ candidates?: LeadCandidate[] }>,
        window.electronAPI.invoke('leads:list', { limit: 0 }) as Promise<{ leads?: LeadRow[] }>,
      ])
      const cands = ext.candidates ?? []
      setCandidates(cands)
      setPoolRows(pool.leads ?? [])
      // 默认全选（未入池项）；确认仍需用户点击「入池」——零自动入池
      setSelected(new Set(cands.map((c) => c.name)))
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => { void load() }, [load])

  const inPoolThisRun = (name: string) =>
    poolRows.some((r) => r.source_run_id === runId && r.name === name)
  const dupOtherRun = (name: string) =>
    poolRows.some((r) => r.source_run_id !== runId && r.name === name)

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const pickCount = candidates.filter((c) => selected.has(c.name) && !inPoolThisRun(c.name)).length

  const addSelected = async () => {
    if (adding || pickCount === 0) return
    const picks = candidates.filter((c) => selected.has(c.name) && !inPoolThisRun(c.name))
    setAdding(true)
    try {
      // 批量入池 = 前端循环 create（0.6 §5 契约）；note=信号摘要
      for (const c of picks) {
        await window.electronAPI.invoke('leads:create', {
          name: c.name, sourceType: 'run', sourceRunId: runId,
          sourceScheduleName: scheduleName, note: c.signal,
        })
      }
      showToast(t('intel.lead.extracted', { count: picks.length }), 'success', {
        action: {
          label: t('intel.lead.goLeads'),
          onClick: () => window.dispatchEvent(new CustomEvent('app:navigate-intel', {
            detail: { tab: 'leads' },
          })),
        },
      })
      onClose()
    } catch {
      showToast(t('intel.lead.opFailed'), 'error')
      // 已成功项从选中集剔除（重拉池判定，不重跑 extract——避免重复 LLM 调用），
      // 模态保留供重试剩余
      try {
        const pool = await window.electronAPI.invoke('leads:list', { limit: 0 }) as { leads?: LeadRow[] }
        const rows = pool.leads ?? []
        setPoolRows(rows)
        setSelected(new Set(
          candidates
            .filter((c) => selected.has(c.name) && !rows.some((r) => r.source_run_id === runId && r.name === c.name))
            .map((c) => c.name),
        ))
      } catch {
        /* 判定失败保持现状，用户可手动取消勾选已入池项 */
      }
    } finally {
      setAdding(false)
    }
  }

  return (
    // 入池进行中三处关闭入口均守卫（CodeReview Minor 2026-10-24，同 LeadPool
    // 立项模态 converting 守卫模式）：防误关后批量 create 在后台继续、用户失去上下文
    <div className={s.backdrop} onClick={() => { if (!adding) onClose() }}>
      <div className={s.dialog} role="dialog" aria-modal="true" data-testid="lead-extract-modal" onClick={(e) => e.stopPropagation()}>
        <div className={s.head}>
          <h3 className={s.title}>{t('intel.lead.extractTitle')}</h3>
          <button type="button" className={s.closeBtn} data-testid="lead-extract-close" aria-label={t('common.close')} onClick={() => { if (!adding) onClose() }}>
            <X size={14} />
          </button>
        </div>

        {loading ? (
          <p className={s.hint}>{t('intel.lead.extracting')}</p>
        ) : failed ? (
          <div className={s.errorWrap}>
            <p className={s.hint}>{t('intel.lead.extractFailed')}</p>
            <button type="button" className={s.secondaryBtn} data-testid="lead-extract-retry" onClick={() => void load()}>
              {t('intel.lead.retry')}
            </button>
          </div>
        ) : candidates.length === 0 ? (
          <p className={s.hint}>{t('intel.lead.noCandidates')}</p>
        ) : (
          <div className={s.candidateList}>
            {candidates.map((c) => {
              const pooled = inPoolThisRun(c.name)
              const dup = dupOtherRun(c.name)
              return (
                <label key={c.name} className={`${s.candidate} ${pooled ? s.candidatePooled : ''}`}>
                  <input
                    type="checkbox"
                    data-testid="lead-candidate-check"
                    checked={!pooled && selected.has(c.name)}
                    disabled={pooled || adding}
                    onChange={() => toggle(c.name)}
                  />
                  <span className={s.candidateBody}>
                    <span className={s.candidateName}>{c.name}</span>
                    <span className={s.candidateSignal}>{c.signal}</span>
                    {c.evidence && <span className={s.candidateEvidence}>{c.evidence}</span>}
                    {pooled && <span className={s.poolTag}>{t('intel.lead.inPool')}</span>}
                    {!pooled && dup && <span className={s.dupTag}>{t('intel.lead.dupSoft')}</span>}
                  </span>
                </label>
              )
            })}
          </div>
        )}

        <div className={s.actions}>
          <button type="button" className={s.secondaryBtn} onClick={() => { if (!adding) onClose() }}>
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className={s.primaryBtn}
            data-testid="lead-extract-confirm"
            disabled={loading || failed || pickCount === 0 || adding}
            onClick={() => void addSelected()}
          >
            {t('intel.lead.confirmAddN', { count: pickCount })}
          </button>
        </div>
      </div>
    </div>
  )
}
