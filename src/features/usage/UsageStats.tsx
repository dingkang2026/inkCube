/** src/features/usage/UsageStats.tsx — 设置-用量统计 tab（2.10 §6.1/线框 7）。
 *  四区布局，全部数据来自 /usage/summary 一次请求，切筛选 = 带新参数重取：
 *  ① 筛选行（时间范围 × 项目）② 汇总卡×4 ③ 按天 SVG 堆叠柱（输入 accent/输出
 *  success）④ 来源/模型/项目三明细表（占比条按 total_tokens 归一；项目行点击下钻）。
 *  projectSignal 响应外部导航（项目徽章点击 / StatusBar 跳转清筛选）。 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { useUsageSummary, type UsageAggRow } from './useUsageSummary'
import { compactTokens, thousands, USAGE_SOURCE_LABEL_KEYS } from './format'
import s from './UsageStats.module.css'

const RANGES = ['today', '7d', '30d', 'all'] as const
type RangeKey = (typeof RANGES)[number]

const RANGE_LABEL_KEYS: Record<RangeKey, string> = {
  today: 'usage.rangeToday',
  '7d': 'usage.range7d',
  '30d': 'usage.range30d',
  all: 'usage.rangeAll',
}

/** 外部导航信号：SettingsPage 的 sectionSignal 原样透传（每次导航新引用） */
export interface UsageProjectSignal {
  projectId?: string | null
}

