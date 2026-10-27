/** src/features/project/tabs/CompilePanel.tsx — Wiki compile detail panel
 *
 * 编译明细内嵌面板（2.6 §10）：置于 WikiTab 状态条下方，可折叠、不遮内容、
 * 不自动弹出（展开经状态条「查看详情」或触发编译；头部点击收起）。逐文件
 * 摘要审阅（2.6 F2）；停止入口在状态条（与暂停机制解耦，0.4 §6.7 修订）。 */
import { useTranslation } from 'react-i18next'
import { FileText, CheckCircle, AlertCircle, Loader2, XCircle, SkipForward, ChevronUp, Sparkles } from 'lucide-react'
import { translateStoredError } from '../../../i18n/errorCodes'
import s from './WikiTab.module.css'

interface FileSummary {
  file_id: string
  filename: string
  pages_created?: number
  pages_updated?: number
  pages_failed?: number
  reviews_count?: number
  pages_touched?: string[]
  skipped?: boolean
  error?: string
}

interface CompilePanelProps {
  task: {
    id: string
    status: string
    pages_total: number
    pages_completed: number
    pages_failed: number
    error_message: string | null
    /** 编译终态自动治理链汇总（JSON 文本，review m-2 2026-10-27 接入消费） */
    governance_summary?: string | null
  } | null
  ingestSummaries: FileSummary[]
  onCollapse: () => void
}

