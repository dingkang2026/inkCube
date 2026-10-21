/** src/features/usage/UsageRounds.tsx — TaskItem 轮次用量下钻（2.10 §5.2/§6.3）。
 *  meta 行触发器（UsageText button）+ accordion 文档流展开（列表滚动容器不裁剪，
 *  不做弹层）；展开时经 usage:taskDetail 取数、收起即弃（重取天然正确，§5.4）；
 *  失败保留 failed 态，再点重试；usage 累计缺失不渲染（历史任务无记录）。 */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { UsageSummary } from '../../types'
import { compactTokens, modelsList } from './format'
import { UsageText } from './UsageText'
import s from './UsageRounds.module.css'

/** 轮次行（GET /usage/task/{id} → db.get_usage_task_rounds 按 round_id 聚合） */
interface UsageRound {
  round_id: string
  started_at: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
  models?: string | null
}

const ROUNDS_PAGE_SIZE = 20

export function UsageRounds({ taskId, usage }: { taskId: string; usage?: UsageSummary | null }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [rounds, setRounds] = useState<UsageRound[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const seqRef = useRef(0)
  const roundsLimitRef = useRef(ROUNDS_PAGE_SIZE)
  if (!usage || !usage.calls) return null

  // 轮次分页（0.1dev-guide 统一约定）：懒加载面板手写游标（收起即弃语义不适配
  // usePagedList 挂载即拉），20/页「加载更多」按递增游标全量重拉
  const fetchRounds = (more: boolean) => {
    if (more && loadingMore) return
    const seq = ++seqRef.current
    if (more) {
      setLoadingMore(true)
      roundsLimitRef.current += ROUNDS_PAGE_SIZE
    }
    window.electronAPI
      .invoke('usage:taskDetail', { taskId, limit: roundsLimitRef.current })
      .then((res) => {
        if (seqRef.current !== seq) return
        const data = (res ?? {}) as { rounds?: UsageRound[]; has_more?: boolean }
        setRounds(Array.isArray(data.rounds) ? data.rounds : [])
        setHasMore(!!data.has_more)
        if (more) setLoadingMore(false)
      })
      .catch(() => {
        if (seqRef.current !== seq) return
        if (!more) setFailed(true)
        else setLoadingMore(false)
      })
  }

  const toggle = () => {
    if (open && !failed) {
      seqRef.current += 1 // 使在途请求失效（收起即弃，迟到的 resolve 不回写）
      setOpen(false)
      setRounds(null)
      setFailed(false)
      roundsLimitRef.current = ROUNDS_PAGE_SIZE // 下次展开从第一页开始
      return
    }
    // 折叠态展开，或失败态重试（2.10 §6.3：失败 → 再点重试）
    setOpen(true)
    setFailed(false)
    fetchRounds(false)
  }

  return (
    <>
      {' · '}
      <UsageText usage={usage} onClick={toggle} expanded={open} />
      {open && (
        <div className={s.panel} onClick={(e) => e.stopPropagation()}>
          {failed ? (
            <div className={`${s.note} ${s.noteError}`}>{t('usage.roundsError')}</div>
          ) : rounds === null ? (
            <div className={s.note}>{t('usage.loading')}</div>
          ) : rounds.length === 0 ? (
            <div className={s.note}>{t('usage.roundsEmpty')}</div>
          ) : (
            <>
              {rounds.map((r, i) => {
                const models = modelsList(r.models ?? undefined)
                return (
                  <div className={s.row} key={r.round_id || i}>
                    <span className={s.idx}>#{i + 1}</span>
                    <span className={s.tokens}>
                      ↑{compactTokens(r.prompt_tokens)} ↓{compactTokens(r.completion_tokens)}
                    </span>
                    <span className={s.calls}>{t('usage.titleCalls', { count: r.calls })}</span>
                    {models.length > 0 && (
                      <span className={s.models} title={models.join(', ')}>
                        {models.join(', ')}
                      </span>
                    )}
                  </div>
                )
              })}
              <div className={`${s.row} ${s.totalRow}`}>
                <span className={s.idx}>{t('usage.roundsTotal')}</span>
                <span className={s.tokens}>
                  ↑{compactTokens(usage.prompt_tokens)} ↓{compactTokens(usage.completion_tokens)}
                </span>
                <span className={s.calls}>{t('usage.titleCalls', { count: usage.calls })}</span>
              </div>
              {hasMore && (
                <button
                  className={s.loadMoreBtn}
                  onClick={() => fetchRounds(true)}
                  disabled={loadingMore}
                >
                  {loadingMore ? t('common.loading') : t('common.loadMore')}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </>
  )
}