export function UsageStats({ projectSignal }: { projectSignal?: UsageProjectSignal | null }) {
  const { t } = useTranslation()
  const [rangeKey, setRangeKey] = useState<RangeKey>('7d')
  const [projectId, setProjectId] = useState<string | null>(projectSignal?.projectId ?? null)

  // 外部导航（徽章点击带项目 / StatusBar 跳转不带=清筛选）：signal 引用变化即覆盖
  useEffect(() => {
    setProjectId(projectSignal?.projectId ?? null)
  }, [projectSignal])

  const { data } = useUsageSummary(rangeKey, projectId ?? undefined)

  const maxTotal = Math.max(1, ...(data?.by_day ?? []).map((d) => d.total_tokens))

  const sharePct = (row: UsageAggRow): number => {
    const totalRange = data?.range.total_tokens ?? 0
    return totalRange > 0 ? Math.round((row.total_tokens / totalRange) * 100) : 0
  }

  const trendDays = data?.by_day ?? []
  const axisFirst = trendDays[0]?.grp ?? ''
  const axisMid = trendDays.length > 2 ? trendDays[Math.floor(trendDays.length / 2)].grp : ''
  const axisLast = trendDays.length > 1 ? trendDays[trendDays.length - 1].grp : ''

  // 加载中（data=null，首次拉取/Python 未就绪）与空态（确认无数据）严格区分：
  // 加载中渲染骨架（§6.3/线框 7），不闪「暂无用量数据」假空态（2026-10-16 UI review）
  const loading = !data
  const isEmpty = !loading && data.range.total_tokens === 0 && data.by_day.length === 0

  return (
    <div className={s.container}>
      {/* ① 筛选行 */}
      <div className={s.filterRow}>
        <div className={s.rangeGroup}>
          {RANGES.map((r) => (
            <button
              key={r}
              className={`${s.rangeBtn} ${rangeKey === r ? s.rangeBtnActive : ''}`}
              onClick={() => setRangeKey(r)}
            >
              {t(RANGE_LABEL_KEYS[r])}
            </button>
          ))}
        </div>
        <span className={s.projectFilter}>
          {t('usage.projectFilter')}:
          <select
            className={s.projectSelect}
            value={projectId ?? ''}
            onChange={(e) => setProjectId(e.target.value || null)}
          >
            <option value="">{t('usage.projectAll')}</option>
            {(data?.by_project ?? []).map((p) => (
              <option key={p.grp} value={p.grp}>
                {p.project_name ?? p.grp}
              </option>
            ))}
          </select>
          {projectId && (
            <button
              className={s.rangeBtn}
              onClick={() => setProjectId(null)}
              title={t('usage.clearFilter')}
            >
              <X size={12} />
            </button>
          )}
        </span>
      </div>

      {!loading && isEmpty ? (
        <div className={s.emptyState}>{t('usage.empty')}</div>
      ) : (
        <>
          {/* ② 汇总卡 ×4（加载中渲染骨架灰块） */}
          <div className={s.summaryCards}>
            <div className={s.card}>
              <div className={s.cardLabel}>{t('usage.sumInput')}</div>
              {loading ? <div className={s.skeleton} /> : <div className={s.cardValue}>{thousands(data.range.prompt_tokens)}</div>}
            </div>
            <div className={s.card}>
              <div className={s.cardLabel}>{t('usage.sumOutput')}</div>
              {loading ? <div className={s.skeleton} /> : <div className={s.cardValue}>{thousands(data.range.completion_tokens)}</div>}
            </div>
            <div className={s.card}>
              <div className={s.cardLabel}>{t('usage.sumCalls')}</div>
              {loading ? <div className={s.skeleton} /> : <div className={s.cardValue}>{thousands(data.range.calls)}</div>}
            </div>
            <div className={s.card}>
              <div className={s.cardLabel}>{t('usage.sumModels')}</div>
              {loading ? <div className={s.skeleton} /> : <div className={s.cardValue}>{data.total.models_count}</div>}
            </div>
          </div>

          {/* ③ 按天趋势（SVG 堆叠柱：输入 accent 在下、输出 success 在上） */}
          <div className={s.trendSection}>
            <span className={s.sectionTitle}>
              {t('usage.dailyTrend')}
              <span className={s.legend}>
                <span>
                  <span className={s.legendDot} style={{ background: 'var(--accent)' }} />
                  {t('usage.legendInput')}
                </span>
                <span>
                  <span className={s.legendDot} style={{ background: 'var(--success)' }} />
                  {t('usage.legendOutput')}
                </span>
              </span>
            </span>
            {loading ? (
              <div className={s.skeletonBlock} />
            ) : trendDays.length === 0 ? (
              <div className={s.trendEmpty}>{t('usage.empty')}</div>
            ) : (
              <>
                <svg
                  className={s.trendSvg}
                  viewBox={`0 0 ${trendDays.length * 10} 64`}
                  preserveAspectRatio="none"
                  role="img"
                >
                  {trendDays.map((d, i) => {
                    const hIn = (d.prompt_tokens / maxTotal) * 64
                    const hOut = (d.completion_tokens / maxTotal) * 64
                    return (
                      <g key={d.grp}>
                        <title>
                          {`${d.grp} · ${t('usage.titleInput')}: ${thousands(d.prompt_tokens)} · ${t('usage.titleOutput')}: ${thousands(d.completion_tokens)}`}
                        </title>
                        <rect x={i * 10 + 2} y={64 - hIn} width={6} height={hIn} fill="var(--accent)" />
                        <rect x={i * 10 + 2} y={64 - hIn - hOut} width={6} height={hOut} fill="var(--success)" />
                      </g>
                    )
                  })}
                </svg>
                <div className={s.trendAxis}>
                  <span>{axisFirst}</span>
                  <span>{axisMid}</span>
                  <span>{axisLast}</span>
                </div>
              </>
            )}
          </div>

          {/* ④ 三明细表 */}
          <div className={s.tables}>
            <div className={s.tableCard}>
              <div className={s.sectionTitle}>{t('usage.tblSource')}</div>
              <div className={s.tableBody}>
                {loading
                  ? Array.from({ length: 3 }, (_, i) => <div key={i} className={s.skeletonRow} />)
                  : (data?.by_source ?? []).map((row) => (
                  <div key={row.grp} className={s.tableRow}>
                    <div className={s.tableRowHead}>
                      <span className={s.rowName}>{t(USAGE_SOURCE_LABEL_KEYS[row.grp] ?? 'usage.srcOther')}</span>
                      <span className={s.rowNums}>
                        ↑{compactTokens(row.prompt_tokens)} ↓{compactTokens(row.completion_tokens)} · {row.calls}
                      </span>
                    </div>
                    <div className={s.shareBar}>
                      <div className={s.shareBarFill} style={{ width: `${sharePct(row)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className={s.tableCard}>
              <div className={s.sectionTitle}>{t('usage.tblModel')}</div>
              <div className={s.tableBody}>
                {loading
                  ? Array.from({ length: 3 }, (_, i) => <div key={i} className={s.skeletonRow} />)
                  : (data?.by_model ?? []).map((row) => (
                  <div key={row.grp} className={s.tableRow}>
                    <div className={s.tableRowHead}>
                      <span className={s.rowName} title={row.grp}>{row.grp}</span>
                      <span className={s.rowNums}>
                        ↑{compactTokens(row.prompt_tokens)} ↓{compactTokens(row.completion_tokens)} · {sharePct(row)}%
                      </span>
                    </div>
                    <div className={s.shareBar}>
                      <div className={s.shareBarFill} style={{ width: `${sharePct(row)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className={s.tableCard}>
              <div className={s.sectionTitle}>{t('usage.tblProject')}</div>
              <div className={s.tableBody}>
                {loading
                  ? Array.from({ length: 3 }, (_, i) => <div key={i} className={s.skeletonRow} />)
                  : (data?.by_project ?? []).map((row) => (
                  <div key={row.grp} className={s.tableRow}>
                    <div className={s.tableRowHead}>
                      <span
                        className={`${s.rowName} ${s.rowNameClickable} ${projectId === row.grp ? s.rowActive : ''}`}
                        title={projectId === row.grp ? undefined : t('usage.drillTip')}
                        onClick={() => setProjectId(row.grp)}
                      >
                        {row.project_name ?? row.grp}
                      </span>
                      <span className={s.rowNums}>
                        ↑{compactTokens(row.prompt_tokens)} ↓{compactTokens(row.completion_tokens)} · {sharePct(row)}%
                      </span>
                    </div>
                    <div className={s.shareBar}>
                      <div className={s.shareBarFill} style={{ width: `${sharePct(row)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