export function CompilePanel({
  task,
  ingestSummaries,
  onCollapse,
}: CompilePanelProps) {
  const { t } = useTranslation()
  if (!task) return null

  const isStopped = task.status === 'stopped'
  const isCompleted = task.status === 'completed'
  const isProcessing = ['queued', 'ingesting', 'storing'].includes(task.status)

  // Deduplicate summaries by file_id (keep last entry for re-compiles)
  const dedupedSummaries = (() => {
    const seen = new Map<string, number>()
    ingestSummaries.forEach((s, i) => {
      if (s.file_id) seen.set(s.file_id, i)
    })
    return ingestSummaries.filter((s, i) => !s.file_id || seen.get(s.file_id) === i)
  })()

  // Count results
  const successCount = dedupedSummaries.filter(s => !s.skipped && !s.error).length
  const errorCount = dedupedSummaries.filter(s => !!s.error).length
  const skippedCount = dedupedSummaries.filter(s => s.skipped).length

  // 治理汇总（review m-2 2026-10-27）：后台自动治理链在本轮编译后的动作摘要，
  // 落库于 task 行 governance_summary（JSON）——原先只落不显，docstring 谎称
  // 「toast 数据源」；损坏/旧格式静默不渲染，不影响面板
  const governance = (() => {
    if (!isCompleted || !task.governance_summary) return null
    try {
      const parsed = JSON.parse(task.governance_summary) as {
        swept?: number; link_fixed?: number; dedup_groups?: number
      }
      return {
        swept: parsed.swept ?? 0,
        linkFixed: parsed.link_fixed ?? 0,
        dedupGroups: parsed.dedup_groups ?? 0,
      }
    } catch {
      return null
    }
  })()

  return (
    <div className={s.compilePanel}>
      <div className={s.compilePanelHeader} onClick={onCollapse} title={t('project.wiki.compileCollapse')}>
        {isProcessing && <Loader2 size={16} className={s.spin} />}
        {isCompleted && <CheckCircle size={16} className={s.compilePanelCompleteIcon} />}
        {isStopped && <XCircle size={16} className={s.compilePanelStoppedIcon} />}
        <span>{t('project.wiki.compileTitle')}</span>
        <button
          className={s.compilePanelCollapseIcon}
          onClick={(e) => { e.stopPropagation(); onCollapse() }}
          title={t('project.wiki.compileCollapse')}
        >
          <ChevronUp size={14} />
        </button>
      </div>

      {/* Progress bar */}
      <div className={s.compilePanelProgress}>
        <div
          className={`${s.compilePanelProgressBar} ${task.pages_total === 0 && isProcessing ? s.compilePanelProgressBarIndeterminate : ''}`}
          style={{
            width: task.pages_total > 0
              ? `${Math.round((task.pages_completed / task.pages_total) * 100)}%`
              : undefined,
          }}
        />
      </div>
      <div className={s.compilePanelStats}>
        {task.pages_total > 0
          ? t('project.wiki.compileProgress', { done: task.pages_completed, total: task.pages_total })
          : isProcessing
            ? t('project.wiki.compileProgressCalculating', { defaultValue: '正在计算任务规模...' })
            : t('project.wiki.compileProgress', { done: 0, total: 0 })}
        {errorCount > 0 && (
          <span className={s.compilePanelErrorCount}>
            {' · '}
            <AlertCircle size={12} /> {errorCount} {t('project.wiki.compileErrors', { defaultValue: '个失败' })}
          </span>
        )}
        {skippedCount > 0 && (
          <span className={s.compilePanelSkippedCount}>
            {' · '}
            <SkipForward size={12} /> {skippedCount} {t('project.wiki.compileSkippedCount', { defaultValue: '个跳过' })}
          </span>
        )}
      </div>

      {/* All file results list */}
      {dedupedSummaries.length > 0 && (
        <div className={s.compilePanelFileList}>
          {dedupedSummaries.map((summary, idx) => (
            <div key={summary.file_id || idx} className={s.compilePanelFileItem}>
              <div className={s.compilePanelFileHeader}>
                <FileText size={13} />
                <span className={s.compilePanelFileName}>{summary.filename}</span>
                {summary.skipped ? (
                  <span className={s.compilePanelSkipped}>
                    <SkipForward size={11} /> {t('project.wiki.compileSkipped')}
                  </span>
                ) : summary.error ? (
                  <span className={s.compilePanelError}>
                    <AlertCircle size={11} /> {t('project.wiki.compileError')}
                  </span>
                ) : (
                  <span className={s.compilePanelDone}>
                    <CheckCircle size={11} /> {t('project.wiki.compileFileDone')}
                  </span>
                )}
              </div>
              {/* Error detail（D1：落库协议串还原为当前语言） */}
              {summary.error && (
                <div className={s.compilePanelErrorDetail}>{translateStoredError(summary.error, t)}</div>
              )}
              {/* Stats for successful files */}
              {!summary.skipped && !summary.error && (
                <div className={s.compilePanelFileStats}>
                  {(summary.pages_created ?? 0) > 0 && (
                    <span className={s.statCreated}>
                      +{summary.pages_created} {t('project.wiki.compileCreated')}
                    </span>
                  )}
                  {(summary.pages_updated ?? 0) > 0 && (
                    <span className={s.statUpdated}>
                      ~{summary.pages_updated} {t('project.wiki.compileUpdated')}
                    </span>
                  )}
                  {(summary.reviews_count ?? 0) > 0 && (
                    <span className={s.statReviews}>
                      {summary.reviews_count} {t('project.wiki.compileReviews')}
                    </span>
                  )}
                  {summary.pages_touched && summary.pages_touched.length > 0 && (
                    <div className={s.compilePanelPages}>
                      {summary.pages_touched.slice(0, 5).map((key: string) => (
                        <span key={key} className={s.pageTag}>{key}</span>
                      ))}
                      {summary.pages_touched.length > 5 && (
                        <span className={s.pageTagMore}>+{summary.pages_touched.length - 5}</span>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Processing state — no file results yet */}
      {isProcessing && dedupedSummaries.length === 0 && (
        <div className={s.compilePanelProcessingHint}>
          <Loader2 size={14} className={s.spin} />
          <span>{t('project.wiki.compileProcessing', { defaultValue: '正在处理第一个文件...' })}</span>
        </div>
      )}

      {/* Completed state */}
      {isCompleted && (
        <div className={s.compilePanelComplete}>
          {errorCount > 0 ? (
            <>
              <AlertCircle size={16} className={s.compilePanelStoppedIcon} />
              <span>
                {t('project.wiki.compileDoneWithErrors', {
                  defaultValue: `汇编完成: ${successCount} 成功, ${errorCount} 失败`,
                  success: successCount,
                  errors: errorCount,
                })}
              </span>
            </>
          ) : (
            <>
              <CheckCircle size={16} className={s.compilePanelCompleteIcon} />
              <span>{t('project.wiki.compileAllDone')}</span>
            </>
          )}
        </div>
      )}

      {/* Stopped state */}
      {isStopped && (
        <div className={s.compilePanelComplete}>
          <XCircle size={16} className={s.compilePanelStoppedIcon} />
          <span>{t('project.wiki.compileIngestStopped')}</span>
        </div>
      )}

      {/* 治理汇总行（review m-2）：编译完成后自动治理（清扫/修死链/重复检测）的动作摘要 */}
      {governance && (
        <div className={s.compilePanelGovernance}>
          <Sparkles size={13} />
          <span>
            {governance.swept + governance.linkFixed + governance.dedupGroups > 0
              ? t('project.wiki.wikiGovernanceSummary', {
                  swept: governance.swept,
                  linkFixed: governance.linkFixed,
                  dedupGroups: governance.dedupGroups,
                  defaultValue: `自动整理：核销 ${governance.swept} 条过时问题、修复 ${governance.linkFixed} 条死链、检出 ${governance.dedupGroups} 组疑似重复`,
                })
              : t('project.wiki.wikiGovernanceClean', { defaultValue: '自动整理已完成，本轮无需处理' })}
          </span>
        </div>
      )}

      {/* Task-level error（D1：落库协议串还原为当前语言） */}
      {task.error_message && (
        <div className={s.compilePanelTaskError}>
          <AlertCircle size={13} />
          <span>{translateStoredError(task.error_message, t)}</span>
        </div>
      )}
    </div>
  )
}
